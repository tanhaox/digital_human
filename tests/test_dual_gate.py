# -*- coding: utf-8 -*-
"""Z 系固化 (1103): Z1/Z3 双维出厂闭环 + Z2 coverage 保真."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from app.services.book_service import episode_gen as eg
from app.services.book_service.dual_review import all_pass, failed_items, review

_LBL = "【0-28秒｜钩子】\n"


def _ep():
    return NS(ep_index=4, target_duration_sec=240.0, roadmap_json={})


def test_coverage_fidelity_drops_phantom():
    """Z2: 自报未命中正文 = 幽灵清洗 (ep4 海尔实锤); 命中/短条目保留."""
    text = "书里记着大卫教数理统计。换一句你想让我听着。"
    cov = ["大卫统计学案例", "海尔砸冰箱案例", "倾听"]
    kept, dropped = eg._coverage_fidelity(text, cov)
    assert "大卫统计学案例" in kept and "倾听" in kept
    assert dropped == ["海尔砸冰箱案例"]


def test_dual_review_reproduces_ep4_findings():
    """评审器独立复现 ep4 v3 手工发现 (D3/D4/L5)."""
    t = open('.tmp/ep4_v3.txt', encoding='utf-8').read()
    R = review(t, ['低自我导向措辞'])
    fails = failed_items(R)
    assert any('D3' in f for f in fails) and any('D4' in f for f in fails)
    assert not all_pass(R)


def test_dual_gate_pass_accepts_full_pass_fix():
    """Z1: 一次修复全过且安全 → 收 (05j 语义)."""
    from app.services.book_service import dual_review as dr
    bad = _LBL + "为什么说，这集讲措辞转换？方案常对客户不采纳，你手里能多四句。"
    fixed = (_LBL + "为什么说，这集讲措辞转换？方案常对客户不采纳，你手里能多四句。\n"
             "先对个号：你上回被客户顶回来，是内容不行，还是开口先输了？")
    with patch.object(eg, "_llm") as m, \
         patch.object(dr, "review", side_effect=lambda t, c=None: (
             review(t, c) if '先对个号' not in t else
             [('D1', 'ok', True)] * 13)):
        m.return_value.chat.return_value = fixed
        got = eg._dual_gate_pass(bad, ep=_ep(), book=NS(book_title='X'),
                                 req_cta=False, series_word="", figure="",
                                 flow_check=False, gen_dir=None,
                                 concepts=['低自我导向措辞'])
    assert got == fixed


def test_dual_gate_pass_rejects_unsafe_fix():
    """修复引入新病族 → 弃用保原稿."""
    from app.services.book_service import dual_review as dr
    bad = _LBL + "为什么说，这集讲措辞转换？方案常对客户不采纳，你手里能多四句。"
    poisoned = bad + "**星号渗入。"
    with patch.object(eg, "_llm") as m, \
         patch.object(dr, "review", return_value=[('D', 'x', True)] * 13):
        m.return_value.chat.return_value = poisoned
        got = eg._dual_gate_pass(bad, ep=_ep(), book=NS(book_title='X'),
                                 req_cta=False, series_word="", figure="",
                                 flow_check=False, gen_dir=None)
    assert got == bad
