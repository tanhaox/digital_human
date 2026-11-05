# -*- coding: utf-8 -*-
"""生产台账 (1005 用户令: 每集生成过多少次图/视频要有记录).

每镜每次 k2/h3 生成事件 append 到 outputs/动画/{书}/epN/build_ledger.jsonl —
append-only, 永不破主流程。三轨复查·系统轨的取数源 (替代 bak 文件计数/全局
日志倒推 — 日志行只有镜号无书集归属, 全局撞名不可靠, 1005 实锤)。

1105 批6 (T1): LLM token 台账 llm_ledger.jsonl — 两套 LLM 栈的 usage 逐调用
落盘 (书/集/相位由 contextvar 携带, anim_service._run_job 设入), job 级累计
进 result (llm_tokens)。同教义: append-only, 失败永不破主流程。

字段: ts / kind(k2|h3|replan) / sid / trigger(batch|redo|replan|single|"")
      / outcome(ok|fail) / ms / note(重掷次数等)
llm_ledger 行: ts / kind=llm / tag / provider / ptok / ctok / cached / phase
"""
import contextvars
import json
import logging
from contextlib import contextmanager
from datetime import datetime

from . import shots as shots_mod

logger = logging.getLogger(__name__)


def record(book_title: str, ep: int, kind: str, sid: str, *,
           trigger: str = "", outcome: str = "ok", ms: int = 0, note: str = "") -> None:
    try:
        p = shots_mod.ep_dir(book_title, ep) / "build_ledger.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": datetime.now().isoformat(timespec="seconds"),
                "kind": kind, "sid": str(sid), "trigger": trigger,
                "outcome": outcome, "ms": int(ms), "note": str(note)[:80]}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 台账永不破主流程
        logger.debug("[ledger] 写入失败 (忽略)", exc_info=True)


def record_llm(book_title: str, ep: int, tag: str, *, provider: str = "",
               ptok: int = 0, ctok: int = 0, cached: int = 0,
               phase: str = "") -> None:
    """1105 批6 (T1): LLM token 台账 — 逐调用 append 到集级 llm_ledger.jsonl."""
    try:
        p = shots_mod.ep_dir(book_title, ep) / "llm_ledger.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": datetime.now().isoformat(timespec="seconds"),
                "kind": "llm", "tag": str(tag)[:40], "provider": str(provider)[:20],
                "ptok": int(ptok), "ctok": int(ctok), "cached": int(cached),
                "phase": str(phase)[:12]}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 台账永不破主流程
        logger.debug("[ledger] llm 台账写入失败 (忽略)", exc_info=True)


# ── LLM 台账作用域: 书/集/相位随 contextvar 走, 线程隔离 (1105 批6) ──────────

class LlmScope:
    """job 级累计器: calls/ptok/ctok/by_tag — _run_job 结束时进 result.llm_tokens."""

    def __init__(self, book_title: str, ep: int, phase: str = ""):
        self.book_title, self.ep, self.phase = book_title, ep, phase
        self.calls = 0
        self.ptok = 0
        self.ctok = 0
        self.by_tag: dict[str, list[int]] = {}   # tag → [calls, ptok, ctok]


_LLM_SCOPE: contextvars.ContextVar = contextvars.ContextVar(
    "llm_ledger_scope", default=None)


@contextmanager
def llm_scope(book_title: str, ep: int, phase: str = ""):
    """设入 LLM 台账上下文 (anim_service._run_job 用)。

    contextvar 线程隔离: job 线程里 set, 只影响本线程及同上下文调用 —
    页面同步小调用 (无 scope) 不记账。"""
    sc = LlmScope(book_title, ep, phase)
    tok = _LLM_SCOPE.set(sc)
    try:
        yield sc
    finally:
        _LLM_SCOPE.reset(tok)


def note_usage(tag: str, provider: str, ptok: int, ctok: int,
               cached: int = 0) -> None:
    """记一次 LLM usage: 有 scope 才落盘+累计 (两套栈共用入口)。"""
    sc = _LLM_SCOPE.get()
    if sc is None:
        return
    try:
        record_llm(sc.book_title, sc.ep, tag=tag, provider=provider,
                   ptok=ptok, ctok=ctok, cached=cached, phase=sc.phase)
        sc.calls += 1
        sc.ptok += ptok
        sc.ctok += ctok
        t = sc.by_tag.setdefault(tag, [0, 0, 0])
        t[0] += 1
        t[1] += ptok
        t[2] += ctok
    except Exception:  # noqa: BLE001
        logger.debug("[ledger] usage 记账失败 (忽略)", exc_info=True)
