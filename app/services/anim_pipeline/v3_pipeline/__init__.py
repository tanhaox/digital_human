# -*- coding: utf-8 -*-
"""v3 八页产线包 (1105 双轨, V0 骨架) — 结构与渲染分离.

相位: triage(①文稿分诊) → arcs(②立意) → shots(③分镜文字) → direct(④导演)
      → stamp(⑥盖章, 确定性换算); ⑤TTS/⑦K2/⑧H3 复用 v2 (薄壳页深链)。

规范 (规划 V0 定): 数据写 shots.json 的 v3 命名空间 (增字段禁删 v2 字段);
LLM 全挂 tag 进 llm_ledger; 每相位落 v3.<phase> 字段 + 放行态 v3.gates[phase]。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 放行闸链 (规划 DAG): key=相位, value=前置相位
GATE_CHAIN: dict[str, str | None] = {
    "triage": None,
    "arcs": "triage",
    "shots": "arcs",
    "direct": "shots",
    "stamp": None,   # ⑥只等⑤TTS (音频验收), 不在 doc 闸链内
}


def run_phase(job: dict[str, Any]) -> None:
    """v3 相位分发 (anim_service._phase_v3 入口)。V0: 骨架占位, 逐相位在 V1-V5 落地。"""
    phase = job["phase"].removeprefix("v3_")
    handler = _HANDLERS.get(phase)
    if handler is None:
        raise SystemExit(f"v3 相位 [{phase}] 建设中 (V0 骨架) — 见 docs/improvements/进行中-20261105-分镜续跑升级.md")
    handler(job)


def _stub(job: dict[str, Any], name: str) -> None:  # V0 占位, V1+ 替换
    raise SystemExit(f"v3 {name} 建设中 (V0 骨架)")


def _h_triage(job: dict[str, Any]) -> None:
    from . import triage
    triage.run(job)
def _h_arcs(job: dict[str, Any]) -> None:
    from . import arcs
    arcs.run(job)
def _h_shots(job: dict[str, Any]) -> None:
    from . import shots
    shots.run(job)
def _h_direct(job: dict[str, Any]) -> None:
    from . import direct
    direct.run(job)
def _h_k2(job: dict[str, Any]) -> None:
    from . import k2
    k2.run(job, only=set(job.get("only") or []) or None)
def _h_stamp(job: dict[str, Any]) -> None: _stub(job, "stamp")


_HANDLERS = {
    "triage": _h_triage, "arcs": _h_arcs, "shots": _h_shots, "k2": _h_k2,
    "direct": _h_direct, "stamp": _h_stamp,
}


def gates_view(doc: dict[str, Any]) -> dict[str, bool]:
    """doc → 各相位放行态 (v3.gates 持久 + GATE_CHAIN 前置推导)。前端轨/页锁数据源。"""
    gates = dict((doc.get("v3") or {}).get("gates") or {})
    out: dict[str, bool] = {}
    for phase, prev in GATE_CHAIN.items():
        out[phase] = bool(gates.get(phase)) and (prev is None or out.get(prev, False) or bool(gates.get(prev)))
    # stamp 特例: 链外 (等音频), 直接读持久位
    out["stamp"] = bool(gates.get("stamp"))
    return out
