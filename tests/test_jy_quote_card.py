# -*- coding: utf-8 -*-
"""截屏卡检测 — 真实稿件用例 (ep2/ep3/ep4 卓有成效系列 + 负例)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.jy_draft_service.quote_card import detect_quote_cards


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
