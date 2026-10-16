"""目标读者以书定测试 (2026-09-13c): 书内容锁定优先, persona 只兜底。

锚: HBO 实锤 — 目标读者画像曾是 persona.target_reader 原文 (账号人口学
"25-50岁男性职场人"), 而书自己的读者信号 (kernel.why_read) 在蒸馏料里没人用。
"""
from __future__ import annotations

from app.services.book_service import creation_steps as cs

# ── _book_reader_value: 书级有效值判定 ──

def test_book_reader_value_tier_priority():
    """tier != persona (书内容锁定/人工填) 才算数; persona 继承值=未定。"""
    assert cs._book_reader_value(
        {"目标读者画像": {"value": "靠流量吃饭的创作者", "tier": "L0"}}) == "靠流量吃饭的创作者"
    assert cs._book_reader_value(
        {"目标读者画像": {"value": "人工填的", "tier": "manual"}}) == "人工填的"
    # persona 继承值 (0907 旧逻辑盖的章) 视为未定 → None, 触发重新锁定
    assert cs._book_reader_value(
        {"目标读者画像": {"value": "25-50岁男性职场人", "tier": "persona"}}) is None
    assert cs._book_reader_value({}) is None


# ── _derive_book_reader: 锁定器 ──

class _StubLLM:
    def __init__(self, payload):
        self._payload = payload
        self.calls: list[str] = []

    def chat(self, sys_p, user_p, **kw):
        self.calls.append(sys_p)
        return self._payload


class _Book:
    book_title = "测试书"
    author = "某人"
    input_json = {"book_meta": {"blurb": "一本关于内容变现的书"}}


_FACING = {"kernel": {"one_liner": "内容战略驱动商业模式",
                      "why_read": "内容创作者急需摆脱流量陷阱",
                      "positioning": "商业分析手册", "reader_shift": ["从流量到订阅"]}}


def test_derive_reader_ok(monkeypatch):
    """eligible=true → {reader, evidence}; 提示词含同构处境/禁人口学铁律。"""
    stub = _StubLLM('{"reader":"收入依赖流量分成、数据好看却不挣钱的创作者","evidence":"全书讲付费主体","eligible":true}')
    monkeypatch.setattr(cs, "_llm", lambda: stub)
    d = cs._derive_book_reader(_Book(), _FACING)
    assert d["reader"] == "收入依赖流量分成、数据好看却不挣钱的创作者"
    assert "同构处境" in stub.calls[0] and "人口学套话" in stub.calls[0]


def test_derive_reader_not_eligible(monkeypatch):
    """eligible=false / 空 reader → None (调用方人设兜底)。"""
    monkeypatch.setattr(cs, "_llm", lambda: _StubLLM('{"reader":"","evidence":"","eligible":false}'))
    assert cs._derive_book_reader(_Book(), _FACING) is None
    monkeypatch.setattr(cs, "_llm", lambda: _StubLLM("不是json"))
    assert cs._derive_book_reader(_Book(), _FACING) is None


def test_derive_reader_no_material():
    """无 kernel/L0/blurb 料 → 直接 None, 不发起 LLM 调用。"""
    class _BareBook:
        book_title = "测试书"
        author = "某人"
        input_json = {}
    assert cs._derive_book_reader(_BareBook(), {}) is None
    assert cs._derive_book_reader(_BareBook(), {"kernel": {}, "l0": {}}) is None
