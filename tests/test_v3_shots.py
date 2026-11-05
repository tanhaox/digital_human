# -*- coding: utf-8 -*-
"""1105 v3 V3 分镜文字: 装包/卡挂载/质检过滤/路由接线 (零 LLM)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline.v3_pipeline import shots as v3shots


def _lines():
    return [
        {"no": 1, "text": "很多顾问都踩过这个大坑：", "mode": "hook"},
        {"no": 2, "text": "书里说：信任是一点一滴存出来的账户。", "mode": "quote"},
        {"no": 3, "text": "这话我信。", "mode": "comm"},
        {"no": 4, "text": "但你账户塌的时候，第一个念头是补。", "mode": "comm"},
        {"no": 5, "text": "第二个念头是躲，很多人就躲过去了。", "mode": "comm"},
        {"no": 6, "text": "2020年那单，我丢了。", "mode": "persona"},
        {"no": 7, "text": "关注我，下集继续。", "mode": "cta"},
    ]


def _arcs():
    return [
        {"arc_id": "A1", "type": "hook", "mode": "hook", "no_start": 1, "no_end": 1},
        {"arc_id": "A2", "type": "quote", "mode": "quote", "no_start": 2, "no_end": 2},
        {"arc_id": "A3", "type": "narr", "mode": "comm", "no_start": 3, "no_end": 5},
        {"arc_id": "A4", "type": "persona", "mode": "persona", "no_start": 6, "no_end": 6},
        {"arc_id": "A5", "type": "cta", "mode": "cta", "no_start": 7, "no_end": 7},
    ]


def test_pack_sids_continuous_and_total_preserved():
    sh, _ = v3shots.pack_shots(_arcs(), _lines())
    assert [s["sid"] for s in sh] == [f"s{i}" for i in range(1, len(sh) + 1)]
    assert all(s["line_refs"] for s in sh)
    total_est = round(sum(s["est_s"] for s in sh), 1)
    line_of = {ln["no"]: ln for ln in _lines()}
    expect = round(sum(len(l["text"]) / v3shots.CHARS_PER_SEC for l in line_of.values()), 1)
    assert abs(total_est - expect) < 0.6      # 装包不丢时长


def test_narrative_packs_by_density_band():
    # 弧内 3 句共 ~7s → 应合成 <3 镜 (装包, 不是一句一镜)
    sh, _ = v3shots.pack_shots(_arcs(), _lines())
    a3 = [s for s in sh if s["arc_id"] == "A3"]
    assert 1 <= len(a3) <= 3
    for s in a3:
        assert s["est_s"] <= v3shots.SHOT_MAX_S + 0.1 or len(s["line_refs"]) == 1


def test_non_narrative_whole_arc_single_shot():
    sh, _ = v3shots.pack_shots(_arcs(), _lines())
    for aid in ("A1", "A5"):    # hook / cta 不切碎
        assert len([s for s in sh if s["arc_id"] == aid]) == 1


def test_build_cards_from_markers_and_adopted():
    sh, _ = v3shots.pack_shots(_arcs(), _lines())
    v3 = {
        "cards": [{"cid": "c01", "style": "list", "text": "止损两问", "line_no": 4}],
        "lines": [dict(l, adopted=["event"]) if l["no"] == 5 else l for l in _lines()],
    }
    cards = v3shots.build_cards(v3, sh)
    assert any(c["from"] == "marker" and c["line_no"] == 4 for c in cards)
    ev = [c for c in cards if c["from"] == "adopted"]
    assert ev and ev[0]["style"] == "event" and ev[0]["line_no"] == 5
    assert all(c["sid"] for c in cards)       # 都映射到了镜


def test_text_layer_issues_filters_later_phase():
    # 短画面句 → 应报「过短」; 缺 keyframe_en/motion (④ 才产) 不应出现在文字层
    doc_shots = [{"sid": "s1", "arc_id": "A1", "line_refs": [1], "est_s": 3,
                  "keyframe_zh": "太短", "in_image_text": [], "text_layer": []}]
    issues = v3shots.text_layer_issues(doc_shots)
    flat = " ".join(issues.get("s1", []))
    assert "过短" in flat
    assert "缺 keyframe_en" not in flat and "缺 motion" not in flat


def test_text_layer_issues_carrier_unreported():
    doc_shots = [{"sid": "s1", "arc_id": "A1", "line_refs": [1], "est_s": 3,
                  "keyframe_zh": "会议室白板上写着三条建议，顾问站在窗前举手讲解整体流程与下一步的分工安排，光线从左侧窗格斜入，桌面文件堆叠",
                  "in_image_text": [], "text_layer": []}]
    issues = v3shots.text_layer_issues(doc_shots)
    assert any("文字载体" in x for x in issues.get("s1", []))


def test_wiring_v3():
    import inspect
    from pathlib import Path
    from app.routers import anim_v3
    src = inspect.getsource(anim_v3)
    assert "/shots\"" in src and "/shots/save" in src
    assert "text_layer_issues" in src
    for fn in (anim_v3.v3_shots_get, anim_v3.v3_shots_save):
        for line in inspect.getsource(fn).splitlines():
            line = line.strip()
            if line.startswith("from ") and " import " in line:
                exec(line, {})          # 延迟导入必须可解析 (V1 实锤)
    for f in ("web/v3/shots.html", "web/v3/shots.js", "web/v3/shots.css"):
        assert Path(f).exists(), f


def test_brand_arc_gets_frozen_skeleton_not_llm():
    """用户令: 非叙事位不分配立意 ≠ 没有画面 — 品牌位走冻结骨架 (LLM 零参与)."""
    from app.services.anim_pipeline import brand
    sh, _ = v3shots.pack_shots(_arcs(), _lines())
    brand_shots = [s for s in sh if s["arc_type"] == "brand"]
    if not brand_shots:
        # 本夹具无 brand 弧 → 造一个
        _arcs2 = _arcs() + [{"arc_id": "A6", "type": "brand", "mode": "brand",
                             "no_start": 7, "no_end": 7}]
        sh, _ = v3shots.pack_shots(_arcs2, _lines())
        brand_shots = [s for s in sh if s["arc_type"] == "brand"]
    assert brand_shots, "品牌弧必须切出镜"
    n = v3shots.fill_brand_shots(brand_shots, {"color_palette": "暖橙+青灰"})
    assert n == len(brand_shots)
    for s in brand_shots:
        assert s["keyframe_zh"], "品牌镜必须有画面句 (来自冻结骨架)"
        assert s.get("brand_card") is True      # v2 字段: K2/H3 品牌直通
        assert s.get("anim", {}).get("beats")   # 三拍 beats 等比拉伸
        assert s.get("source") == "brand_card"


def test_brand_skeleton_is_deterministic():
    """同一槽长两次生成必须逐字一致 (品牌 DNA 不许随机)."""
    from app.services.anim_pipeline import brand
    a = brand.shot_design(5.0, {"color_palette": "暖橙"})
    b = brand.shot_design(5.0, {"color_palette": "暖橙"})
    assert a["keyframe_zh"] == b["keyframe_zh"]
    assert a["anim"]["opening_desc"] == b["anim"]["opening_desc"]
