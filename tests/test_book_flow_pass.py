# -*- coding: utf-8 -*-
"""V1 顺稿 pass (1103): 验收逻辑单测 — 收 / 新病族拒 / 超Δ拒 / 空返回保原稿.

实锤锚: ep2 六类缝 (案例双讲/挂空点名/轴错位/挂空起势/仪式句混用/起势堆叠)。
LLM mock, 验收链 (snapshot+family_new+Δ) 全真跑。
"""
from types import SimpleNamespace as NS
from unittest.mock import patch

from app.services.book_service import episode_gen as eg

_LBL = "【0-28秒｜钩子】\n"


def _ep():
    return NS(ep_index=2, target_duration_sec=240.0, roadmap_json={})


def _book():
    return NS(book_title="值得信赖的顾问")


def _run(text_out: str, base_text: str = _LBL + "你是技术好人品好，客户还是不点头。这是第一句展开。"):
    with patch.object(eg, "_llm") as m:
        m.return_value.chat.return_value = text_out
        return eg._flow_smooth_pass(
            base_text, ep=_ep(), book=_book(), req_cta=False, series_word="",
            figure="", flow_check=True, gen_dir=None), m.return_value.chat.call_args


def test_flow_pass_accepts_clean_smooth():
    """缝不增/无新病族/Δ合法 → 收 (顺后稿替换)."""
    smoothed = _LBL + "你是技术好人品好，客户还是不点头。这是第一句展开。先看分子，可信度那一格。"
    got, call = _run(smoothed)
    assert got == smoothed
    assert call.kwargs.get("tag") == "epgen:flow"


def test_flow_pass_rejects_new_family():
    """顺后稿带新病族 (复读段) → 弃用保原稿."""
    base = _LBL + "你是技术好人品好。这是展开第一句，这是展开第二句，这是展开第三句。"
    bad = _LBL + "你是技术好人品好。这是展开第一句，这是展开第一句。这是展开第二句。" * 2
    got, _ = _run(bad, base)
    assert got == base


def test_flow_pass_rejects_over_delta():
    """|Δ|>150 字 → 弃用保原稿 — 顺稿只许小刀 (扩写/大砍都是别的 pass 的活)."""
    base = _LBL + "客户还是不点头。展开一句。"
    bloated = _LBL + "客户还是不点头。展开一句。" + "再补一句没用的水字。" * 20
    got, _ = _run(bloated, base)
    assert got == base


def test_flow_pass_accepts_small_shrink():
    """删双讲/挂空残句天然是减法 (ep2 首跑 -20 被旧'只许增'误拒实锤) — 小减法收."""
    base = _LBL + "客户还是不点头。展开一句。先说句反调。再说回正题。结尾一句。"
    smoothed = _LBL + "客户还是不点头。展开一句。结尾一句。"
    got, _ = _run(smoothed, base)
    assert got == smoothed


def test_flow_pass_rejects_deep_shrink():
    """砍内容超 150 字 = 压缩 pass 的活 → 拒收."""
    base = _LBL + "客户还是不点头。" + "这是展开内容的一句。" * 16
    shrunk = _LBL + "客户还是不点头。"
    got, _ = _run(shrunk, base)
    assert got == base


def test_flow_pass_empty_return_keeps_original():
    got, _ = _run("", _LBL + "客户还是不点头。展开一句。")
    assert got.startswith(_LBL)


def test_listen_pass_accepts_clean():
    """1103 W固化: 听感 pass 验收 — 无新病族/阻断不增/|Δ|≤250 才收."""
    from types import SimpleNamespace as NS
    from unittest.mock import patch
    from app.services.book_service import episode_gen as eg
    base = "【0-28秒｜钩子】\n这集讲三件事。第一件是可信度打分。第二件是可靠度。第三件是亲近感。结尾。"
    listened = base + "回顾段末补一句：你上回那单，折在哪一格？"
    with patch.object(eg, "_llm") as m:
        m.return_value.chat.return_value = listened
        got = eg._listen_pass(base, ep=NS(ep_index=3, target_duration_sec=240.0, roadmap_json={}),
                              book=NS(book_title="值得信赖的顾问"), req_cta=True,
                              series_word="", figure="", flow_check=True, gen_dir=None)
    assert got == listened
    assert m.return_value.chat.call_args.kwargs.get("tag") == "epgen:listen"


def test_listen_pass_rejects_new_block():
    from types import SimpleNamespace as NS
    from unittest.mock import patch
    from app.services.book_service import episode_gen as eg
    base = "【0-28秒｜钩子】\n普通口播一句。再来一句展开内容。"
    bad = "【0-28秒｜钩子】\n普通口播一句。再来一句展开内容。**星号渗入。"
    with patch.object(eg, "_llm") as m:
        m.return_value.chat.return_value = bad
        got = eg._listen_pass(base, ep=NS(ep_index=3, target_duration_sec=240.0, roadmap_json={}),
                              book=NS(book_title="X"), req_cta=False,
                              series_word="", figure="", flow_check=False, gen_dir=None)
    assert got == base
