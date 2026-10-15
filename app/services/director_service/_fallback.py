"""Director Agent 2.0 — 失败槽位替换 (fallback 链).

按管线开关动态构造替代链 (仅回退到启用管线内的工作流), 或保留 legacy 固定链。
字幕体系已砍掉 (2026-08-01): 兜底不再用黑底白字 black_subtitle, 直接黑屏。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import DirectorSlot
from app.services.director_parser import _HOST_FAMILY
from app.services.slot_workflows.hf_chart import _chart_has_data, _normalize_chart_input
from app.services.slot_workflows.hf_extract import _extract_hf_content

logger = logging.getLogger(__name__)

__all__ = ["replace_failed_slot"]


def _h_fallback_workflow(slot: DirectorSlot) -> str:
    """broll_pexels → H 线降级目标按数据形态三分 (2026-09-09):

    quote → hf_quote; 有图表数据 → hf_chart; 全无数据 → hf_title。
    无数据段进 hf_chart 会渲染 470px 巨 "0" ("页面上只有一个字母 o" 实锤),
    标题卡复用提取的 title/subtitle, 卡面有真实内容。
    """
    rc = (slot.params_json or {}).get("render_config") or {}
    if rc.get("quote"):
        return "hf_quote"
    chart = _normalize_chart_input(rc, _extract_hf_content(slot.text_context or ""))
    return "hf_chart" if _chart_has_data(chart) else "hf_title"


def replace_failed_slot(
    db: Session,
    slot: DirectorSlot,
    fallback_chain: list[str] | None = None,
    enabled_pipelines: set[str] | None = None,
) -> DirectorSlot:
    """Replace a failed slot with the next-best workflow in the fallback chain.

    Args:
        fallback_chain: 显式链; None 时由 *enabled_pipelines* 决定.
        enabled_pipelines: 启用的管线集合 (e.g. {"c","p","h"}).
            None=全部启用 → 保留 legacy 固定链 (不感知开关, 向后兼容).
            非 None → 按启用管线动态构造替代链, 只回退到启用管线内的工作流
            (slot.workflow 置首, 避免 host 失败时 current=链首 跳过最佳替代).
    """
    if fallback_chain is None:
        if enabled_pipelines is None:
            # 字幕体系已砍掉(2026-08-01): 兜底不再用黑底白字 black_subtitle, 直接黑屏。
            fallback_chain = ["broll_pexels", "broll_local", "black_placeholder"]
        else:
            if slot.workflow in ("broll_local", "black_placeholder"):
                # 无管线兜底: 固定链, black_placeholder 恒在末尾 → 走到尽头即返回
                # (若 prepend slot.workflow 再加兜底, black_placeholder↔broll_local 会循环)
                fallback_chain = ["broll_local", "black_placeholder"]
            else:
                family_priorities: list[tuple[str, str]]
                if slot.workflow in _HOST_FAMILY:
                    family_priorities = [("c", "host"), ("h", "hf_title"), ("p", "broll_pexels")]
                elif slot.workflow in ("hf_chart", "hf_title"):
                    family_priorities = [("h", "hf_chart"), ("c", "host"), ("p", "broll_pexels")]
                elif slot.workflow == "broll_pexels":
                    # H 线降级目标按数据形态选 (2026-08-25 两分 → 2026-09-09 三分,
                    # 见 _h_fallback_workflow): 无数据段不再进 hf_chart 渲染空卡巨 0。
                    h_wf = _h_fallback_workflow(slot)
                    family_priorities = [("p", "broll_pexels"), ("c", "host"), ("h", h_wf)]
                elif slot.workflow == "evidence_image":
                    # 证据图失败 (池空/无匹配/图坏) → 同性质降级 broll_pexels,
                    # 不再回头 host/hf (证据段本质是 B-roll 段, 2026-09-04 管线③)。
                    family_priorities = [("p", "broll_pexels")]
                else:
                    family_priorities = []
                # 只保留启用管线内的工作流 (head 过滤), slot.workflow 置首避免跳过最佳替代
                head = [wf for pl, wf in family_priorities if pl in enabled_pipelines]
                fallback_chain = list(dict.fromkeys([slot.workflow, *head, "broll_local", "black_placeholder"]))

    current = slot.workflow
    if current not in fallback_chain:
        current = fallback_chain[0]
    idx = fallback_chain.index(current)
    if idx + 1 >= len(fallback_chain):
        return slot

    next_workflow = fallback_chain[idx + 1]

    slot.status = "replaced"
    slot.error_code = slot.error_code or "FALLBACK"
    slot.error_message = (slot.error_message or "") + f" | fallback to {next_workflow}"

    new_slot = DirectorSlot(
        director_job_id=slot.director_job_id,
        slot_index=slot.slot_index,
        start_sec=slot.start_sec,
        end_sec=slot.end_sec,
        duration_sec=slot.duration_sec,
        text_context=slot.text_context,
        segment_id=slot.segment_id,
        visual_type=next_workflow,
        workflow=next_workflow,
        params_json={"replaced_from": current, **slot.params_json},
        status="queued",
    )
    db.add(new_slot)
    db.commit()
    db.refresh(new_slot)
    logger.info("Slot %s replaced: %s -> %s", slot.id, current, next_workflow)
    return new_slot
