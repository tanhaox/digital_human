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


def test_carrier_false_positive_exemptions():
    """实锤误报: 已写「空白/无字/无反光」的载体曾被抓成问题。"""
    yes = [
        ("s2 画面句含文字载体「字迹」但未申报", "…窗户玻璃无反光字迹，礼盒…"),
        ("s17 画面句含文字载体「标签」但未申报", "…格面标签留白无字，顶光…"),
        ("s26 画面句含文字载体「便签」但未申报", "…听筒旁贴着一张空白便签…"),
    ]
    for msg, txt in yes:
        assert v3shots._carrier_already_blanked(msg, txt), txt
    no = [
        ("s1 画面句含文字载体「白板」但未申报", "会议室白板前的顾问，双手摊开示意…"),
        ("s3 画面句含文字载体「白板」但未申报", "白板上密密麻麻写满公式，顾问指着…"),
    ]
    for msg, txt in no:
        assert not v3shots._carrier_already_blanked(msg, txt), txt


def test_brand_shots_excluded_from_text_qc():
    """品牌镜走冻结骨架, 不参与 LLM 文字层质检 (实锤: s9/s34 品牌镜被报引号问题)."""
    brand_shot = {"sid": "s9", "arc_id": "A3", "line_refs": [22], "est_s": 5,
                  "keyframe_zh": "山顶岩石上的登山客读书，书页摊开（内容留白）…" * 3,
                  "in_image_text": [], "text_layer": [], "brand_card": True}
    issues = v3shots.text_layer_issues([brand_shot])
    assert "s9" not in issues


def test_quote_position_labels_exempt():
    """实锤误报: LLM 用「图纸台前」: 标镜位, 被当成画内文字报错。"""
    assert v3shots._quotes_are_position_labels('深夜书房微位「图纸台前」：近景俯角，老谭指尖悬在模型上方…')
    assert v3shots._quotes_are_position_labels('「窗边旧椅」：中景侧逆光，瑞贝卡抱膝而坐…')
    # 真·画内文字 → 不豁免
    assert not v3shots._quotes_are_position_labels('白板上写着「止损两问」四个大字，顾问指着…')
    assert not v3shots._quotes_are_position_labels('没有引号的普通画面句，桌上摊着文件…')


def test_one_card_per_line_priority():
    """实锤: 同一句被 list+img 两个建议各建一张卡, 文字相同 = 视觉重叠。"""
    sh = [{"sid": "s1", "line_refs": [7]}]
    v3 = {"cards": [], "lines": [
        {"no": 7, "text": "第一项，会做什么、不会做什么。", "adopted": ["list", "img"]}]}
    cs = v3shots.build_cards(v3, sh)
    assert len(cs) == 1 and cs[0]["style"] == "list"       # 文字卡优先
    # 只有配图建议时才建配图卡
    v3b = {"cards": [], "lines": [{"no": 7, "text": "某句。", "adopted": ["img"]}]}
    assert v3shots.build_cards(v3b, sh)[0]["style"] == "img"
    # 不同行各自成卡
    v3c = {"cards": [], "lines": [
        {"no": 7, "text": "甲。", "adopted": ["list"]},
        {"no": 9, "text": "乙。", "adopted": ["quote"]}]}
    sh2 = [{"sid": "s1", "line_refs": [7, 9]}]
    assert len(v3shots.build_cards(v3c, sh2)) == 2


def test_stale_adopted_cards_not_duplicated():
    """实锤: 旧生成卡被当稿件指令卡保留 → 同一行两份, 文字完全相同。"""
    sh = [{"sid": "s1", "line_refs": [41]}]
    v3 = {"cards": [{"cid": "c01", "style": "img", "text": "旧卡",
                     "line_no": 41, "from": "adopted"}],
          "lines": [{"no": 41, "text": "第一项…", "adopted": ["list", "img"]}]}
    cs = v3shots.build_cards(v3, sh)
    assert len(cs) == 1 and cs[0]["style"] == "list"
    # 稿件指令卡 (marker) 必须保留
    v3b = {"cards": [{"cid": "c01", "style": "list", "text": "稿件卡",
                      "line_no": 41, "from": "marker"}],
           "lines": [{"no": 41, "text": "第一项…", "adopted": []}]}
    cs2 = v3shots.build_cards(v3b, sh)
    assert len(cs2) == 1 and cs2[0]["text"] == "稿件卡"


def test_marker_and_adopted_no_double_card():
    """实锤: marker 卡与 adopted 卡去重 key 不同型 → 同行出两张完全相同的卡。"""
    sh = [{"sid": "s1", "line_refs": [41]}]
    v3 = {"cards": [{"cid": "c01", "style": "event", "text": "同句",
                     "line_no": 41, "from": "marker"}],
          "lines": [{"no": 41, "text": "同句", "adopted": ["event"]}]}
    cs = v3shots.build_cards(v3, sh)
    assert len(cs) == 1, [c["style"] for c in cs]
    # 稿件指令卡优先 (即使建议是别的样式)
    v3b = {"cards": [{"cid": "c01", "style": "list", "text": "稿件卡",
                      "line_no": 41, "from": "marker"}],
           "lines": [{"no": 41, "text": "同句", "adopted": ["img"]}]}
    cs2 = v3shots.build_cards(v3b, sh)
    assert len(cs2) == 1 and cs2[0]["style"] == "list"


def test_kind_style_two_dimensions():
    """用户令: 内容类型(六类) 与 渲染方式(三通道) 是两个维度, 不混一列。"""
    sh = [{"sid": "s1", "line_refs": [41]}]
    # 注释前缀 → kind + 默认 style
    v3 = {"cards": [{"cid": "c01", "kind": "结论", "name": "结论",
                     "text": "边界即信任", "line_no": 41, "from": "marker"}],
          "lines": [{"no": 41, "text": "边界即信任。", "adopted": []}]}
    c = v3shots.build_cards(v3, sh)[0]
    assert c["kind"] == "结论" and c["style"] == "event"      # 结论 → 事件字
    # 建议 → kind 映射
    assert v3shots._SUGG_KIND["quote"] == "金句"
    assert v3shots._SUGG_KIND["event"] == "结论"
    assert v3shots._SUGG_KIND["list"] == "总结"
    # 六类都有默认渲染
    for k in v3shots.KINDS:
        assert k in v3shots.KIND_STYLE, k


def test_note_prefix_maps_to_kind():
    from app.services.anim_pipeline.v3_pipeline import triage
    for pref, kind in (("金句", "金句"), ("口诀", "口诀"), ("结论", "结论"),
                       ("标题", "标题"), ("提示", "提示"), ("总结", "总结")):
        mode, rec = triage.route_markers(f"{pref}：示例内容")
        assert mode == "note" and rec["kind"] == kind, (pref, rec)


def test_card_render_html_builds_for_all_kinds():
    """配图渲染: 六类内容各自能出 HTML (不启动浏览器, 只验纯函数)。"""
    from app.services.anim_pipeline.v3_pipeline import card_render as cr
    for kind, lines in (("总结", ["甲", "乙"]), ("金句", ["信任是账户，取现要预约"]),
                        ("结论", ["边界即信任"]), ("标题", ["核心二三"]),
                        ("提示", ["先做这个"]), ("口诀", ["丑话前置"])):
        h = cr.build_card_html(kind, "标题", lines)
        assert "<html>" in h and "老谭读书" in h
        assert "content" in h
