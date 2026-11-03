# -*- coding: utf-8 -*-
"""1103 T 批: 乱序重生成盲区修补 — T2 预告复读闸单测 (T1 占坑注入/T3 概念点名走真跑留档).

实锤: ep2 重生成稿 (gen_20261103_112244) 预告「下一集聊：为什么越急着给答案，客户
越把嘴闭上？」≈ ep1 尾预告 (bigram J≈0.74), ep3 真题材是信任等式 — 承接句被当预告甩锅。
"""
from types import SimpleNamespace as NS

from app.services.book_service.episode_gen import _teaser_repeat_issues

_EP1_TEASER = "下一集聊：为什么我们越急着给答案，客户越把嘴闭上？"
_EP2_TEASER_DUP = "下一集聊：为什么越急着给答案，客户越把嘴闭上？"
_EP2_TEASER_OK = "下一集聊：你的信任到底卡在哪一格？自我导向是怎么毁掉专业度的？"


def _book(prev_text: str):
    return NS(episodes=[
        NS(ep_index=1, script_text=prev_text),
        NS(ep_index=2, script_text=""),
    ])


def _ep2(roadmap=None):
    return NS(ep_index=2, roadmap_json=roadmap or {"启下": "下集拆解信任等式清零自我导向分母"})


def test_teaser_repeat_flagged():
    iss = _teaser_repeat_issues(_book(_EP1_TEASER), _ep2(),
                                f"正文。\n{_EP2_TEASER_DUP}\n我是老谭。")
    assert len(iss) == 1 and "预告复读上集预告" in iss[0] and "信任等式" in iss[0], iss


def test_teaser_distinct_passes():
    iss = _teaser_repeat_issues(_book(_EP1_TEASER), _ep2(),
                                f"正文。\n{_EP2_TEASER_OK}\n我是老谭。")
    assert iss == [], iss


def test_ep1_exempt():
    book = NS(episodes=[NS(ep_index=1, script_text="")])
    assert _teaser_repeat_issues(book, NS(ep_index=1), _EP2_TEASER_DUP) == []


def test_prev_without_teaser_passes():
    iss = _teaser_repeat_issues(_book("结尾没有预告句。"), _ep2(), f"正文。\n{_EP2_TEASER_DUP}\n")
    assert iss == [], iss
