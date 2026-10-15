# -*- coding: utf-8 -*-
"""Pexels 健壮性单测 (2026-09-04, 慢滴流挂死 + LLM 漏词双修):

① download wall-clock 总上限 — requests (10,300) 只卡字节间隔, 慢滴流可拖数小时
   (job 33b2b922 slot 02 实证); 超限抛 PexelsResolveError + 半成品清理。
② _backfill_pexels_keywords — LLM 偶发漏 keywords (8/23 槽), 兜底 = 主体继承
   (同 plan 多数 keywords[0]) + 概念词典映射 (visual_director_v2.txt 表内词)。

不联网 — http_session / validate_video / time.monotonic 全 monkeypatch。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import requests
from sqlalchemy.orm import Session

from app.config import load_config, set_config
from app.database import db_session, init_db
from app.models import Article, DirectorJob, Script
from app.services.pexels_service.types import PexelsResolveError


# ── 测试环境: 独立 SQLite (trace 落库断言用) ──

@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test_pexels_robust.db"
    cfg = load_config()
    cfg.__dict__["defaults"] = cfg.defaults.__class__(
        **{
            **{k: getattr(cfg.defaults, k) for k in cfg.defaults.__dataclass_fields__},
            "materials_dir": str(tmp_path / "materials"),
        }
    )
    set_config(cfg)
    init_db(f"sqlite:///{db_path}")
    yield


# ── ① download wall-clock 总上限 ──

class _FakeResp:
    """requests 流式响应桩: 上下文管理器 + 无限慢滴流 chunk."""

    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        yield from self._chunks


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    def get(self, url, **kw):
        return self._resp


class TestDownloadTotalCap:
    def test_slow_drip_capped_and_partial_removed(self, tmp_path, monkeypatch):
        from app.services.pexels_service import _http as hw

        monkeypatch.setattr(hw, "_download_total_timeout_sec", lambda: 180)
        # 假时钟: 每次调用 +100s → started=100, 第 2 个 chunk 后 elapsed=200 > 180
        t = {"v": 0.0}

        def fake_monotonic():
            t["v"] += 100.0
            return t["v"]

        monkeypatch.setattr(hw.time, "monotonic", fake_monotonic)
        # 慢滴流: 5 个 chunk 但时钟先行烧穿 cap
        monkeypatch.setattr(hw, "http_session", lambda svc: _FakeSession(_FakeResp([b"x" * 8192] * 5)))

        dest = tmp_path / "materials" / "pexels_7.mp4"
        with pytest.raises(PexelsResolveError, match="wall-clock"):
            hw.download(svc=None, url="https://v.pexels.com/files/7.mp4",
                        pexels_id=7, materials_dir=str(tmp_path / "materials"))
        assert not dest.exists()  # 半成品清理

    def test_happy_path_within_cap(self, tmp_path, monkeypatch):
        from app.services.pexels_service import _http as hw

        monkeypatch.setattr(hw, "_download_total_timeout_sec", lambda: 180)
        monkeypatch.setattr(hw.time, "monotonic", lambda: 42.0)  # 恒定 → elapsed 恒 0
        monkeypatch.setattr(hw, "validate_video", lambda p: True)
        monkeypatch.setattr(hw, "http_session",
                            lambda svc: _FakeSession(_FakeResp([b"ab", b"cd"])))

        out = hw.download(svc=None, url="https://v.pexels.com/files/42.mp4",
                          pexels_id=42, materials_dir=str(tmp_path / "materials"))
        assert Path(out).read_bytes() == b"abcd"

    def test_total_timeout_default_in_script_env(self, monkeypatch):
        """lifespan 未跑 (get_config 抛 RuntimeError) → 回退 180s 默认."""
        from app.services.pexels_service import _http as hw

        def raiser():
            raise RuntimeError("Config not loaded (lifespan did not run)")

        monkeypatch.setattr("app.config.get_config", raiser)
        assert hw._download_total_timeout_sec() == 180


# ── ② _backfill_pexels_keywords (LLM 漏词兜底) ──

class TestKeywordsBackfill:
    def _slot(self, idx, workflow, text, params=None):
        from app.schemas.director import DirectorSlotPlan
        return DirectorSlotPlan(
            slot_index=idx, start_sec=float(idx * 5), end_sec=float(idx * 5 + 5),
            text_context=text, visual_type=workflow, workflow=workflow,
            params=params or {},
        )

    def _job(self, db: Session) -> DirectorJob:
        art = Article(title="t", raw_text="x" * 10, track="tech")
        db.add(art); db.flush()
        script = Script(article_id=art.id, version=1, script_text="s", status="draft")
        db.add(script); db.flush()
        job = DirectorJob(script_id=script.id, video_format="portrait")
        db.add(job); db.commit()
        return job

    def test_subject_inherited_and_concept_hit(self, tmp_db):
        from app.services.director_service._postprocess import _backfill_pexels_keywords

        with db_session() as db:
            job = self._job(db)
            good = {"keywords": ["man talking to camera", "podcast studio"]}
            missing = self._slot(2, "broll_pexels", "这道数学题的考卷分数非常惊人")

            class P:
                slots = [
                    self._slot(0, "broll_pexels", "叙述甲", dict(good)),
                    self._slot(1, "broll_pexels", "叙述乙", dict(good)),
                    missing,
                ]
            _backfill_pexels_keywords(db, job, P)
            kws = missing.params["keywords"]
            assert kws[0] == "man talking to camera"   # 多数主体继承
            assert "math exam paper" in kws            # 概念映射命中
            assert missing.params["fallback_keywords"] is True
            # 已有词的槽不动、不打标
            assert P.slots[0].params == good
            assert "fallback_keywords" not in P.slots[0].params
            # trace 落库可观测
            steps = [t["step"] for t in (job.plan_json or {}).get("trace", [])]
            assert "pexels_keywords_backfill" in steps

    def test_empty_list_treated_as_missing(self, tmp_db):
        from app.services.director_service._postprocess import _backfill_pexels_keywords

        with db_session() as db:
            job = self._job(db)
            s = self._slot(0, "broll_pexels", "编程写代码调试程序", {"keywords": []})

            class P:
                slots = [s]
            _backfill_pexels_keywords(db, job, P)
            assert s.params["keywords"] == ["city skyline timelapse", "programming terminal"]
            assert s.params["fallback_keywords"] is True

    def test_shot_contract_goal_scanned(self, tmp_db):
        from app.services.director_service._postprocess import _backfill_pexels_keywords

        with db_session() as db:
            job = self._job(db)
            s = self._slot(0, "broll_pexels", "",
                           {"shot_contract": {"visual_goal": "电路板铜线走线特写"}})

            class P:
                slots = [s]
            _backfill_pexels_keywords(db, job, P)
            assert "circuit board macro" in s.params["keywords"]

    def test_no_concept_hit_generic_fallback(self, tmp_db):
        from app.services.director_service._postprocess import _backfill_pexels_keywords

        with db_session() as db:
            job = self._job(db)
            s = self._slot(0, "broll_pexels", "一段与词典完全无关的叙述")

            class P:
                slots = [s]
            _backfill_pexels_keywords(db, job, P)
            assert s.params["keywords"] == ["city skyline timelapse", "person using computer"]

    def test_details_cap_and_subject_dedup(self, tmp_db):
        from app.services.director_service._postprocess import _backfill_pexels_keywords

        with db_session() as db:
            job = self._job(db)
            # 主体 = math exam paper (多数), 且文本同时命中 4 类概念 → details ≤3 且主体词不重复
            good = {"keywords": ["math exam paper"]}
            s = self._slot(2, "broll_pexels",
                           "编程代码 服务器算力训练 发布会官方声明 电路板打样")

            class P:
                slots = [
                    self._slot(0, "broll_pexels", "a", dict(good)),
                    self._slot(1, "broll_pexels", "b", dict(good)),
                    s,
                ]
            _backfill_pexels_keywords(db, job, P)
            kws = s.params["keywords"]
            assert kws[0] == "math exam paper"
            details = kws[1:]
            assert len(details) == 3
            assert len(set(details)) == 3                       # 去重
            assert "math exam paper" not in details             # 与主体不重复
            assert all(d in {"programming terminal", "server racks corridor",
                             "tech product launch stage", "circuit board macro"}
                       for d in details)

    def test_non_pexels_and_all_present_untouched(self, tmp_db):
        from app.services.director_service._postprocess import _backfill_pexels_keywords

        with db_session() as db:
            job = self._job(db)
            hf = self._slot(0, "hf_title", "无词 HF 卡")  # 非 pexels → 不动
            ok = self._slot(1, "broll_pexels", "叙述", {"keywords": ["city skyline timelapse"]})

            class P:
                slots = [hf, ok]
            _backfill_pexels_keywords(db, job, P)
            assert hf.params == {}
            assert ok.params == {"keywords": ["city skyline timelapse"]}
            # 无补词 → 无 trace
            assert "pexels_keywords_backfill" not in [
                t["step"] for t in (job.plan_json or {}).get("trace", [])
            ]
