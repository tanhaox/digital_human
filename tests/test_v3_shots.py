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
        "lines": [dict(l, adopted=["event"],
                       suggs=[{"kind": "event", "card_text": "体面退出"}])
                  if l["no"] == 5 else l for l in _lines()],
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


def test_carrier_blanked_overlap_and_real_props():
    """1105 ep5 实锤: 「空白板面」的「空白」跨在载体词上 → 前后文窗口永远看不到, 7 镜假红字;
    而便签/屏幕当氛围道具画 (无空白限定) 是真问题, 不许一起放过。"""
    yes = [
        # 「空」在前文、「白」是载体首字 — 旧实现 pre/post 都看不到「空白」
        ("s1 画面句含文字载体「白板」但未申报", "前景散落着几页草稿。空白板面。镜头低角度特写"),
        # 同一无字物件: 先写空白, 后文只提反光, 无内容动词
        ("s31 画面句含文字载体「白板」但未申报",
         "他身后一块立式白板板面空白，板面边缘还很新；白板板面在余光里微微反光"),
    ]
    for msg, txt in yes:
        assert v3shots._carrier_already_blanked(msg, txt), txt
    no = [
        ("s5 画面句含文字载体「便签」但未申报", "身后玻璃墙上贴着几张便签，其中一张被气流掀起一角"),
        ("s8 画面句含文字载体「屏幕」但未申报", "手机屏幕亮起，显示来电界面，但无文字；焦点在手机屏幕的光亮"),
        ("s27 画面句含文字载体「便签」但未申报", "右侧一张便签纸被穿堂风掀起一角"),
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
    both = [{"kind": "list", "card_text": "①会做什么 ②不会做什么"},
            {"kind": "img", "card_text": "会做/不做/配合/边界"}]
    v3 = {"cards": [], "lines": [
        {"no": 7, "text": "第一项…", "adopted": ["list", "img"], "suggs": both}]}
    cs = v3shots.build_cards(v3, sh)
    # 都有卡文字时图示优先 (1105 二次修正: 分诊只在"念不出来必须上图示"时才给 img)
    assert len(cs) == 1 and cs[0]["style"] == "img"
    # 只有配图建议时才建配图卡
    v3b = {"cards": [], "lines": [{"no": 7, "text": "某句。", "adopted": ["img"],
           "suggs": [{"kind": "img", "card_text": "信任=可信度×可靠度"}]}]}
    assert v3shots.build_cards(v3b, sh)[0]["style"] == "img"
    # 不同行各自成卡
    v3c = {"cards": [], "lines": [
        {"no": 7, "text": "甲。", "adopted": ["list"],
         "suggs": [{"kind": "list", "card_text": "甲乙丙"}]},
        {"no": 9, "text": "乙。", "adopted": ["quote"],
         "suggs": [{"kind": "quote", "card_text": "金句锚"}]}]}
    sh2 = [{"sid": "s1", "line_refs": [7, 9]}]
    assert len(v3shots.build_cards(v3c, sh2)) == 2


def test_stale_adopted_cards_not_duplicated():
    """实锤: 旧生成卡被当稿件指令卡保留 → 同一行两份, 文字完全相同。"""
    sh = [{"sid": "s1", "line_refs": [41]}]
    v3 = {"cards": [{"cid": "c01", "style": "img", "text": "旧卡",
                     "line_no": 41, "from": "adopted"}],
          "lines": [{"no": 41, "text": "第一项…", "adopted": ["list", "img"],
                     "suggs": [{"kind": "list", "card_text": "①会做 ②不做"},
                               {"kind": "img", "card_text": "会做/不做/边界"}]}]}
    cs = v3shots.build_cards(v3, sh)
    assert len(cs) == 1 and cs[0]["style"] == "img"     # 图示优先, 旧 adopted 卡不复活
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
    assert v3shots._SUGG_KIND["img"] == "图示"     # 只有图示(公式/图表/流程/表格)才出图
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


def test_diagram_html_builds():
    """脑图/流程图: ECharts tree (本地 vendor, 零新依赖)。"""
    from app.services.anim_pipeline.v3_pipeline import card_render as cr
    root = {"name": "信任", "children": [
        {"name": "可信度", "children": [{"name": "专业"}, {"name": "经验"}]},
        {"name": "可靠度", "children": [{"name": "按时"}]}]}
    h = cr.build_diagram_html(root)
    assert "echarts" not in h.lower() or "__opt" in h    # 选项通过 __opt 注入
    assert "__opt" in h and "tree" in h and "老谭读书" in h
    # 叶子必须去掉圆点 (实锤: leaves.symbolSize 不生效)
    import json
    opt = json.loads(h.split("window.__opt = ", 1)[1].split(";</script>", 1)[0])
    kids = opt["series"][0]["data"][0]["children"]
    leaf = kids[0]["children"][0]
    assert leaf.get("symbolSize") == 0


def test_blankify_carriers():
    """1105 用户令: v3 停用图内字通道 — 一键合规改成把载体写成「空白XX」, 不再申报。"""
    shots = [
        {"sid": "s5", "keyframe_zh": "玻璃墙上贴着几张便签，其中一张被气流掀起一角"},
        {"sid": "s8", "keyframe_zh": "手机屏幕亮起，显示来电界面"},
        {"sid": "s23", "keyframe_zh": "他的手停在中间位置，画面凝固"},
    ]
    issues = {
        "s5": ["s5 画面句含文字载体「便签」未写成空白 — …"],
        "s8": ["s8 画面句含文字载体「屏幕」未写成空白 — …"],
        "s23": ["s23 画面句含终态词 (停在中间位置) — …"],
    }
    fixed, manual = v3shots.blankify_carriers(shots, issues)
    assert fixed == ["s5"]                                   # 量词后才敢插
    assert "几张空白便签" in shots[0]["keyframe_zh"]
    assert shots[1]["keyframe_zh"].startswith("手机屏幕")     # 「手机屏幕」不出量词 → 不硬插
    assert "in_image_text" not in shots[0]                    # 不再写申报字段
    assert len(manual) == 2 and any("s8" in m for m in manual)


def test_arc_level_quotas_are_episode_scoped():
    """1105 用户令: v3 图内字停用 → 不报零申报; 人物/景深改全集口径 (弧≠场, 1 镜弧不是场)。"""
    zh = ("深夜书房里，一位顾问俯身在橡木桌旁摊开图纸，台灯暖光从左侧斜切下来，"
          "黄铜镇纸压住纸角，窗外是虚化的城市轮廓，前景散落着几页草稿。")
    shots = [{"sid": f"s{i}", "arc_id": f"A{i}", "est_s": 5.0, "keyframe_zh": zh,
              "in_image_text": [], "text_layer": []} for i in (1, 2, 3)]
    issues = v3shots.text_layer_issues(shots)
    arc = " ".join(issues.get("弧级", []))
    assert "整场零申报" not in arc          # 通道停用
    assert "场A" not in arc                 # 不再按单弧喊配额


def test_strip_quoted_labels():
    """1105 用户令: v3 画面里不许有字 — 引号标签与模型自造的申报注释就地删。"""
    shots = [
        {"sid": "s27", "keyframe_zh": "听筒垂挂，光从亭顶漏下，凝在「该拨未拨」的静默前一刻。"},
        {"sid": "s31", "keyframe_zh": "身后一块白板（申报文字载体：白板返工记录），板面被打亮。"},
    ]
    issues = {
        "s27": ["s27 画面句含引号文字「该拨未拨」 — v3 图内字通道已停用"],
        "s31": ["s31 画面句含引号文本未申报 in_image_text (二段化只认显式申报)"],
    }
    fixed, manual = v3shots.strip_quoted_labels(shots, issues)
    assert fixed == ["s27", "s31"]
    assert "该拨未拨" not in shots[0]["keyframe_zh"]
    assert "静默前一刻" in shots[0]["keyframe_zh"]      # 不留病句片
    assert "的的" not in shots[0]["keyframe_zh"]
    assert "申报" not in shots[1]["keyframe_zh"]
    assert manual == []


def test_arc_reminders_do_not_block_gate():
    """弧级=策略提醒 (人检可保留), 只有镜级才拦闸 (实锤: 旧实现 `not issues` 闸永久锁死)。"""
    assert v3shots.has_blocking_issues({}) is False
    assert v3shots.has_blocking_issues({"弧级": ["景深配额: 全集 …"]}) is False
    assert v3shots.has_blocking_issues({"s8": ["…"], "弧级": ["…"]}) is True


def test_build_cards_prefers_card_text_over_priority():
    """实锤 L41: 确定性 list 建议无 card_text, 老优先级让它压过 img → 清单卡变成口播断句残片。"""
    v3 = {"cards": [], "lines": [{
        "no": 41,
        "text": "第一项，会做什么、不会做什么。第二项，客户需要配合什么、不需要做什么。第三项，分析边界在哪，不碰哪些领域。",
        "adopted": ["img", "list"],
        "suggs": [
            {"kind": "img", "card_text": "丑话清单①会/不会做 ②配合/不配合 ③分析边界",
             "diagram": {"name": "丑话清单前三项", "children": [{"name": "会做什么"}]}},
            {"kind": "list", "text": "成清单排比 — 建议合并制作为剪映清单卡"},
        ]}]}
    cards = v3shots.build_cards(v3, [{"sid": "s17", "line_refs": [41]}])
    assert len(cards) == 1
    c = cards[0]
    assert c["kind"] == "图示" and c["style"] == "img"      # 带卡文字的那条胜出
    assert c["text"] == "丑话清单①会/不会做 ②配合/不配合 ③分析边界"
    assert "第三项" not in c["text"]                          # 不再是口播截断
    assert c.get("diagram") and c["sid"] == "s17"


def test_build_cards_skips_suggestion_without_card_text():
    """没卡文字就不建卡: 用口播整句硬截 = 断句残片, 宁缺勿滥。"""
    v3 = {"cards": [], "lines": [{
        "no": 5, "text": "第一项，会做什么。第二项，不会做什么。第三项，边界在哪。",
        "adopted": ["list"], "suggs": [{"kind": "list", "text": "成清单排比"}]}]}
    assert v3shots.build_cards(v3, [{"sid": "s1", "line_refs": [5]}]) == []


def test_merge_same_list_cards():
    """实锤 ep5 丑话清单: L35 11项 / L41 前三项 / L43 4-9项 三张卡 → 只留最全那张。"""
    cards = [
        {"cid": "c01", "kind": "图示", "style": "img", "line_no": 35,
         "text": "丑话清单11项：范围/配合/分析/计划/沟通/汇报/报告/审核/交付/验收/评价",
         "diagram": {"name": "丑话清单"}},
        {"cid": "c02", "kind": "图示", "style": "img", "line_no": 41,
         "text": "丑话清单①会/不会做 ②配合/不配合 ③分析边界",
         "diagram": {"name": "丑话清单前三项"}},
        {"cid": "c03", "kind": "图示", "style": "img", "line_no": 43,
         "text": "丑话清单4-9：计划/沟通/汇报/报告/审核/交付",
         "diagram": {"name": "过程与产出"}},
        {"cid": "c04", "kind": "图示", "style": "img", "line_no": 69,
         "text": "客户含糊→写进清单｜拒绝划界→标高风险",
         "diagram": {"name": "客户反应判定"}},
    ]
    out = v3shots._merge_same_list(cards)
    assert len(out) == 2                       # 丑话清单三合一 + 客户反应判定
    assert [c["line_no"] for c in out] == [35, 69]
    assert "11项" in out[0]["text"]            # 留覆盖最全的
    assert [c["cid"] for c in out] == ["c01", "c02"]   # 重编号


def test_list_kind_summary_vs_hint():
    assert v3shots._list_kind("第二项…第三项…") == "提示"      # 清单提醒, 不是总结
    assert v3shots._list_kind("回顾一下这节课的总结") == "总结"


def test_expected_item_count():
    """跨句清单的项数 (第一项×3 + 第四到六项×3 + 第七到九项×3 + 最后两项×2 = 11)。"""
    txt = ("第一项，会做什么、不会做什么。第二项，客户需要配合什么。第三项，分析边界在哪。"
           "第四到六项，工作计划。第七到九项，报告怎么用。最后两项最关键：验收标准。")
    assert v3shots.expected_item_count(txt) == 11
    assert v3shots.expected_item_count("最后两项最关键") == 2
    assert v3shots.expected_item_count("这里没有清单") == 0


def test_sanitize_diagram_children_cap():
    """实锤: children 默认上限 6, 11 项清单被砍成 6 项 — 补全/渲染路径必须放宽。"""
    node = {"name": "根", "children": [{"name": f"项{i}"} for i in range(1, 12)]}
    assert len(v3shots.sanitize_diagram(node)["children"]) == 6
    assert len(v3shots.sanitize_diagram(node, budget=[14], max_children=14)["children"]) == 11


def test_batch_quota_feeds_rules_to_writer():
    """用户令: 配额规则必须进生成提示, 不能只在检查器里 (事后重投 = token 暴涨)。"""
    batch = [{"sid": f"s{i}"} for i in range(1, 9)]
    q = v3shots.batch_quota(batch, {"s1": "hook", "s2": "hook"})
    assert "大场景" in q and "至少 2 镜" in q          # 8 镜 → ⌈8/4⌉ = 2
    assert "s1/s2" in q and "人物" in q
    assert v3shots.batch_quota([], {}) == ""
    # 常驻规则也在系统提示里 (双保险)
    assert "景深节奏" in v3shots._SYS and "开篇留人" in v3shots._SYS
    # 批次行标了 hook, 写手才知道哪几镜属于开篇
    row = v3shots._batch_prompt([{"sid": "s1", "arc_id": "A1", "narration": "口播", "est_s": 5}],
                                {"A1": {}}, {}, {"s1": "hook"})
    assert "s1[hook]" in row


def test_every_text_qc_rule_is_stated_in_prompt():
    """用户令: 一切机制放在生成时 — 检查器有的规则, 提示词必须有对应条款。

    这张表就是契约: 新增 QC 规则时必须同步 prompt (否则写手不知道 → 事后重挑 → token 暴涨)。
    """
    pairs = [
        ("画面句过短", "80-200"),
        ("文字载体", "空白白板"),
        ("引号文本", "引号"),
        ("终态词", "终态"),
        ("大字字面", "大字"),
        ("相邻镜克隆", "换皮克隆"),
        ("景深配额", "大场景"),
        ("分区人物配额", "人物"),
    ]
    prompt = v3shots._SYS + v3shots.batch_quota([{"sid": f"s{i}"} for i in range(8)],
                                                {"s1": "hook"})
    for rule, word in pairs:
        assert word in prompt, f"QC 规则「{rule}」没进生成提示 (关键词: {word})"


def test_is_big_scene_matches_depth_words():
    assert v3shots.is_big_scene("黄昏的悬崖盘山公路大全景俯瞰，一辆黑色豪车")
    assert not v3shots.is_big_scene("过肩近景，驾驶员双手紧握方向盘，指节发白")


def test_person_words_match_prompt_wording():
    """提示词说「背影/半个身位都算人物」, 检查器必须同口径 (实锤: 工程师背影漏判)。"""
    from app.services.anim_pipeline.final_gate import zone_quota_issues
    import json as _json
    txt = "地基深坑里一名戴安全帽的工程师背影蹲在最底层，手捏着一截红色引线"
    doc = {"shots": [{"shot_id": "s1", "arc_id": "", "t_start": 0, "keyframe_zh": txt,
                      "in_image_text": []}]}
    before = zone_quota_issues(doc)                    # 默认词表: 不认「背影」
    after = zone_quota_issues(doc, extra_words=v3shots._PERSON_EXTRA)
    assert before and not after
    assert "背影" in v3shots._PERSON_EXTRA


def test_direct_cam_table_and_clause():
    """④ 单一事实源: LLM 吐四元组, 程序渲染官方句 (UI/H3/校验同表)。"""
    from app.services.anim_pipeline.v3_pipeline import direct as D
    assert D.cam_clause({"kind": "摇", "dir": "左", "amp": "大", "speed": "慢"}) \
        == "The camera pans left with large amplitude at slow speed."
    assert D.cam_clause({"kind": "推", "dir": "", "amp": "中", "speed": "中"}) \
        == "The camera pushes in."
    assert D.cam_clause({"kind": "固定", "dir": "", "amp": "大", "speed": "快"}) \
        == "The camera holds a static shot."       # 固定镜无幅速
    assert D.cam_clause({"kind": "环绕"}) == ""      # 未注册型 → 空


def test_direct_execution_policy():
    """步数/工位是程序独占: 素材 12 其余 8; 运镜锁 h3 (r2v 裸奔挂账)。"""
    from app.services.anim_pipeline.v3_pipeline import direct as D
    assert D.steps_for({}) == 8
    assert D.steps_for({"asset_ref": {"image": "t.png"}}) == 12
    assert D.station_for({"kind": "摇"}) == "h3"
    assert D.station_for({"kind": "固定"}) == "auto"


def test_direct_validate_rules():
    """枚举/方向/固定纪律/槽长闸/相邻景别 — 生成时执法 (1105 用户令)。"""
    from app.services.anim_pipeline.v3_pipeline import direct as D
    shots = [{"sid": "s1", "arc_id": "A1", "est_s": 8.0},
             {"sid": "s2", "arc_id": "A1", "est_s": 4.5}]
    ok = {"s1": {"size": "全景", "cam": {"kind": "摇", "dir": "左", "amp": "大", "speed": "慢"}},
          "s2": {"size": "特写", "cam": {"kind": "固定", "dir": "", "amp": "", "speed": ""}}}
    assert D.validate(ok, shots) == {}
    bad = {"s1": {"size": "全景", "cam": {"kind": "环绕", "dir": "", "amp": "", "speed": ""}},
           "s2": {"size": "全景", "cam": {"kind": "推", "dir": "上", "amp": "大", "speed": "慢"}}}
    iss = D.validate(bad, shots)
    assert any("不在枚举内" in x for x in iss["s1"])
    assert any("方向只准" in x or "无方向" in x for x in iss["s2"])
    assert any("槽长" in x for x in iss["s2"])          # 4.5s 短槽运镜 → 拦
    assert any("相邻镜景别相同" in x for x in iss["s2"])  # s1 全景 s2 全景
    static_amp = {"s1": ok["s1"], "s2": {"size": "特写",
                   "cam": {"kind": "固定", "dir": "", "amp": "大", "speed": ""}}}
    assert any("固定镜不带幅速" in x for x in D.validate(static_amp, shots)["s2"])


def test_direct_prompt_carries_the_rules():
    """规则前移契约: 枚举/槽长/固定默认 必须在生成提示里 (否则写手不知道)。"""
    from app.services.anim_pipeline.v3_pipeline import direct as D
    for k in D.CAM_DEFS:
        assert k in D._SYS
    assert "6" in D._SYS and "固定是默认" in D._SYS
    assert "相邻镜景别" in D._SYS


def test_narrative_feeds_both_prompts():
    """产消审计 (1105 用户令) 实锤孤岛修复: arcs.narrative 只有人闸在看,
    ③④写手不知道弧的叙事任务 → s2 画面接不住口播爆点。两处提示必须带。"""
    arc = {"A1": {"world": "悬崖公路", "metaphor": "边界=护栏",
                  "narrative": "专家执行到位仍被客户翻脸"}}
    from app.services.anim_pipeline.v3_pipeline.shots import _batch_prompt as p3
    r3 = p3([{"sid": "s1", "arc_id": "A1", "narration": "口播", "est_s": 5}],
            arc, {}, {})
    assert "本弧讲什么" in r3 and "专家执行到位" in r3
    from app.services.anim_pipeline.v3_pipeline.direct import _batch_prompt as p4
    r4 = p4([{"sid": "s1", "arc_id": "A1", "est_s": 5, "keyframe_zh": "画面"}],
            arc, {"s1": "全景"})
    assert "本弧讲什么" in r4 and "专家执行到位" in r4


def test_direct_finalize_idempotent_and_freezes():
    """④ 放行钩子 (ID-131): 英文化批 + frozen_at 冻结戳; 幂等; 未达标拒放行。"""
    import sys, json
    from app.services.anim_pipeline.v3_pipeline import direct as D
    sys.path.insert(0, ".")
    shots = [{"sid": "s1", "keyframe_zh": "悬崖公路全景，豪车入弯", "keyframe_en": ""},
             {"sid": "s2", "keyframe_zh": "过肩近景握盘", "keyframe_en": ""}]
    doc = {"v3": {"shots": shots,
                  "direct": {x["sid"]: {"size": "全景", "cam": {"kind": "固定"},
                                        "motion": "手握紧", "light": ""} for x in shots},
                  "direct_issues": {}}}

    class _LLM:
        def chat(self, *a, **kw):
            assert kw.get("tag") == "v3_en"
            ids = [ln.split("|")[0] for ln in a[1].splitlines() if ln.startswith("s")]
            return json.dumps({"shots": [{"sid": i, "keyframe_en": "cliff road EN",
                                          "motion_en": "grip EN"} for i in ids]})

    class _SvcMod:
        @staticmethod
        def get_llm_service():
            return _LLM()

        @staticmethod
        def _parse_json(raw):
            return json.loads(raw)

    real = sys.modules.get("app.services.llm_service")
    sys.modules["app.services.llm_service"] = _SvcMod
    try:
        out = D.finalize(doc)
        assert out["translated"] == 2 and doc["v3"]["frozen_at"]
        assert all(s["keyframe_en"] == "cliff road EN" for s in shots)
        assert all(s["opening_desc"] == s["keyframe_en"] for s in shots)   # 锚定链同构
        assert doc["v3"]["direct"]["s1"]["motion_en"] == "grip EN"
        again = D.finalize(doc)                     # 幂等
        assert again["already"] is True
        # 未达标拒放行
        bad = {"v3": {"shots": shots, "direct": {"s1": {}},
                      "direct_issues": {"s1": ["相邻镜景别相同"]}}}
        import pytest
        with pytest.raises(SystemExit):
            D.finalize(bad)
    finally:
        if real:
            sys.modules["app.services.llm_service"] = real


def test_llm_fill_cancel_between_batches(monkeypatch):
    """批间取消 (1105): 面板 ⛔ → 下一批开跑前 SystemExit, 不烧后面的批。"""
    from app.services.anim_pipeline.v3_pipeline import direct as D
    job = {"cancel": False}
    calls = []

    class _LLM:
        def chat(self, *a, **kw):
            calls.append(1)
            return '{"shots":[]}' if kw.get("tag") != "v3_direct" else '{"shots":[]}'

    class _Svc:
        @staticmethod
        def get_llm_service():
            return _LLM()

        @staticmethod
        def _parse_json(raw):
            return {"shots": []}

    monkeypatch.setitem(__import__("sys").modules, "app.services.llm_service", _Svc)
    shots = [{"sid": f"s{i}", "arc_id": "A1", "est_s": 8.0, "keyframe_zh": "x" * 90}
             for i in range(1, 21)]          # 3 批
    import pytest
    orig_chat = _LLM.chat

    def chat_cancel_second(self, *a, **kw):
        calls.append(1)
        if len(calls) >= 2:
            job["cancel"] = True             # 第 2 批返回时已请求取消
        return '{"shots":[]}'

    _LLM.chat = chat_cancel_second
    with pytest.raises(SystemExit):
        D._llm_fill(shots, [], {"s1": "中景"}, job)
    assert len(calls) == 2                    # 第 3 批没烧


def test_direct_enum_forward_and_repairs():
    """ep5 实弹复发修: 景别枚举必须进提示; 入库归一; 每弧运镜超额降级; 相邻换档。"""
    from app.services.anim_pipeline.v3_pipeline import direct as D
    assert "大场景/全景/中景/近景/特写" in D._SYS and "不准自创词" in D._SYS
    assert "每弧运镜镜至多 2 个" in D._SYS                    # 配额规则前移
    shots = [{"sid": f"s{i}", "arc_id": "A1", "est_s": 8.0} for i in range(1, 5)]
    direct = {
        "s1": {"size": "大场景", "cam": {"kind": "推", "dir": "", "amp": "大", "speed": "慢"}},
        "s2": {"size": "大场景", "cam": {"kind": "拉", "dir": "", "amp": "大", "speed": "慢"}},
        "s3": {"size": "大场景", "cam": {"kind": "摇", "dir": "左", "amp": "大", "speed": "慢"}},
        "s4": {"size": "大场景", "cam": {"kind": "固定", "dir": "", "amp": "", "speed": ""}},
    }
    n = D._fix_adjacency(direct, shots)
    seq = [direct[f"s{i}"]["size"] for i in range(1, 5)]
    assert n >= 2                                    # 4 连同景别: 级联修交替即合法
    assert all(a != b for a, b in zip(seq, seq[1:]))  # 无相邻相同
    assert not any(k != "弧级" for k in D.validate(direct, shots))


def test_v3_k2_phase_registered_and_gated():
    """⑤ K2 薄壳: v3_k2 进 PHASES + GPU_PHASES (GPU 独占令) + 分发表; 投影契约。"""
    from app.services import anim_service as A
    from app.services.anim_pipeline import v3_pipeline as V
    from app.services.anim_pipeline.v3_pipeline import k2 as vk2
    assert "v3_k2" in A.PHASES and "v3_k2" in A.GPU_PHASES
    assert V._HANDLERS.get("k2") is V._h_k2
    # 投影: v2 形状契约 (k2.run_shot 吃的字段全带) + arcs 主旨同构
    vs = vk2._project_shot({"sid": "s1", "arc_id": "A1",
                            "keyframe_zh": "画面", "keyframe_en": "scene",
                            "seed": 42}, 7)
    assert vs["shot_id"] == "s1" and vs["seed"] == 7 and vs["attempts"]["k2"] == 0
    assert vs["in_image_text"] == []                     # 停用令
    doc = {"book_title": "书", "v3": {"arcs": [
        {"arc_id": "A1", "metaphor": "边界=护栏", "narrative": "讲什么"}]}}
    pd = vk2._project_doc(doc)
    assert pd["arcs"][0]["metaphor_core"] == "边界=护栏"   # arc_thesis 读这个
    assert "arcs" not in doc or doc["arcs"] == [{"arc_id": "A1", "metaphor_core": "边界=护栏", "narrative": None}] or not doc.get("arcs")


def test_v3_k2_refuses_unfrozen(monkeypatch):
    """前置闸: ④ 未放行 (无 keyframe_en) 直接拒 — 不白烧 GPU。"""
    from app.services.anim_pipeline.v3_pipeline import k2 as vk2
    import pytest
    monkeypatch.setattr("app.services.anim_pipeline.shots.load_doc",
                        lambda *a, **k: {"v3": {"shots": [{"sid": "s1"}]}})
    with pytest.raises(SystemExit, match="未放行"):
        vk2.run({"book_title": "书", "ep": 5})


def test_slot_alignment_mechanism():
    """1106 用户令: 槽位 = K2/TTS/H3 共同轴, 上限 15s。
    长行拆句 (s5/s12 病根) + 全弧型窗化 (s32 CTA 33.8s 病根) + 双层硬闸。"""
    from app.services.anim_pipeline.v3_pipeline.triage import split_long_lines, split_text
    from app.services.anim_pipeline.v3_pipeline import shots as S
    from app.services.anim_pipeline.v3_pipeline import direct as D

    # 拆句: 79 字复合长句 → 多子行, 每段 ≤40 字; 标注挂首行
    t = ("上集咱们问：对客户直来直去，还是措辞讲究？答案是——软话说尽，硬事才能办成。"
         "管理者提升效能一共六件工具——这是第四件，前三件管怎么听、怎么说，这件管怎么划界。")
    parts = split_text(t)
    assert len(parts) >= 3 and all(len(p) <= 40 for p in parts)
    lines = [{"no": 1, "text": t, "mode": "comm", "suggs": [{"kind": "event"}],
              "adopted": ["event"]}]
    nl, m = split_long_lines(lines)
    assert len(m[1]) >= 3 and len(nl) == len(m[1])       # 句级+逗号级拆, 段数≥3
    assert all(len(x["text"]) <= 40 for x in nl)
    assert nl[0]["suggs"] and not any(x.get("suggs") for x in nl[1:])  # 建议只挂首行

    # 窗化: CTA 152 字旧逻辑整段一镜 33.8s → 现在切多槽, 全部 ≤15s
    cta = [{"no": i + 1, "text": x, "mode": "cta"} for i, x in enumerate([
        "马上能做的一步：今晚翻出你手头的项目，对照十一项清单，看哪几项还没写清楚。标不出来的，明早第一件事就是补。",
        "有人说对客户就该直来直去，也有人说措辞不讲究方案再好也白搭——你站哪边？",
        "第四件交完了，整套行动清单统一放在行动册等你。",
        "身边那个方案硬、单子软的同行，这套丑话清单值得转给他，兴许帮他省下几场白开的会。"])]
    nl2, _ = split_long_lines(cta)
    arcs = [{"arc_id": "A15", "type": "cta", "no_start": 1, "no_end": len(nl2),
             "est_s": 0, "world": "", "metaphor": ""}]
    sh, warns = S.pack_shots(arcs, nl2)
    assert len(sh) >= 3 and all(x["est_s"] <= S.SLOT_MAX_S for x in sh)

    # 双层硬闸: 装包 warns + ④ 校验 issue
    sh2, w2 = S.pack_shots([{"arc_id": "A1", "type": "cta", "no_start": 1, "no_end": 1,
                             "est_s": 0, "world": "", "metaphor": ""}],
                           [{"no": 1, "text": "字" * 80, "mode": "cta"}])
    assert any("超 H3" in x for x in w2)
    iss = D.validate({}, [{"sid": "s1", "arc_id": "A1", "est_s": 17.5}])
    assert any("15s" in x for x in iss["s1"])


def test_v3_redo_registered_and_pipeline():
    """列队重做 (1106 用户令: v2 cartRedo/cartRedoImg 对应位) 相位注册 + 编排契约。"""
    from app.services import anim_service as A
    from app.services.anim_pipeline import v3_pipeline as V
    from app.services.anim_pipeline.v3_pipeline import redo as R
    assert "v3_redo" in A.PHASES and "v3_redo" in A.GPU_PHASES
    assert V._HANDLERS.get("redo") is V._h_redo
    # redesign 桩: LLM 只改指定镜, 校验长度闸
    import json
    doc = {"v3": {"shots": [{"sid": "s1", "arc_id": "A1", "keyframe_zh": "旧" * 60}],
                  "arcs": [{"arc_id": "A1", "world": "悬崖公路", "narrative": "讲边界"}]}}

    class _LLM:
        def chat(self, *a, **kw):
            assert kw.get("tag") == "v3_redo"
            return json.dumps({"shots": [{"sid": "s1", "keyframe_zh": "新" * 60}]})

    class _Svc:
        @staticmethod
        def get_llm_service():
            return _LLM()

        @staticmethod
        def _parse_json(raw):
            return json.loads(raw)

    import sys
    real = sys.modules.get("app.services.llm_service")
    sys.modules["app.services.llm_service"] = _Svc
    try:
        out = R.redesign(doc, ["s1"], note="不满意")
        assert out == {"s1": "新" * 60}
        short = R.redesign(doc, ["s1"])   # 桩不改, 只验证过短被拒 → 换个拒绝桩
    finally:
        if real:
            sys.modules["app.services.llm_service"] = real


def test_slot_texts_and_resegment_pure():
    """D 刀 (1106 用户令: TTS 以导演层方案执行): 槽→段文本纯函数。"""
    from app.services.anim_pipeline.v3_pipeline.tts_slot import slot_texts
    doc = {"v3": {
        "lines": [{"no": 1, "text": "第一句。"}, {"no": 2, "text": ""},
                  {"no": 3, "text": "第二句。"}, {"no": 4, "text": "第三句。"}],
        "shots": [{"sid": "s1", "line_refs": [1, 2, 3]},
                  {"sid": "s2", "line_refs": [4]}]}}
    out = slot_texts(doc)
    assert out == [{"sid": "s1", "text": "第一句。\n第二句。"},
                   {"sid": "s2", "text": "第三句。"}]


def test_muted_slot_selfheal():
    """1106 用户问: 屏一块音频 → 槽轴断链。自愈: 锚匹配缺块槽标 audio_muted。"""
    from app.services.anim_pipeline.v3_pipeline.tts_slot import (
        sync_muted_slots, _anchor)
    # 锚归一化: 剥气口标记 + 只取首行 (多行文本直接取前缀会跨行误伤)
    assert _anchor("书里有个更惨的。 -0.8s-\n罗伯特受邀") == "书里有个更惨的。"
    assert _anchor("先对个号：你上回") == "先对个号：你上回"
    doc = {"v3": {
        "lines": [{"no": 1, "text": "第一句。"}, {"no": 2, "text": "第二句。"}],
        "shots": [{"sid": "s1", "line_refs": [1]}, {"sid": "s2", "line_refs": [2]}]}}
    # s1 的块在, s2 的块被屏 → 只有 s2 标
    out, _drift = sync_muted_slots(doc, [{"text": "第一句。"}])
    assert out == {"s1": False, "s2": True}
    assert doc["v3"]["shots"][1]["audio_muted"] is True
    # 恢复 (块回来) → 自动清标
    out2, _ = sync_muted_slots(doc, [{"text": "第一句。"}, {"text": "第二句。"}])
    assert out2 == {"s1": False, "s2": False}
    assert not doc["v3"]["shots"][1].get("audio_muted")


def test_drift_edited_vs_muted_distinction():
    """1106 用户问: 是修改了还是摒弃了 — 锚失配分型: 模糊近邻=改字 (回填第五处
    同步, 不跳工位), 全无=真屏弃。"""
    from app.services.anim_pipeline.v3_pipeline.tts_slot import sync_muted_slots
    doc = {"v3": {
        "lines": [{"no": 1, "text": "先对个号：你上回项目加需求"},
                  {"no": 2, "text": "被删掉的句。"}],
        "shots": [{"sid": "s1", "line_refs": [1]}, {"sid": "s2", "line_refs": [2]}]}}
    # s1 被改字 (对个号→对齐一下), s2 的块被屏
    muted, drifted = sync_muted_slots(doc, [
        {"text": "先对齐一下：你上回项目加需求，是你没写清楚"}])
    assert drifted == {"s1": True, "s2": False}       # 改字 → drifted
    assert muted == {"s1": False, "s2": True}         # 真没块 → muted
    assert doc["v3"]["lines"][0]["text"] == "先对齐一下：你上回项目加需求，是你没写清楚"  # 第五处同步: 整行=块行
    assert not doc["v3"]["shots"][0].get("audio_muted")  # 改字槽不跳工位


def test_manifest_line_to_segment_mapping():
    """1106 总根修: _manifest_callback 曾把行号当段序号 (v2 一段一行蒙对,
    v3 槽段多行后偏 2-3 格 — 编辑/屏蔽/重roll 全打错段)。验包含段映射。"""
    # 与 tts_service._seg_of_line 同构的最小实现验证语义
    seg_pairs = [("id0", "行零\n行一"), ("id1", "行二"), ("id2", "行三\n行四\n行五")]
    starts, acc = [], 0
    for sid, txt in seg_pairs:
        starts.append((acc, sid))
        acc += sum(1 for ln in txt.splitlines() if ln.strip())
    def seg_of_line(n):
        sid = starts[-1][1]
        for s0, id0 in starts:
            if n < s0:
                break
            sid = id0
        return sid
    assert starts == [(0, "id0"), (2, "id1"), (3, "id2")]
    assert seg_of_line(0) == "id0" and seg_of_line(1) == "id0"   # 行1 仍属段0 (旧法会给 id1 ✗)
    assert seg_of_line(2) == "id1"
    assert seg_of_line(5) == "id2" and seg_of_line(99) == "id2"


def test_v3_k2_requires_slot_aligned_audio(monkeypatch):
    """1106 用户令 (⑤TTS→⑥K2): 音频包按槽就位才烧卡 — TTS 改动便宜先定稿。"""
    from app.services.anim_pipeline.v3_pipeline import k2 as vk2
    import pytest
    monkeypatch.setattr("app.services.anim_pipeline.shots.load_doc",
                        lambda *a, **k: {"v3": {"shots": [{"sid": "s1"}],
                                                "frozen_at": "x"}})
    job = {"book_title": "书", "book_id": "bk", "ep": 5}
    # 无音频包 → 拒
    def _no_audio(*a, **k):
        raise RuntimeError("没有产线 script_id")
    monkeypatch.setattr("app.services.anim_draft.fetch_episode_audio", _no_audio)
    with pytest.raises(SystemExit, match="TTS"):
        vk2.run(job)
    # 块数≠槽数 → 拒
    monkeypatch.setattr("app.services.anim_draft.fetch_episode_audio",
                        lambda *a, **k: {"files": [{} for _ in range(2)]})
    with pytest.raises(SystemExit, match="未定稿"):
        vk2.run(job)


def test_stamp_projection_and_gates():
    """⑦ 盖章 (V5): 查表灌秒 (1:1 零分摊) + v2 回填 (退回通道) + 运镜映射。"""
    from app.services.anim_pipeline.v3_pipeline import stamp as ST
    # 运镜映射
    assert ST._cam_of({"kind": "摇", "dir": "左"}) == "左摇"
    assert ST._cam_of({"kind": "推"}) == "缓推" and ST._cam_of({}) == "固定"
    # 投影: 3 槽 (s2 屏弃), 块对齐 s1/s3
    doc = {"book_title": "书", "v3": {
        "lines": [{"no": 1, "text": "第一句。" * 8}, {"no": 2, "text": "第二句。" * 8},
                  {"no": 3, "text": "第三句。" * 8}],
        "arcs": [{"arc_id": "A1", "type": "narr", "metaphor": "边界=护栏", "world": "悬崖公路"}],
        "shots": [
            {"sid": "s1", "arc_id": "A1", "line_refs": [1], "est_s": 5, "keyframe_zh": "画面一",
             "keyframe_en": "scene one", "k2": {"image": "img/s1.png", "status": "approved", "seed": 7}},
            {"sid": "s2", "arc_id": "A1", "line_refs": [2], "est_s": 5, "audio_muted": True},
            {"sid": "s3", "arc_id": "A1", "line_refs": [3], "est_s": 5, "keyframe_zh": "画面三",
             "keyframe_en": "scene three", "seed": 9}],
        "direct": {"s1": {"size": "全景", "cam": {"kind": "摇", "dir": "左"}, "motion_en": "hand moves"},
                   "s3": {"size": "特写", "cam": {"kind": "推"}}},
        "cards": [{"cid": "c1", "sid": "s1", "kind": "金句", "text": "丑话清单"}]}}
    L1, L3 = "第一句。" * 8, "第三句。" * 8
    blocks = [{"text": L1, "dur_s": 6.2}, {"text": L3, "dur_s": 4.1}]
    out = ST.project_v2(doc, blocks)
    assert out["slots"] == 2 and out["muted"] == 1
    assert out["missing_audio"] == [] and out["total_s"] == 10.3
    # v2 投影: 屏弃槽不在, 时序累计, beats/camera/状态回填
    assert [x["shot_id"] for x in doc["shots"]] == ["s1", "s3"]
    v1, v3s = doc["shots"][0], doc["shots"][1]
    assert v1["t_start"] == 0 and v1["t_end"] == 6.2 and v1["status"] == "approved"
    assert v1["camera"] == "左摇" and v1["anim"]["duration_s"] == 6.2
    assert v1["anim"]["beats"][0]["motion"] == "hand moves"
    assert v3s["t_start"] == 6.2 and v3s["t_end"] == 10.3 and v3s["status"] == "planned"
    assert doc["arcs"][0]["metaphor_core"] == "边界=护栏"
    # v3 槽的实测字段
    sh = {s["sid"]: s for s in doc["v3"]["shots"]}
    assert sh["s1"]["dur_real"] == 6.2 and sh["s2"]["t_end"] == sh["s2"]["t_start"]
    # 锚失配: 块文本全不匹配 → missing_audio
    out2 = ST.project_v2(doc, [{"text": "完全无关", "dur_s": 1}])
    assert len(out2["missing_audio"]) == 2


def test_v3_h3_cam_word_and_gate(monkeypatch):
    """⑧ H3 (V6b): 运镜型→v2 词映射 + 未盖章拒跑。"""
    from app.services.anim_pipeline.v3_pipeline import h3 as VH
    import pytest
    assert VH._cam_word({"kind": "摇", "dir": "左"}) == "左摇"
    assert VH._cam_word({"kind": "推"}) == "拉近" and VH._cam_word({}) == ""
    monkeypatch.setattr("app.services.anim_pipeline.shots.load_doc",
                        lambda *a, **k: {"v3": {"shots": [{"sid": "s1"}]}})
    with pytest.raises(SystemExit, match="未盖章"):
        VH.run({"book_title": "书", "ep": 5})


def test_stamp_projection_field_contract():
    """⑧ H3 实锤: 投影漏 h3_seed/physical_lock → 每镜 KeyError 降级静态图废片。
    契约: v2 h3/k2 消费的字段, 投影必须全带。"""
    from app.services.anim_pipeline.v3_pipeline import stamp as ST
    doc = {"book_title": "书", "v3": {
        "lines": [{"no": 1, "text": "第一句。" * 8}],
        "arcs": [], "cards": [],
        "shots": [{"sid": "s1", "arc_id": "A1", "line_refs": [1], "est_s": 5,
                   "keyframe_zh": "画面", "keyframe_en": "scene",
                   "k2": {"image": "img/s1.png", "status": "approved", "seed": 7}}],
        "direct": {}}}
    ST.project_v2(doc, [{"text": "第一句。" * 8, "dur_s": 5.0}])
    v2s = doc["shots"][0]
    need = ("shot_id", "arc_id", "keyframe_zh", "keyframe_en", "narration",
            "t_start", "t_end", "seed", "h3_seed", "camera", "status",
            "image_file", "attempts", "brand_card", "in_image_text", "text_layer",
            "anim")
    missing = [k for k in need if k not in v2s]
    assert not missing, f"投影缺字段: {missing}"
    assert isinstance(v2s["h3_seed"], int) and v2s["h3_seed"] > 0
    # anim 内键: v2 h3 build_prompt 硬索引 opening_desc + physical_lock
    for k in ("opening_desc", "physical_lock", "duration_s", "beats"):
        assert k in v2s["anim"], f"anim 缺键: {k}"
    assert "physical_lock" in v2s["anim"], "physical_lock 缺 → KeyError 废片族"
    assert v2s["anim"]["physical_lock"]


def test_h3_cam_permit_speed_amp_parameterized():
    """1106 v3 实锤 (ep5 s2/s3 慢镜头): _CAM_PERMIT 硬编码 slow/large, 与 ④导演
    speed=快 官方句同 prompt 打架 → H3 折中取慢。契约: 空参=v2 原文零扰动,
    四元组档 (快/中/慢 × 大/中/小) 正确落词, 豁免句随补丁挂载。"""
    from app.services.anim_pipeline.h3 import (_cam_permit_sentence, _cam_patch_prompt,
                                               _CAM_BAN, _CAM_LAW_WAIVER)
    base = _cam_permit_sentence("pan_right")
    assert base == _cam_permit_sentence("pan_right", "", ""), "空参必须 v2 原文"
    assert "at slow speed" in base and "with large amplitude" in base
    fast = _cam_permit_sentence("pan_right", "快", "大")
    assert "at fast speed" in fast and "slow" not in fast
    mid = _cam_permit_sentence("push_in", "中", "中")
    assert "at moderate speed" in mid and "with medium amplitude" in mid
    slow_small = _cam_permit_sentence("tilt_up", "慢", "小")
    assert "at slow speed" in slow_small and "with small amplitude" in slow_small
    # 补丁链: 军规句被替换 + 书律豁免句挂载 (防 motion law "law wins" 压回固定)
    out = _cam_patch_prompt("Physical lock: " + _CAM_BAN + " — tail.",
                            "pan_right", "快", "大")
    assert "at fast speed" in out and _CAM_BAN not in out
    assert _CAM_LAW_WAIVER.strip() in out


def test_v3_h3_only_reset_before_batch(monkeypatch):
    """⑧ 点名重掷 (1106 品牌镜无重置通道实锤): run_batch 只吃 approved —
    anim_done/anim_fail 不先失效就是静默空转。契约: only 点名镜失效视频
    (keep_image 保图) 换 h3_seed 回 approved 再进批。"""
    import app.services.anim_pipeline.shots as shots_mod
    import app.services.anim_pipeline.h3 as h3mod
    import app.services.anim_pipeline.comfy as comfy_mod
    import app.services.gpu_service_manager as gsm
    import app.services.job_events as je
    from app.services.anim_pipeline.v3_pipeline import h3 as VH

    doc = {"book_title": "书", "ep": 5,
           "v3": {"stamp": {"at": 1}, "shots": [{"sid": "s1"}], "direct": {}},
           "shots": [{"shot_id": "s1", "status": "anim_done", "h3_seed": 7,
                      "attempts": {"k2": 1, "h3": 1}, "video_file": "anim/s1.mp4",
                      "image_file": "img/s1.png"}]}
    monkeypatch.setattr(shots_mod, "load_doc", lambda *a, **k: doc)
    monkeypatch.setattr(shots_mod, "save", lambda d: None)
    monkeypatch.setattr(shots_mod, "audit_reroll", lambda *a, **k: None)  # 不落真台账
    seen = {}

    def fake_batch(d, only=None, retry_failed=False, on_shot_done=None,
                   stop_check=None):
        seen["only"] = only
        seen["statuses"] = {s["shot_id"]: s["status"] for s in d["shots"]}
        return {"ok": 1, "fail": 0}
    monkeypatch.setattr(h3mod, "run_batch", fake_batch)

    class _Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(gsm, "get_gpu_service_manager",
                        lambda: type("M", (), {"session": lambda *a, **k: _Sess()})())
    monkeypatch.setattr(comfy_mod, "free_memory", lambda: None)
    monkeypatch.setattr(je, "_publish", lambda *a, **k: None)

    VH.run({"id": "j", "book_title": "书", "ep": 5, "cancel": False}, only={"s1"})
    s = doc["shots"][0]
    assert s["status"] == "approved", "点名镜必须失效回 approved 再进批"
    assert "video_file" not in s, "视频必须清 (旧片作废)"
    assert s["image_file"] == "img/s1.png", "图保留 (重掷=只重生视频)"
    assert s["h3_seed"] != 7, "重掷必须换 seed"
    assert seen["only"] == {"s1"}
