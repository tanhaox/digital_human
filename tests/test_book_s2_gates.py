# -*- coding: utf-8 -*-
"""S2 成稿结构闸 (1103): 概念覆盖(阻断) / 模块计数同轴(提示) / 钩子开环回扣(提示).

实锤锚: ep2 (c5148d33) 概念「包容的专业精神」核心词全稿 0 次; 「第一档×3+这一步」
档/步两轴并存; 钩子"答案藏在书里一句话里"全篇未揭晓。
模块名模板化检查已撤 — 「核心概念拆解（N）」是系统 _LABEL_BASE 默认标签,
0917 架构令明定"标签只是刻度", 该检查会每集恒报=噪音。
"""
from app.services.book_service.creation_common import (
    _listen_feel_issues,
    concept_coverage_issues,
    hook_position_issues,
)

EP2_CONCEPTS = ["技术至上论", "包容的专业精神", "排他的专业主义"]


# ── 概念覆盖闸 ──

def test_concept_coverage_blocks_missing_core():
    """ep2 实锤: 技术至上/排他的专业都落了, 唯独「包容的专业」0 次 → 阻断."""
    t = "这一集先讲技术至上的病，再讲排他的专业主义怎么把人挡在门外。"
    iss = concept_coverage_issues(t, EP2_CONCEPTS)
    assert len(iss) == 1 and "包容的专业" in iss[0] and "(阻断)" in iss[0], iss


def test_concept_coverage_all_present_passes():
    t = "技术至上是病，排他的专业主义是病灶，包容的专业才是解药。"
    assert concept_coverage_issues(t, EP2_CONCEPTS) == []


def test_concept_coverage_empty_concepts_passes():
    assert concept_coverage_issues("随便什么稿", []) == []
    assert concept_coverage_issues("随便什么稿", None) == []


def test_concept_coverage_suffix_normalized():
    """概念带类后缀, 稿里用核心词即算覆盖 (剥后缀比对)."""
    assert concept_coverage_issues("开会的本质是包容的专业姿态。", ["包容的专业精神"]) == []


def test_concept_coverage_glue_char_tolerance():
    """的/性 胶水字插拔属措辞自由 (ep2 回放实锤: 概念「排他性的专业主义」稿写「排他的专业主义»)."""
    assert concept_coverage_issues("这病叫排他的专业主义，把人挡在门外。", ["排他性的专业主义"]) == []


# ── 模块计数同轴 (_listen_feel 查④) ──

def test_module_count_axis_ep2_style_flagged():
    """ep2 实锤: 第X档×3 (结构性) + 这一步 → 档/步两轴."""
    t = "第一档，技术至上。信号对上一个就在往死胡同上走。第二档，低防御措辞。第三档，先付出。这一步的判定只有一条。"
    iss = _listen_feel_issues(t)
    assert any("模块计数不同轴" in i for i in iss), iss


def test_module_count_single_family_passes():
    t = "第一档，技术至上。第二档，低防御措辞。这一档的判定只有一条。"
    iss = [i for i in _listen_feel_issues(t) if "模块计数" in i]
    assert iss == [], iss


def test_module_count_series_word_not_counted():
    """系列词 第X件工具 不算模块计数族 — 与"第X档"并存不报."""
    t = "这套五件工具，今天是第2件工具。第一档，技术至上。第二档，低防御措辞。"
    iss = [i for i in _listen_feel_issues(t, series_word="五件工具") if "模块计数" in i]
    assert iss == [], iss


def test_module_count_content_singleton_not_flagged():
    """内容性单发提及 (非结构性枚举 ≥2) 不报 — 防误伤."""
    t = "先看第一步，把方案摆出来。再看该层逻辑顺不顺。"
    iss = [i for i in _listen_feel_issues(t) if "模块计数" in i]
    assert iss == [], iss


# ── 钩子开环回扣 (hook_position_issues ③) ──

_HOOK = "把自己活成百科全书的顾问，是客户最先淘汰的人"


def _hook_script(hook_body: str, rest: str) -> str:
    return (f"【0-25秒｜钩子】{hook_body}\n【25-50秒｜回顾+引入】{rest}\n"
            f"【50-105秒｜核心概念拆解（一）】正文块。\n【105-160秒｜核心概念拆解（二）】正文块。\n"
            f"【160-210秒｜核心概念拆解（三）】正文块。\n【210-270秒｜总结+下期预告】收尾。\n")


def test_hook_open_loop_unpaid_flagged():
    """ep2 实锤: 钩子承诺"答案藏在书里一句话里", 全篇无回扣."""
    t = _hook_script("为什么说，把自己活成百科全书的顾问，业绩比较低？答案藏在书里一句话里。",
                     "这集就给你补上后半截。")
    iss = hook_position_issues(t, _HOOK)
    assert any("开环未回扣" in i for i in iss), iss


def test_hook_open_loop_paid_passes():
    t = _hook_script("为什么说，把自己活成百科全书的顾问，业绩比较低？答案藏在书里一句话里。",
                     "这集就给你补上后半截。就是开头那句话——专业不是摆出来给人看的。")
    iss = [i for i in hook_position_issues(t, _HOOK) if "开环" in i]
    assert iss == [], iss


def test_hook_without_promise_passes():
    t = _hook_script("为什么说，把自己活成百科全书的顾问，业绩比较低？这集拆给你。",
                     "这集就给你补上后半截。")
    iss = [i for i in hook_position_issues(t, _HOOK) if "开环" in i]
    assert iss == [], iss


# ── 1103 U: 注释污染/轻归一/作为分段/系列词实测 ──

def test_concept_note_parenthesis_stripped():
    """总编剧概念带「（四维打分）」注解 — 剥注解后按正式名比对."""
    from app.services.book_service.creation_common import concept_speakable
    assert concept_speakable("信任等式（四维打分）") == "信任等式"
    assert concept_speakable("行动册装订（不引入新概念）") == "行动册装订"
    assert concept_speakable("责任归己转换") == "责任归己"


def test_coverage_keeps_deliverable_name():
    """交付物名 (信任等式) 不剥 — 全稿只写「信任」不算覆盖 (真失配不放走)."""
    iss = concept_coverage_issues("信任是分水岭，别的都是装饰。", ["信任等式（四维打分）"])
    assert len(iss) == 1 and "信任等式" in iss[0], iss


def test_coverage_zuowei_segment_fallback():
    """「自我导向作为分母」式描述性概念 — 作为切段最长段在稿即算."""
    assert concept_coverage_issues("这集盯死分母：自我导向。", ["自我导向作为分母"]) == []


def test_redup_enumerative_adverb_whitelisted():
    assert _listen_feel_issues("咱们一条一条讲，一项一项过。") == []


def test_ban_list_scoring_range_not_flagged():
    """1103 W: 打1到5分/打一遍是打分动作不是评论钩 — ban 误报修复 (ep2 两连实锤)."""
    from app.services.book_service.creation_common import validate_script
    iss = validate_script("翻出你最近3个客户，把四格各打1到5分，让他照着打一遍。评论区聊聊你最低那格。",
                          240.0, book_title="值得信赖的顾问")[1]
    assert not any("反套路" in i and "打" in i for i in iss), iss


def test_pause_mark_legit_not_flagged():
    """1103 W2: 手写气口 -Xs- 后贴汉字 = 合法 (ep2 听感手术实锤); 时序标记仍拦."""
    from app.services.book_service.creation_common import validate_script
    ok_legit = validate_script("可核心的会-0.3s-从不叫你。", 240.0, book_title="X")[1]
    assert not any("控制符" in i for i in ok_legit), ok_legit
    ok_time = validate_script("分段 -0.3s-A： 开场白。", 240.0, book_title="X")[1]
    assert any("控制符" in i for i in ok_time), ok_time


def test_subtitle_wash_strips_pause_and_pinyin():
    """1103 W2: 字幕层剥 -Xs- 与拼音标注 (观众只见裸字)."""
    from app.services.jy_draft_service.subtitle_text import wash_subtitle_text
    assert wash_subtitle_text("可核心的会-0.3s-从不叫你。全<中|ZHONG4>。") == "可核心的会从不叫你。全中。"
