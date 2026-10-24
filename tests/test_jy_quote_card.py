# -*- coding: utf-8 -*-
"""截屏卡检测 — 真实稿件用例 (ep2/ep3/ep4 卓有成效系列 + 负例)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.jy_draft_service.quote_card import (
    detect_quote_cards,
    detect_quote_cards_smart,
    is_list_card,
    is_pure_list_card,
    wash_card_lines,
)


def test_labeled_list_ep3_judgement():
    texts = ["怎么判定你干的事算不算贡献。", "给你三问，翻开账就能圈自己。",
             "一问：这成果最后给谁用？", "二问：谁能验证它有用？",
             "三问：我不做，会有什么后果？", "三问全答不上来，这活大概率是内部自转。"]
    cards = detect_quote_cards(texts)
    assert len(cards) == 1 and cards[0]["kind"] == "list"
    assert cards[0]["start"] == 2 and cards[0]["end"] == 5


def test_labeled_list_ep4_open_three():
    texts = ["第二件，开工前先问自己三句。",
             "第一句：这件事的成果长什么样？", "第二句：这份成果交给谁？",
             "第三句：他拿到手，会拿它去干什么？",
             "书里那个银行代理部的新经理，就是这么干的。"]
    cards = detect_quote_cards(texts)
    assert len(cards) == 1 and cards[0]["kind"] == "list"
    assert [t[:3] for t in cards[0]["lines"]] == ["第一句", "第二句", "第三句"]


def test_parallel_ep3_three_plain_lines():
    texts = ["翻成白话就三句。", "你做出的东西有人用；", "你带出来的标准别人照着做；",
             "你身边的人因为你长本事了。", "三样占一样，才叫有贡献。"]
    cards = detect_quote_cards(texts)
    par = [c for c in cards if c["kind"] == "parallel"]
    assert len(par) == 1 and par[0]["start"] == 1 and par[0]["end"] == 4


def test_aphorism():
    texts = ["做得专业不等于有价值。", "专业自嗨，是管理者最贵的一种浪费。",
             "贡献从来不是你觉得好，是外面有人接。"]
    cards = detect_quote_cards(texts)
    kinds = [c["kind"] for c in cards]
    assert kinds.count("aphorism") == 2


def test_negative_ep2_narration_not_card():
    # ep2 拆解一叙事三连 (11/15/16 字, 含逗号) — 不该误报 parallel
    texts = ["第一步，先记录，别急着分析。", "书里的逻辑很直接：人的记忆靠不住。",
             "凭印象报时间账，误差大到能骗过自己。", "操作方法就一条：当时记，不补记。"]
    cards = detect_quote_cards(texts)
    assert not any(c["kind"] == "parallel" for c in cards)
    assert not any(c["kind"] == "list" for c in cards)


def test_cap_four():
    texts = []
    for k in range(6):
        texts += [f"第{k}组开场句，讲一个完整的意思。",
                  "一问：这成果给谁用？", "二问：谁能验证？", "三问：不做会怎样？"]
    cards = detect_quote_cards(texts)
    assert len(cards) == 4  # 上限封顶


def test_labeled_list_ep6_comma_labels_and_leadin():
    # 1025 ep6 实锤: 逗号式标签 ("第一条，") + 首行报幕引子 ("给一张可截图模板：第一条，…")
    texts = ["不是“尽量做好”，是低于这条线，方案直接作废。",
             "给一张可截图模板：第一条，必须达成的结果：数字、时间、质量，写清楚。",
             "第二条，绝对不能牺牲的底线：成本、合规、体验，选最硬的。",
             "第三条，低于哪条直接作废：写一句“触线即否”。",
             "写不出来也没关系。"]
    cards = detect_quote_cards(texts)
    lists = [c for c in cards if c["kind"] == "list"]
    assert len(lists) == 1
    c = lists[0]
    assert c["start"] == 1 and c["end"] == 4
    assert c["lines"][0].startswith("第一条，")  # 引子已剥, 报幕不上卡
    assert [l[:3] for l in c["lines"]] == ["第一条", "第二条", "第三条"]


def test_mantra_ep6_two():
    texts = ["今天5分钟，分享一张能截图的“决策边界卡”：",
             "三问定性质，三线定底线，三案防翻车。",
             "可以先收藏，下次开会前30秒翻出来，能用上。",
             "整套东西，浓缩成一句口诀： -0.8s-",
             "先判性质，再钉三线，后找反方。"]
    cards = detect_quote_cards(texts)
    mantras = [c for c in cards if c["kind"] == "mantra"]
    assert len(mantras) == 2
    assert mantras[0]["lines"][0].startswith("三问定性质")
    assert mantras[1]["lines"][0].startswith("先判性质")


def test_mantra_negative_unequal_or_quoted():
    # ep6 负例: 长短不齐 (7/8/7) 与带引号等长 (6/6/6) 都不该成口诀卡
    texts = ["很多人天天救火，不一定是能力问题，往往是规则缺位。",
             "不是“尽量做好”，是低于这条线，方案直接作废。"]
    cards = detect_quote_cards(texts)
    assert not any(c["kind"] == "mantra" for c in cards)


# ── 1025 LLM 主判 (detect_quote_cards_smart): fake-LLM 三例 + real_api 冒烟 ──
import json

import pytest


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, system, user, **kw):
        return json.dumps(self.payload, ensure_ascii=False)


def test_smart_llm_ranges_verbatim_and_clamped(monkeypatch):
    texts = ["开场引入一句。", "三问定性质，三线定底线，三案防翻车。", "", "收尾一句。"]
    fake = {"cards": [
        {"start": 2, "end": 3, "kind": "mantra", "why": "口诀"},
        {"start": 1, "end": 5, "kind": "list", "why": "与上重叠应被丢"},
    ]}
    monkeypatch.setattr("app.services.llm_service.get_llm_service",
                        lambda: _FakeLLM(fake))
    cards = detect_quote_cards_smart(texts)
    assert len(cards) == 1
    c = cards[0]
    assert c["start"] == 1 and c["end"] == 2  # 编号1起→索引0起
    # 卡上文字 = texts 原文切片 (LLM 写的字一个不用)
    assert c["lines"] == ["三问定性质，三线定底线，三案防翻车。"]


def test_smart_llm_garbage_clamped(monkeypatch):
    texts = ["一问：这成果给谁用？", "二问：谁能验证？", "三问：不做会怎样？", "收尾。"]
    fake = {"cards": [
        {"start": 0, "end": 99, "kind": "list", "why": "越界夹取"},
        {"start": "x", "end": 2, "kind": "bad", "why": "非整数丢"},
        {"start": 4, "end": 5, "kind": "aphorism", "why": "字数不足丢"},
    ]}
    monkeypatch.setattr("app.services.llm_service.get_llm_service",
                        lambda: _FakeLLM(fake))
    cards = detect_quote_cards_smart(texts)
    assert len(cards) == 1
    assert cards[0]["start"] == 0 and cards[0]["end"] == 4  # start0→-1夹回0, end99夹到尾
    assert [l[:2] for l in cards[0]["lines"]] == ["一问", "二问", "三问", "收尾"]


def test_smart_llm_failure_or_empty_falls_back_to_regex(monkeypatch):
    texts = ["一问：这成果给谁用？", "二问：谁能验证？", "三问：不做会怎样？"]

    def _boom():
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.services.llm_service.get_llm_service", _boom)
    cards = detect_quote_cards_smart(texts)
    assert len(cards) == 1 and cards[0]["kind"] == "list"  # 异常回正则

    monkeypatch.setattr("app.services.llm_service.get_llm_service",
                        lambda: _FakeLLM({"cards": []}))
    cards = detect_quote_cards_smart(texts)
    assert len(cards) == 1 and cards[0]["kind"] == "list"  # 空判也回正则


@pytest.mark.real_api
def test_smart_real_ep6_texts_smoke():
    # ep6 真句节选 (两个设计截屏卡 + 口诀 + 断言), 真 LLM 实调
    texts = ["为什么发明了数码相机的柯达，最后被数码相机淘汰？",
             "结果不是没有Plan B，是不敢启用Plan B。",
             "今天5分钟，分享一张能截图的“决策边界卡”：",
             "三问定性质，三线定底线，三案防翻车。",
             "先说结论：决策不是找标准答案。",
             "给一张可截图模板：第一条，必须达成的结果：数字、时间、质量，写清楚。",
             "第二条，绝对不能牺牲的底线：成本、合规、体验，选最硬的。",
             "第三条，低于哪条直接作废：写一句“触线即否”。",
             "这张“三条线”，建议截图。",
             "没有B方案的计划，越精密越脆。",
             "整套东西，浓缩成一句口诀：",
             "先判性质，再钉三线，后找反方。",
             "我是老谭，拆一本书，多一个硬本事。"]
    cards = detect_quote_cards_smart(texts)
    assert 2 <= len(cards) <= 4  # 稿里至少两处明写的截屏卡
    for c in cards:  # 确定性闸: 区间合法 + 文字逐字原文
        assert 0 <= c["start"] < c["end"] <= len(texts)
        assert c["lines"] == [texts[k].strip() for k in range(c["start"], c["end"])]


def test_wash_card_lines_clean_screen_text():
    # 1025 验收实锤: 停顿标记/报幕引子/拼音/markdown 全剥, 洗后空行丢弃
    assert wash_card_lines(
        ["整套东西，浓缩成一句口诀： -0.8s-", "先判性质，再钉三线，后找反方。"]
    ) == ["先判性质，再钉三线，后找反方"]
    assert wash_card_lines(
        ["给一张可截图模板：第一条，必须达成的结果：数字、时间、质量，写清楚。"]
    ) == ["第一条，必须达成的结果：数字、时间、质量，写清楚"]
    assert wash_card_lines(
        ["可以拿这三问去聊：第一，如果这个方案失败，最可能死在哪？", "第二，谁最反对？"]
    ) == ["第一，如果这个方案失败，最可能死在哪？", "第二，谁最反对？"]
    assert wash_card_lines(
        ["**专业自嗨**，<是|shì>管理者最贵的一种浪费。"]
    ) == ["专业自嗨，是管理者最贵的一种浪费"]
    assert wash_card_lines(["-1.2s-"]) == []  # 洗后空 → 整卡跳过的判定依据
    # 1025 二轮: 句首报幕短语 (≤10字无逗号+冒号+有正文) 剥; 含逗号引导不动
    assert wash_card_lines(["记一句话就行：反复出现的，定规则；"]) == ["反复出现的，定规则；"]
    assert wash_card_lines(["先说结论：决策不是找标准答案。"]) == ["决策不是找标准答案"]
    assert wash_card_lines(["第一步，先判：这事是通例，还是特例？"]) == ["第一步，先判：这事是通例，还是特例？"]


def test_is_list_card_semantic_style_gate():
    # 1025 用户令: 分样式看语义不看行数 — 有报幕标签=清单卡(黑金6号), 无标签=金句(红锤8号)
    assert is_list_card(["第一条，必须达成的结果：数字、时间、质量，写清楚",
                         "第二条，绝对不能牺牲的底线：成本、合规、体验，选最硬的"])
    assert is_list_card(["一问：这成果给谁用？", "二问：谁能验证？"])
    assert not is_list_card(["三问定性质，三线定底线，三案防翻车"])  # "三问"后无分隔符
    assert not is_list_card(["反复出现的，定规则；", "偶尔发生的，才临时处置"])


def test_is_pure_list_card_size_tier():
    # 1025 用户令 B 案: 行行带标签=标准清单(6号); 带追问杂行=问句清单(5号)
    assert is_pure_list_card(["第一条，必须达成的结果：数字、时间、质量，写清楚",
                              "第二条，绝对不能牺牲的底线：成本、合规、体验，选最硬的"])
    assert not is_pure_list_card(["第一，如果这个方案失败，最可能死在哪？",
                                  "第二，谁最反对？",
                                  "他的理由能不能变成B方案？"])  # 杂行 → 5号档
    assert not is_pure_list_card(["三问定性质，三线定底线，三案防翻车"])  # 金句非清单


def test_style_label_bare_ordinal():
    # 1025 实锤补: 三问卡 "第一，/第二，" 无量词 — 样式判据须认 (检测正则仍严格)
    assert is_list_card(["第一，如果这个方案失败，最可能死在哪？", "第二，谁最反对？"])
    assert not is_pure_list_card(["第一，谁最反对？", "他的理由能不能变成B方案？"])
    assert not is_list_card(["三问定性质，三线定底线，三案防翻车"])  # 量词后是"定"非分隔符


def test_smart_cache_same_texts_no_reshuffle(tmp_path, monkeypatch):
    # 1025 实锤: 同稿重装配不该洗牌 — 缓存命中复用; 稿变 (hash 异) 重判
    calls = []

    class _CountLLM:
        def chat(self, system, user, **kw):
            calls.append(1)
            return json.dumps({"cards": [{"start": 2, "end": 3,
                                          "kind": "mantra", "why": "口诀"}]})

    monkeypatch.setattr("app.services.llm_service.get_llm_service", lambda: _CountLLM())
    texts = ["开场一句。", "三问定性质，三线定底线，三案防翻车。"]
    cp = tmp_path / "quote_cards.json"
    c1 = detect_quote_cards_smart(texts, cache_path=cp)
    c2 = detect_quote_cards_smart(texts, cache_path=cp)   # 命中缓存, LLM 零调用
    assert len(calls) == 1 and c1 == c2
    c3 = detect_quote_cards_smart(texts + ["稿改了。"], cache_path=cp)  # hash 变 → 重判
    assert len(calls) == 2
