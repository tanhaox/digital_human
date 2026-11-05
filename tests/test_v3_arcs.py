# -*- coding: utf-8 -*-
"""1105 v3 V2 立意: 语态切弧/骨架/弧界执法/路由接线 (零 LLM)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline.v3_pipeline import arcs


def _lines():
    return [
        {"no": 1, "text": "很多顾问都踩过这个大坑：", "mode": "hook"},
        {"no": 2, "text": "【0-60 秒｜开篇】", "mode": "section"},
        {"no": 3, "text": "书里说：信任是一点一滴存出来的账户。", "mode": "quote"},
        {"no": 4, "text": "这话我信。", "mode": "comm"},
        {"no": 5, "text": "但你账户塌的时候，第一个念头是补。", "mode": "comm"},
        {"no": 6, "text": "", "mode": "comm"},                      # 空行剔除
        {"no": 7, "text": "2020年那单，我丢了。", "mode": "persona"},
        {"no": 8, "text": "关注我，下集继续。", "mode": "cta"},
        {"no": 9, "text": "【卡:止损两问】两轮不开吗？", "mode": "card"},
    ]


def test_modules_group_consecutive_mode():
    mods = arcs.modules_of(_lines())
    assert [(m["mode"], m["no_start"], m["no_end"]) for m in mods] == [
        ("hook", 1, 1), ("quote", 3, 3), ("comm", 4, 5),
        ("persona", 7, 7), ("cta", 8, 8)]
    # 段标/空行/卡指令都不成模块
    assert all(m["mode"] not in ("section", "card", "img", "unknown") for m in mods)


def test_skeleton_one_arc_per_module():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    assert [a["arc_id"] for a in sk] == ["A1", "A2", "A3", "A4", "A5"]
    assert [a["type"] for a in sk] == ["hook", "quote", "narr", "persona", "cta"]
    assert sk[2]["no_start"] == 4 and sk[2]["no_end"] == 5   # 两行评论并一弧
    assert sk[2]["line_count"] == 2
    assert all(a["est_s"] > 0 for a in sk)


def test_boundary_clean_passes():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    assert arcs.boundary_issues(sk, _lines()) == []


def test_boundary_cross_mode_flagged():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    bad = [dict(sk[0]), dict(sk[1], no_end=5), *sk[3:]]   # 书证弧吞掉评论行
    issues = arcs.boundary_issues(bad, _lines())
    assert any("跨语态" in i for i in issues), issues


def test_boundary_missing_and_duplicate_flagged():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    dropped = sk[:-1]                                      # 丢掉最后一段 CTA
    assert any("未归属任何弧" in i for i in arcs.boundary_issues(dropped, _lines()))
    dup = sk + [dict(sk[2], arc_id="A9")]                  # 同一区间归两弧
    assert any("重复归属" in i for i in arcs.boundary_issues(dup, _lines()))


def test_non_narrative_types():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    non = {a["type"] for a in sk if a["mode"] in ("hook", "cta", "brand")}
    assert non == {"hook", "cta"}
    assert "brand" in arcs.NON_NARRATIVE


def test_wiring_v2():
    import inspect
    from pathlib import Path
    from app.routers import anim_v3
    src = inspect.getsource(anim_v3)
    assert "/concept\"" in src and "/concept/save" in src
    assert "boundary_issues" in src                  # 保存时复检执法
    # 端点内延迟导入必须真能解析 (V1 实锤: 相对导入错 → 保存恒 500)
    import importlib
    for fn in (anim_v3.v3_concept_get, anim_v3.v3_concept_save):
        for line in inspect.getsource(fn).splitlines():
            line = line.strip()
            if line.startswith("from ") and " import " in line:
                exec(line, {})
    for f in ("web/v3/concept.html", "web/v3/concept.js", "web/v3/concept.css"):
        assert Path(f).exists(), f
