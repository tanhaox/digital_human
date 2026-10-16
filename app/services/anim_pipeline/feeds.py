# -*- coding: utf-8 -*-
"""可选加料供给注册点 (B08, 2026-10-16) — 管线取外部数据的唯一通道.

背景: derive_characters 要蒸馏产物"故事库"段、圣经推导要樊登全书稿, 原先管线内
延迟 import book_service 构成环#2。现改为: app 外壳 (anim_service, 两边都可引)
启动时把读料函数注册进来, 管线经 get_feed 取 — 管线零 book_service import、
管线函数签名零改动 (参数注入会连锁 6+ 签名且路由直调时缺料), 注册是进程级的,
路由直调管线 (books.py derive_characters) 也不缺料。

未注册/抛错 = 缺料, 由调用方按原容错语义跳过 (与旧 try/except 等价)。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_FEEDS: dict[str, Callable[..., Any]] = {}


def register_feed(name: str, fn: Callable[..., Any]) -> None:
    """app 外壳注册读料函数 (重复注册后者覆盖, 兼顾测试注入)."""
    _FEEDS[name] = fn


def get_feed(name: str) -> Callable[..., Any] | None:
    """管线取供给; 未注册返回 None (调用方按缺料处理)."""
    return _FEEDS.get(name)
