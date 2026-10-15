# -*- coding: utf-8 -*-
"""生产台账 (1005 用户令: 每集生成过多少次图/视频要有记录).

每镜每次 k2/h3 生成事件 append 到 outputs/动画/{书}/epN/build_ledger.jsonl —
append-only, 永不破主流程。三轨复查·系统轨的取数源 (替代 bak 文件计数/全局
日志倒推 — 日志行只有镜号无书集归属, 全局撞名不可靠, 1005 实锤)。

字段: ts / kind(k2|h3|replan) / sid / trigger(batch|redo|replan|single|"")
      / outcome(ok|fail) / ms / note(重掷次数等)
"""
import json
import logging
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
