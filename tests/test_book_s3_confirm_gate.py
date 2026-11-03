# -*- coding: utf-8 -*-
"""S3 出口闸 (1103): "(阻断)" 后缀统一拦截 confirm + 关 auto_confirm 旁路.

实锤: ep2 _issues 记录了问题照样 confirmed 出库; 旧 confirm 闸只认"案例跨集重复
(阻断)"一个子串, auto_confirm_if_anim_done 直置 status 整体绕过。
"""
import json
from types import SimpleNamespace as NS

import pytest

from app.services.book_service import episode_gen as eg


def test_blocking_issues_filter_by_suffix():
    ep = NS(roadmap_json={"_issues": [
        "概念未覆盖(阻断): 「包容的专业精神」核心词全稿未出现",
        "案例跨集重复(阻断): 「X」已由第2集讲过",
        "模块计数不同轴: 第一档×3 与 这一步×1",
        "钩子开环未回扣: 全篇未兑现",
    ]})
    got = eg._blocking_issues(ep)
    assert len(got) == 2 and all("(阻断)" in i for i in got)


def test_confirm_blocked_by_s2_concept_gate():
    """S2 概念覆盖闸 (首个新住户) 在 confirm 出口真拦."""
    ep = NS(roadmap_json={"_issues": ["概念未覆盖(阻断): 「包容的专业精神」核心词「包容的专业」全稿未出现"]},
            status="draft", book=NS(episodes=[]))
    with pytest.raises(ValueError, match="确认被阻断"):
        eg.confirm_episode(None, ep)  # type: ignore[arg-type]


def test_confirm_compat_case_dup_still_blocks():
    """1019 案例排重阻断串 (旧格式) 在泛化闸下依旧拦 — 兼容回归."""
    ep = NS(roadmap_json={"_issues": ["案例跨集重复(阻断): 「X」已由第2集讲过"]},
            status="draft", book=NS(episodes=[]))
    with pytest.raises(ValueError, match="确认被阻断"):
        eg.confirm_episode(None, ep)  # type: ignore[arg-type]


def test_confirm_passes_with_hint_only():
    """提示级问题不拦 — 人闸终审语义保留."""
    ep = NS(roadmap_json={"_issues": ["模块计数不同轴: 第一档×3 与 这一步×1"]},
            status="draft", book=NS(episodes=[NS(status="confirmed")]))
    assert eg.confirm_episode(NS(commit=lambda: None), ep).status == "confirmed"  # type: ignore[arg-type]


def test_auto_confirm_skips_when_blocked(tmp_path, monkeypatch):
    """auto_confirm 旁路已关: 动画全 done 但阻断未清 → 留 draft 不自动过."""
    import app.services.anim_pipeline.shots as _shots
    f = tmp_path / "shots.json"
    f.write_text(json.dumps({"shots": [{"status": "anim_done"}]}), encoding="utf-8")
    monkeypatch.setattr(_shots, "shots_path", lambda title, n: f)
    ep = NS(status="draft", script_id="s1", ep_index=2,
            book=NS(book_title="t", episodes=[]),
            roadmap_json={"_issues": ["概念未覆盖(阻断): 「X」全稿未出现"]},
            script_generated_at=None)
    db = NS(commit=lambda: None)
    assert eg.auto_confirm_if_anim_done(db, ep) is False  # type: ignore[arg-type]
    assert ep.status == "draft"


def test_auto_confirm_passes_when_clean(tmp_path, monkeypatch):
    import app.services.anim_pipeline.shots as _shots
    f = tmp_path / "shots.json"
    f.write_text(json.dumps({"shots": [{"status": "anim_done"}]}), encoding="utf-8")
    monkeypatch.setattr(_shots, "shots_path", lambda title, n: f)
    ep = NS(status="draft", script_id="s1", ep_index=2,
            book=NS(book_title="t", episodes=[NS(status="confirmed")]),
            roadmap_json={"_issues": ["模块计数不同轴: 提示级"]},
            script_generated_at=None)
    db = NS(commit=lambda: None)
    assert eg.auto_confirm_if_anim_done(db, ep) is True  # type: ignore[arg-type]
    assert ep.status == "confirmed"
