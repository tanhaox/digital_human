# -*- coding: utf-8 -*-
"""1109 采纳饿死根修: ① LLM 分诊建议不交 card_text (ep7 13/13 全空) →
build_cards 宁缺勿滥闸全静默跳 → v3.cards=0 → 装配无卡 (用户「采纳方案去哪了」)。
修 = 建卡口窄 LLM 补产 + 域闸校验 + 显性报告 (card_fill_report)。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

import app.services.llm_service as llm_service
from app.services.anim_pipeline.v3_pipeline import shots as v3s


def _v3():
    return {
        "lines": [
            {"no": 1, "text": "先听，再问，再换说法，是顾问的三板斧。",
             "adopted": ["quote"],
             "suggs": [{"kind": "quote", "text": "可做金句", "card_text": ""}]},
            {"no": 2, "text": "客户翻脸不是意外，是规律。",
             "adopted": ["event"],
             "suggs": [{"kind": "event", "text": "宜上事件金字", "card_text": ""}]},
        ],
        "cards": [],
        "shots": [{"sid": "s1", "line_refs": [1, 2]}],
    }


def test_valid_card_text_domain():
    assert v3s._valid_card_text("event", "体面退出")
    assert not v3s._valid_card_text("event", "")                     # 空
    assert not v3s._valid_card_text("event", "一二三四五六七八九十十一十二十三")  # >12
    assert v3s._valid_card_text("quote", "先听，再问，再换说法")


def test_backfill_fills_and_builds(monkeypatch):
    def fake_chat(sys_p, prompt, **kw):
        assert '"kind": "quote"' in prompt or '"kind":"quote"' in prompt
        return ('{"cards":[{"no":1,"kind":"quote","card_text":"先听，再问，再换说法","diagram":null},'
                '{"no":2,"kind":"event","card_text":"翻脸规律","diagram":null}]}')
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    v3 = _v3()
    cards = v3s.build_cards(v3, v3["shots"])
    rep = v3["card_fill_report"]
    assert rep["pending"] == 2 and rep["filled"] == 2 and not rep["rejected"]
    assert rep["filled"] + len(rep["rejected"]) == rep["pending"]
    texts = [c["text"] for c in cards]
    assert "先听，再问，再换说法" in texts and "翻脸规律" in texts


def test_backfill_rejects_overlong(monkeypatch):
    def fake_chat(sys_p, prompt, **kw):
        return ('{"cards":[{"no":1,"kind":"quote","card_text":"'
                + "超" * 50 + '","diagram":null},'
                '{"no":2,"kind":"event","card_text":"翻脸规律","diagram":null}]}')
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    v3 = _v3()
    cards = v3s.build_cards(v3, v3["shots"])
    rep = v3["card_fill_report"]
    assert rep["rejected"] == ["L1:quote"] and rep["filled"] == 1
    assert len(cards) == 1 and cards[0]["text"] == "翻脸规律"   # 不合格不硬造


def test_backfill_llm_failure_visible(monkeypatch):
    def fake_chat(sys_p, prompt, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    v3 = _v3()
    cards = v3s.build_cards(v3, v3["shots"])   # 不炸 — 宁缺勿滥保持
    assert cards == []
    rep = v3["card_fill_report"]
    assert rep["llm_fail"] and rep["pending"] == 2


def test_no_pending_no_llm_call(monkeypatch):
    def fake_chat(*a, **kw):
        raise AssertionError("无待补不应调 LLM")
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat", fake_chat)
    v3 = _v3()
    v3["lines"][0]["suggs"][0]["card_text"] = "已带卡面文字"
    v3["lines"][1]["adopted"] = []
    v3s.build_cards(v3, v3["shots"])
    assert v3["card_fill_report"]["pending"] == 0
