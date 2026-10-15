# -*- coding: utf-8 -*-
"""证据图管线②③ 单测 (2026-09-04): extract_images 过滤 / is_evidence_claim 正反例 /
pick_image_for_slot 打分与去重 / 闸门降级 / handler 集成 (tmp SQLite + monkeypatch).

不联网、不拉 llama-server — VLM/下载全部 monkeypatch (先例 test_pexels_service).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import load_config, set_config
from app.database import db_session, init_db
from app.models import (
    Article,
    DirectorJob,
    DirectorSlot,
    MaterialItem,
    MaterialPackage,
    Script,
)
from app.services.url_fetcher import extract_images


# ── 测试环境: 独立 SQLite + materials_dir 指向 tmp ──

@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test_evidence.db"
    cfg = load_config()
    # materials_dir (证据图缓存) + composition_output_root (slot 产物) 都指到 tmp
    cfg.__dict__["defaults"] = cfg.defaults.__class__(
        **{
            **{k: getattr(cfg.defaults, k) for k in cfg.defaults.__dataclass_fields__},
            "materials_dir": str(tmp_path / "materials"),
            "composition_output_root": str(tmp_path / "composition"),
        }
    )
    set_config(cfg)
    init_db(f"sqlite:///{db_path}")
    yield


# ── ① extract_images (url_fetcher) ──

class TestExtractImages:
    HTML = """
    <img src="https://a.com/icon.png">
    <img data-src="https://cdn.a.com/swe-bench.jpg" width="800" alt="SWE-bench 得分对比">
    <img src="https://a.com/pixel.gif" width="1" height="1">
    <img src="//a.com/chart2.png" width="1200" height="600">
    <img src="/rel/chart3.webp">
    <img src="https://a.com/logo.svg">
    <img src="https://cdn.a.com/swe-bench.jpg" width="800">  <!-- 重复 (query 外去重) -->
    """

    def test_filters_and_lazy_attrs(self):
        out = extract_images(self.HTML, "https://a.com/news/page.html")
        urls = [o["url"] for o in out]
        # icon/pixel/logo 剔除; data-src 优先; // 补 https; 相对路径补全; 重复去重
        assert urls == [
            "https://cdn.a.com/swe-bench.jpg",
            "https://a.com/chart2.png",
            "https://a.com/rel/chart3.webp",
        ]
        assert out[0]["alt"] == "SWE-bench 得分对比"
        assert out[1]["w"] == 1200

    def test_cap(self):
        html = "".join(f'<img src="https://a.com/i{n}.jpg" width="600">' for n in range(20))
        assert len(extract_images(html, cap=5)) == 5

    def test_relative_without_base_dropped(self):
        # 协议相对 URL 自带 https: 前缀补全, 与 base 无关
        out = extract_images('<img src="//x.com/a.jpg" width="500">')
        assert [o["url"] for o in out] == ["https://x.com/a.jpg"]
        # 真正的相对路径无 base_url 才丢弃
        assert extract_images('<img src="/only/path.jpg" width="500">') == []


# ── ② is_evidence_claim (语义闸门: 数字 + 测试/比较词 双条件) ──

class TestIsEvidenceClaim:
    @pytest.mark.parametrize("text", [
        "智能测试它考了五十九分，满分六十二分",
        "SWE基准实测它干到了七十三点七",
        "每秒三百零五个词，比上一代快了一倍",
        "单价直接砍到零点七美元一次",
        "领先了整整八个百分点",
        "跑分超出对手30%",
        "价格只有对手的三分之一",
        "它在榜单上排第二",
    ])
    def test_positive(self, text):
        from app.services.evidence_service import is_evidence_claim
        assert is_evidence_claim(text)

    @pytest.mark.parametrize("text", [
        "这一招确实是狠招，直接改写了行业玩法",   # 无数字
        "第三周的时候事情有了转机",               # 数字但无比较语义
        "大家好我是老谭",                         # 口播套话
        "",                                      # 空
        # 真机 Gemini 稿漏判段 (功能计数词供假数字, 2026-09-04 E2E 实证):
        "谷歌刚扔出一个模型，便宜到像白送。",     # "一个"不算数据
        "原因只有一条，它们没比Flash强出足够身位",
        "旗舰要是只比便宜货强一点点",
        "留了一手",                               # "一手"不算
    ])
    def test_negative(self, text):
        from app.services.evidence_service import is_evidence_claim
        assert not is_evidence_claim(text)

    def test_biru_no_false_positive(self):
        from app.services.evidence_service import is_evidence_claim
        # "比如" 不是比较句式, 不应触发 _CLAIM_CMP
        assert not is_evidence_claim("比如前年就有了类似的产品")


# ── ② _number_in_text 边界判定 (防部分撞车) ──

class TestNumberBoundary:
    def test_cn_reading_partial_no_match(self):
        # 图 '60' 读 '六十' 不得命中文 '六十二' (真机 E2E 撞车实证)
        from app.services.evidence_service import _num_variants, _number_in_text
        v = _num_variants("60")
        assert not _number_in_text("60", v, "满分约六十二，它跑到五十九")

    def test_cn_reading_exact_match(self):
        from app.services.evidence_service import _num_variants, _number_in_text
        v = _num_variants("73.7")
        assert _number_in_text("73.7", v, "在软件工程基准上，它考了七十三点七。什么概念？")

    def test_cn_reading_standalone(self):
        from app.services.evidence_service import _num_variants, _number_in_text
        v = _num_variants("60")
        assert _number_in_text("60", v, "帧率稳定在六十附近")


# ── ② pick_image_for_slot (数字重合最强信号) ──

class TestPickImageForSlot:
    POOL = [
        {"url": "u1", "local_path": "p1", "source_media": "新浪", "kind": "benchmark",
         "desc_zh": "SWE基准得分表 73.7 领先", "numbers": ["73.7", "59"], "quality": 5},
        {"url": "u2", "local_path": "p2", "source_media": "搜狐", "kind": "photo",
         "desc_zh": "数据中心机房照片", "numbers": [], "quality": 9},
        {"url": "u3", "local_path": "p3", "source_media": "网易", "kind": "price",
         "desc_zh": "API 定价页 对比 Gemini", "numbers": ["0.7"], "quality": 6},
    ]

    def test_number_match_beats_quality(self):
        from app.services.evidence_service import pick_image_for_slot
        c = pick_image_for_slot("SWE基准实测它干到了七十三点七", [], self.POOL)
        assert c["url"] == "u1"  # 数字命中 ×10 压过 u2 的 quality 9

    def test_used_exclusion(self):
        from app.services.evidence_service import pick_image_for_slot
        c = pick_image_for_slot("SWE基准实测它干到了七十三点七", [], self.POOL, {"u1"})
        assert c is None  # u2/u3 无数字命中 → 无合格图

    def test_price_kind_bonus(self):
        from app.services.evidence_service import pick_image_for_slot
        c = pick_image_for_slot("单价只要零点七美元", ["Gemini"], self.POOL)
        assert c["url"] == "u3"

    def test_no_match_returns_none(self):
        from app.services.evidence_service import pick_image_for_slot
        assert pick_image_for_slot("今天天气不错", [], self.POOL) is None


# ── ② collect_evidence_pool / _parse_vlm_json ──

class TestPoolAndVlmParse:
    def test_parse_vlm_json(self):
        from app.services.evidence_service import _parse_vlm_json
        raw = '啰嗦前言 {"is_chart": true, "kind": "leaderboard", "desc_zh": "智能指数榜单", "numbers": ["59", "62", 3.5], "quality": 7, "watermark": "corner"}'
        v = _parse_vlm_json(raw)
        assert v["is_chart"] and v["kind"] == "leaderboard"
        assert v["numbers"] == ["59", "62", "3.5"]
        assert v["watermark"] == "corner"

    def test_parse_garbage(self):
        from app.services.evidence_service import _parse_vlm_json
        assert _parse_vlm_json("模型抽风没有json") is None

    def test_pool_filters(self, tmp_db):
        from app.services.evidence_service import collect_evidence_pool, reset_vlm_client
        reset_vlm_client()
        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech")
            db.add(art); db.flush()
            script = Script(article_id=art.id, version=1, script_text="s", status="draft")
            db.add(script); db.flush()
            pkg = MaterialPackage(article_id=art.id)
            db.add(pkg); db.flush()
            it = MaterialItem(package_id=pkg.id, source_type="url", raw_text="r",
                              fetch_ok=True, char_count=1)
            db.add(it); db.flush()
            script.material_package_id = pkg.id
            it.images_json = [
                {"url": "ok", "local_path": "p", "source_media": "m",
                 "vlm": {"is_chart": True, "quality": 6, "kind": "benchmark",
                          "numbers": ["73.7"], "desc_zh": "", "watermark": "none"}},
                {"url": "lowq", "local_path": "p2", "source_media": "m",
                 "vlm": {"is_chart": True, "quality": 3, "numbers": [], "desc_zh": "", "watermark": "none"}},   # quality<4
                {"url": "heavy", "local_path": "p3", "source_media": "m",
                 "vlm": {"is_chart": True, "quality": 8, "numbers": [], "desc_zh": "", "watermark": "heavy"}},  # 水印
                {"url": "notchart", "local_path": "p4", "source_media": "m",
                 "vlm": {"is_chart": False, "quality": 9, "numbers": [], "desc_zh": "", "watermark": "none"}},  # 非证据图
                {"url": "novlm", "local_path": "p5", "source_media": "m", "vlm": None},                        # VLM 失败
                {"url": "nolocal", "source_media": "m",
                 "vlm": {"is_chart": True, "quality": 8, "numbers": [], "desc_zh": "", "watermark": "none"}},  # 无 local_path
            ]
            db.commit()
            pool = collect_evidence_pool(db, script)
        assert [p["url"] for p in pool] == ["ok"]

    def test_pool_no_package(self, tmp_db):
        from app.services.evidence_service import collect_evidence_pool, reset_vlm_client
        reset_vlm_client()
        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech")
            db.add(art); db.flush()
            script = Script(article_id=art.id, version=1, script_text="s", status="draft")
            db.add(script); db.commit()
            assert collect_evidence_pool(db, script) == []  # 无素材包


# ── ② scan_package_images 烟雾 (config/缓存目录路径 + 空包零开销返回) ──

class TestScanSmoke:
    def test_empty_package_smoke(self, tmp_db):
        # 踩到 get_config().defaults.materials_dir + cache mkdir (真机曾在此 AttributeError)
        from app.services.evidence_service import scan_package_images
        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech")
            db.add(art); db.flush()
            pkg = MaterialPackage(article_id=art.id)
            db.add(pkg); db.commit()
            stats = scan_package_images(db, pkg)
        assert stats == {"scanned_items": 0, "candidates": 0, "downloaded": 0, "charts": 0}

    def test_rescan_preserves_vlm_on_failure(self, tmp_db, tmp_path, monkeypatch):
        # 真机实证: 二扫 VLM 挂 → vlm:null 不得冲掉旧标签; null 条目可补扫
        from app.services import evidence_service as es

        img = tmp_path / "c.jpg"
        from PIL import Image
        Image.new("RGB", (640, 480), (10, 20, 30)).save(img)

        good = '{"is_chart": true, "kind": "leaderboard", "desc_zh": "榜", "numbers": ["73.7"], "quality": 9, "watermark": "none"}'
        state = {"reply": good}

        class StubClient:
            def chat_with_images(self, paths, sys, user):
                if state["reply"] is None:
                    raise RuntimeError("server hiccup")
                return state["reply"]

        monkeypatch.setattr(es, "_get_vlm_client", lambda: StubClient())
        monkeypatch.setattr(es, "_download_image", lambda url, cache: (str(img), 640, 480))

        URLS = ["https://a.com/a.jpg", "https://a.com/b.jpg"]
        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech", source_url="https://a.com/p",
                          images_json=[{"url": u, "alt": "", "w": 640, "h": 480} for u in URLS])
            db.add(art); db.flush()
            pkg = MaterialPackage(article_id=art.id)
            db.add(pkg); db.commit()

            # 首扫 VLM 全挂 → vlm:null, 条目未扫完 (可补扫)
            state["reply"] = None
            s1 = es.scan_package_images(db, pkg)
            assert s1["charts"] == 0
            item = pkg.items[0]
            assert all(e["vlm"] is None for e in item.images_json)

            # 补扫 VLM 恢复 → 打上标签
            state["reply"] = good
            s2 = es.scan_package_images(db, pkg)
            assert s2["charts"] == 2
            assert item.images_json[0]["vlm"]["numbers"] == ["73.7"]

            # 扫完条目 → 幂等跳过 (VLM 再挂也不重扫不降级)
            state["reply"] = None
            s3 = es.scan_package_images(db, pkg)
            assert s3["downloaded"] == 0
            assert item.images_json[0]["vlm"]["numbers"] == ["73.7"]

            # 混合态 (模拟预算中断): b 条 null + VLM 仍挂 → 重试但 a 好标签保住
            # (注意深拷贝: 浅拷贝原地改会连同旧值一起脏, SQLAlchemy 判无变化不 flush)
            ij = [dict(e) for e in item.images_json]
            ij[1]["vlm"] = None
            item.images_json = ij; db.commit()
            s4 = es.scan_package_images(db, pkg)
            assert s4["downloaded"] == 2  # 未扫完 → 整条目重试
            by_url = {e["url"]: e["vlm"] for e in item.images_json}
            assert by_url[URLS[0]]["numbers"] == ["73.7"]  # 失败不降级
            assert by_url[URLS[1]] is None  # 本轮仍失败, 维持 null


# ── ④ 闸门 (_enforce_evidence_gates) ──

class TestEvidenceGates:
    def _make_plan_slot(self, idx, workflow, text, claim=""):
        from app.schemas.director import DirectorSlotPlan
        params = {"claim": claim} if claim else {}
        return DirectorSlotPlan(
            slot_index=idx, start_sec=float(idx * 5), end_sec=float(idx * 5 + 5),
            text_context=text, visual_type=workflow, workflow=workflow, params=params,
        )

    def test_semantic_gate_demotes(self, tmp_db, monkeypatch):
        from app.services.director_service._postprocess import _enforce_evidence_gates
        from app.services import evidence_service as es
        # 池非空 (可用性闸门放行), 语义闸门拦截
        monkeypatch.setattr(es, "collect_evidence_pool", lambda db, script: [{"url": "u"}])
        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech")
            db.add(art); db.flush()
            script = Script(article_id=art.id, version=1, script_text="s", status="draft")
            db.add(script); db.flush()
            job = DirectorJob(script_id=script.id, video_format="portrait")
            db.add(job); db.commit()

            class P:  # 轻量 plan 桩
                slots = [
                    self._make_plan_slot(0, "evidence_image", "这一招确实是狠招改写了行业玩法"),  # 非比较段 → 降级
                    self._make_plan_slot(1, "evidence_image", "SWE基准实测干到了七十三点七"),     # 保留
                    self._make_plan_slot(2, "broll_pexels", "一般叙述段"),                        # 不动
                ]
            _enforce_evidence_gates(db, job, P)
            assert P.slots[0].workflow == "broll_pexels"
            assert "fallback_reason" in P.slots[0].params
            assert P.slots[1].workflow == "evidence_image"
            assert P.slots[2].workflow == "broll_pexels"

    def test_empty_pool_demotes_all(self, tmp_db, monkeypatch):
        from app.services.director_service._postprocess import _enforce_evidence_gates
        from app.services import evidence_service as es
        monkeypatch.setattr(es, "collect_evidence_pool", lambda db, script: [])
        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech")
            db.add(art); db.flush()
            script = Script(article_id=art.id, version=1, script_text="s", status="draft")
            db.add(script); db.flush()
            job = DirectorJob(script_id=script.id, video_format="portrait")
            db.add(job); db.commit()

            class P:
                slots = [self._make_plan_slot(i, "evidence_image", f"测试分数第{i}名成绩") for i in range(3)]
            _enforce_evidence_gates(db, job, P)
            assert all(s.workflow == "broll_pexels" for s in P.slots)

    def test_cap_gate(self, tmp_db, monkeypatch):
        from app.services.director_service._postprocess import _EVIDENCE_CAP, _enforce_evidence_gates
        from app.services import evidence_service as es
        monkeypatch.setattr(es, "collect_evidence_pool", lambda db, script: [{"url": "u"}])
        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech")
            db.add(art); db.flush()
            script = Script(article_id=art.id, version=1, script_text="s", status="draft")
            db.add(script); db.flush()
            job = DirectorJob(script_id=script.id, video_format="portrait")
            db.add(job); db.commit()

            class P:
                slots = [self._make_plan_slot(i, "evidence_image", f"测试{i}分成绩排名")
                         for i in range(_EVIDENCE_CAP + 3)]
            _enforce_evidence_gates(db, job, P)
            kept = [s for s in P.slots if s.workflow == "evidence_image"]
            demoted = [s for s in P.slots if s.workflow == "broll_pexels"]
            assert len(kept) == _EVIDENCE_CAP
            assert len(demoted) == 3

    @staticmethod
    def _script(db, art):
        s = Script(article_id=art.id, version=1, script_text="s", status="draft")
        db.add(s); db.flush()
        return s.id


# ── ③ handler 集成 (monkeypatch 下载/VLM/ffmpeg; 断言产物与写回) ──

class TestHandler:
    def test_execute_slot(self, tmp_db, tmp_path, monkeypatch):
        from app.services.slot_workflows import evidence_image as hw
        from app.services.slot_workflows.evidence_image import execute_evidence_image_slot

        # 造一张真实小图 (Pillow 校验要过)
        from PIL import Image
        img_file = tmp_path / "ev.jpg"
        Image.new("RGB", (640, 480), (30, 40, 60)).save(img_file)

        # handler 顶层 from-import → 打 handler 模块命名空间
        monkeypatch.setattr(hw, "collect_evidence_pool", lambda db, script: [{
            "url": "https://x/chart.jpg", "local_path": str(img_file),
            "source_media": "新浪科技", "kind": "benchmark",
            "desc_zh": "SWE基准 73.7", "numbers": ["73.7"], "quality": 7,
        }])

        rendered: list = []
        monkeypatch.setattr(
            hw, "render_scale_pad",
            lambda src, out, **kw: rendered.append((src, out, kw)) or (out.write_bytes(b"x") or str(out)),
        )
        monkeypatch.setattr(
            hw, "run_ffmpeg",
            lambda cmd, timeout=None: Path(cmd[cmd.index("-i") + 1]).with_suffix(".raw.mp4").write_bytes(b"x"),
        )

        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech")
            db.add(art); db.flush()
            script = Script(article_id=art.id, version=1, script_text="s", status="draft")
            db.add(script); db.flush()
            job = DirectorJob(script_id=script.id, video_format="portrait")
            db.add(job); db.flush()
            slot = DirectorSlot(
                director_job_id=job.id, slot_index=3, start_sec=10.0, end_sec=15.5,
                duration_sec=5.5,
                text_context="SWE基准实测它干到了七十三点七",
                visual_type="evidence_image", workflow="evidence_image",
                params_json={"claim": "SWE基准 73.7%", "keywords": ["Gemini"]},
                status="queued",
            )
            db.add(slot); db.commit(); db.refresh(slot)

            out = execute_evidence_image_slot(db, slot)
            assert "evidence_003.mp4" in out
            assert slot.params_json["image_url"].endswith("chart.jpg")
            assert slot.params_json["source_media"] == "新浪科技"
            assert len(rendered) == 1

    def test_empty_pool_raises(self, tmp_db, monkeypatch):
        from app.services.slot_workflows import evidence_image as hw
        from app.services.slot_workflows.evidence_image import execute_evidence_image_slot
        monkeypatch.setattr(hw, "collect_evidence_pool", lambda db, script: [])
        with db_session() as db:
            art = Article(title="t", raw_text="x" * 10, track="tech")
            db.add(art); db.flush()
            script = Script(article_id=art.id, version=1, script_text="s", status="draft")
            db.add(script); db.flush()
            job = DirectorJob(script_id=script.id, video_format="portrait")
            db.add(job); db.flush()
            slot = DirectorSlot(
                director_job_id=job.id, slot_index=0, start_sec=0, end_sec=5,
                duration_sec=5.0,
                text_context="x", visual_type="evidence_image", workflow="evidence_image",
                params_json={}, status="queued",
            )
            db.add(slot); db.commit()
            with pytest.raises(RuntimeError, match="池为空"):
                execute_evidence_image_slot(db, slot)
