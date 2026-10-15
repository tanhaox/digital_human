"""Slot executor — orchestration layer for batch-by-type phase execution.

Workflow implementations live in slot_workflows.py.
This module handles: execute_slot, execute_all_slots, phase scheduling, fallbacks.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import DirectorJob, DirectorSlot
from app.services.director_events import publish as _evt
from app.services.director_service import replace_failed_slot, append_trace
from app.services.director_parser import _best_fallback_workflow
from app.services.gpu_service_manager import get_gpu_service_manager
from app.services.slot_workflows import WORKFLOW_HANDLERS

logger = logging.getLogger(__name__)

# 取消标志 — 由 director router 的 /cancel 端点设置
_cancel_flags: set[str] = set()

# 强制停止标志 — 由 force-stop 端点设置. 无天然清理点, 每个新执行入口
# (execute_job/compose_job/retry_slot/retry_by_workflow) 必须显式 clear_force_stopped.
_force_stopped: set[str] = set()


def request_cancel(job_id: str) -> None:
    """Signal the execute loop to stop after the current phase."""
    _cancel_flags.add(job_id)


def clear_cancel(job_id: str) -> None:
    """Remove the cancel flag (used by the planning-cancel path on finish)."""
    _cancel_flags.discard(job_id)


def is_cancelled(job_id: str) -> bool:
    return job_id in _cancel_flags


def _is_cancelled(job_id: str) -> bool:
    return job_id in _cancel_flags


def mark_force_stopped(job_id: str) -> None:
    """标记强制停止. 后台线程任何状态写前查 is_force_stopped → 跳过终态覆盖."""
    _force_stopped.add(job_id)


def is_force_stopped(job_id: str) -> bool:
    return job_id in _force_stopped


def clear_force_stopped(job_id: str) -> None:
    """清除强制停止标记. 必须在每个新执行入口调用, 防脏标记阻断后续执行."""
    _force_stopped.discard(job_id)


def clear_force_stopped_all() -> None:
    """清空全部强制停止标记 (lifespan 重启清理)."""
    _force_stopped.clear()


def clear_cancel_flags_all() -> None:
    """清空全部取消标记 (lifespan 重启清理)."""
    _cancel_flags.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Single slot execution
# ---------------------------------------------------------------------------

def execute_slot(db: Session, slot: DirectorSlot) -> DirectorSlot:
    """Run a single slot and commit completed/failed status."""
    if slot.status not in ("queued", "replaced"):
        return slot

    slot.status = "running"
    slot.retry_count += 1
    db.commit()

    # 线程子进程上下文: 绑定 job_id, 供 proc_registry 自动关联本 slot 的子进程
    from app.services.proc_registry import set_current_job_id
    set_current_job_id(slot.director_job_id)
    try:
        handler = WORKFLOW_HANDLERS.get(slot.workflow)
        if handler is None:
            slot.status = "failed"
            slot.error_code = "UNKNOWN_WORKFLOW"
            slot.error_message = f"unknown workflow {slot.workflow}"
            db.commit()
            return slot

        try:
            if slot.workflow in ("hf_chart", "hf_title", "hf_opening", "hf_quote",
                                 "hf_identity", "hf_follow"):
                output_path = handler(db, slot, slot.workflow)
            else:
                output_path = handler(db, slot)
            slot.output_path = output_path
            slot.status = "completed"
            slot.error_code = None
            slot.error_message = None
        except Exception as exc:
            logger.exception("Slot %s workflow %s failed", slot.id, slot.workflow)
            slot.status = "failed"
            slot.error_code = type(exc).__name__.upper()
            slot.error_message = str(exc)[:512]

        db.commit()
        db.refresh(slot)
        return slot
    finally:
        set_current_job_id(None)


# ---------------------------------------------------------------------------
# Phase scheduling
# ---------------------------------------------------------------------------

_EXECUTION_PHASES: list[dict[str, Any]] = [
    {"workflows": {"host", "mixed_host_broll"}, "gpu": "comfyui", "pipeline": "c"},
    # evidence_image 与 broll_pexels 同 phase: pipeline "p"、无 GPU (证据图管线③)
    {"workflows": {"broll_pexels", "evidence_image"}, "gpu": None, "pipeline": "p"},
    {"workflows": {"hf_chart", "hf_title", "hf_opening", "hf_quote",
                   "hf_identity", "hf_follow"}, "gpu": None, "pipeline": "h"},
    {"workflows": {"broll_local", "black_placeholder"}, "gpu": None, "pipeline": None},
]

# 管线名 → phase 索引 (C/P/H 三条主线, 兜底 phase 不在其中)
_PIPELINE_PHASE_MAP: dict[str, int] = {
    "c": 0,
    "p": 1,
    "h": 2,
}


def _run_phase(
    db: Session,
    slots: list[DirectorSlot],
    *,
    auto_replace: bool,
    job_id: str = "",
    phase_name: str = "",
) -> list[DirectorSlot]:
    """Execute *slots* (same workflow family); return those still failed."""
    failed: list[DirectorSlot] = []
    total = len([s for s in slots if s.status == "queued"])
    done_count = 0
    for slot in sorted(slots, key=lambda s: s.slot_index):
        if slot.status != "queued":
            continue
        # slot 级别取消检查
        if _is_cancelled(job_id):
            slot.status = "failed"
            slot.error_code = "CANCELLED"
            slot.error_message = "用户取消"
            db.commit()
            failed.append(slot)
            _evt(job_id, {"type": "slot_fail", "phase": phase_name, "slot_index": slot.slot_index, "workflow": slot.workflow, "error": "用户取消"})
            continue
        _evt(job_id, {"type": "slot_start", "phase": phase_name, "slot_index": slot.slot_index, "workflow": slot.workflow})
        execute_slot(db, slot)
        done_count += 1
        if slot.status == "failed":
            failed.append(slot)
            _evt(job_id, {"type": "slot_fail", "phase": phase_name, "slot_index": slot.slot_index, "workflow": slot.workflow, "error": slot.error_message or "", "progress": f"{done_count}/{total}"})
        else:
            _evt(job_id, {"type": "slot_done", "phase": phase_name, "slot_index": slot.slot_index, "workflow": slot.workflow, "progress": f"{done_count}/{total}"})
    return failed


def _handle_fallbacks(
    db: Session,
    failed_slots: list[DirectorSlot],
    *,
    job_id: str = "",
    enabled_pipelines: set[str] | None = None,
) -> None:
    """Walk fallback chains for failed slots."""
    manager = get_gpu_service_manager()
    comfyui_pending: list[DirectorSlot] = []
    c_disabled = enabled_pipelines is not None and "c" not in enabled_pipelines

    def _walk(slot: DirectorSlot) -> None:
        cur = slot
        tried: set[str] = set()
        while cur.status == "failed":
            new_slot = replace_failed_slot(db, cur, enabled_pipelines=enabled_pipelines)
            if new_slot.id == cur.id:
                break
            # 死循环防护 (2026-08-20): 管线部分启用时 fallback 链可互相引用
            # (broll_pexels↔hf_chart, hf_title↔hf_chart↔host), 若不拦截会无限
            # 替换 slot. 同链已试过该 workflow → 强制降级到链尾兜底 black_placeholder.
            if new_slot.workflow in tried:
                logger.warning(
                    "[execute] slot %s fallback 死循环: %s 已试过, 强制降级 black_placeholder",
                    slot.id, new_slot.workflow,
                )
                new_slot.workflow = "black_placeholder"
                new_slot.visual_type = "black_placeholder"
                params = new_slot.params_json or {}
                params["fallback_reason"] = "loop_guard_black"
                new_slot.params_json = params
                db.commit()
            tried.add(new_slot.workflow)
            if new_slot.workflow in ("host", "mixed_host_broll"):
                if c_disabled:
                    # C 线禁用: 不拉起 ComfyUI, 就地降级到启用管线内的最佳替代
                    fallback = _best_fallback_workflow(enabled_pipelines, prefer=new_slot.workflow)
                    params = new_slot.params_json or {}
                    params["fallback_reason"] = "c_pipeline_disabled"
                    params["replaced_from"] = new_slot.workflow
                    new_slot.workflow = fallback
                    new_slot.visual_type = fallback
                    new_slot.params_json = params
                    db.commit()
                    execute_slot(db, new_slot)
                    return
                comfyui_pending.append(new_slot)
                return
            execute_slot(db, new_slot)
            cur = new_slot

    for slot in failed_slots:
        _walk(slot)

    if comfyui_pending:
        logger.info("[execute] %d fallback slot(s) need ComfyUI, opening session", len(comfyui_pending))
        try:
            with manager.session("comfyui"):
                for slot in comfyui_pending:
                    _walk(slot)
        except Exception as gpu_exc:
            logger.error("[execute] ComfyUI fallback session failed: %s", gpu_exc)
            for slot in comfyui_pending:
                if slot.status == "queued":
                    slot.status = "failed"
                    slot.error_code = "GPU_SERVICE_UNAVAILABLE"
                    slot.error_message = str(gpu_exc)[:512]
                    db.commit()


def execute_all_slots(db: Session, job_id: str, *, auto_replace: bool = True, enabled_pipelines: set[str] | None = None) -> DirectorJob:
    """Run all queued slots grouped by workflow type in execution phases.

    Args:
        enabled_pipelines: Set of pipeline letters to execute ({"c", "p", "h"}).
            None = all enabled.  Phase 4 (local/black fallback) always runs.
            Example: {"c", "h"} runs ComfyUI + HF, skips Pexels.
    """
    job = db.get(DirectorJob, job_id)
    if job is None:
        raise ValueError(f"DirectorJob {job_id} not found")

    job.status = "executing"
    job.started_at = _now()
    db.commit()
    append_trace(db, job, "execute", "start",
                 f"开始执行所有 Slots ({sum(1 for s in job.slots if s.status == 'queued')} queued)")
    _evt(job_id, {"type": "exec_start", "msg": "开始执行所有 Slots"})

    manager = get_gpu_service_manager()

    pending = [s for s in job.slots if s.status == "queued"]
    by_wf: dict[str, list[DirectorSlot]] = {}
    for s in pending:
        by_wf.setdefault(s.workflow, []).append(s)

    # ── 防御性检查: host-family slot 数量不得超过配置上限 ──
    # 规划阶段 (director_parser.cap_host_count) 已经做过硬截断；
    # 执行层只做保险，不静默降级，只记录 warning 并在前端提示管理员。
    from app.config import get_config
    max_host = get_config().defaults.max_host_slots
    host_family = {"host", "mixed_host_broll"}
    host_slots = [s for s in pending if s.workflow in host_family]
    if len(host_slots) > max_host:
        logger.error(
            "[execute] 规划层 host 截断失效: %d host-family slots > max %d; "
            "请检查 director_parser.cap_host_count",
            len(host_slots), max_host,
        )
        _evt(
            job_id,
            {
                "type": "plan_warning",
                "msg": f"警告: host-family slots 共 {len(host_slots)} 个，超过硬上限 {max_host}；规划层截断可能未生效",
            },
        )

    all_failed: list[DirectorSlot] = []

    # 解析启用的管线: None = 全部启用, set() = 全关 (只跑兜底)
    if enabled_pipelines is None:
        disabled_phases: set[int] = set()
    else:
        enabled_phases = {_PIPELINE_PHASE_MAP[p] for p in enabled_pipelines if p in _PIPELINE_PHASE_MAP}
        disabled_phases = {0, 1, 2} - enabled_phases

    phase_names = ["ComfyUI (host/mixed)", "Pexels (broll)", "HyperFrames (chart/title)", "本地素材 (local/black)"]
    cancelled = False
    for phase_idx, phase in enumerate(_EXECUTION_PHASES):
        # ── 管线跳过逻辑 ──
        pipeline_tag = phase.get("pipeline")
        if pipeline_tag is not None and phase_idx in disabled_phases:
            tag_label = pipeline_tag.upper()
            logger.info("[execute] 管线 %s 已禁用, 降级 phase %d (%s) 的 slot", tag_label, phase_idx, phase_names[phase_idx] if phase_idx < len(phase_names) else str(phase_idx))

            # 构建 fallback workflow → phase index 快速查找表
            _wf_phase_idx: dict[str, int] = {}
            for _pi, _ph in enumerate(_EXECUTION_PHASES):
                for _wf in _ph["workflows"]:
                    _wf_phase_idx[_wf] = _pi

            downgraded = 0
            skipped = 0
            for wf in phase["workflows"]:
                for s in list(by_wf.get(wf, [])):
                    if s.status != "queued":
                        continue
                    fallback = _best_fallback_workflow(enabled_pipelines, prefer=s.workflow)
                    if fallback == s.workflow:
                        # 所有替代管线也禁用了 → 只能跳过
                        s.status = "skipped"
                        s.error_message = f"管线 {tag_label} 已禁用, 且无可用替代管线"
                        db.commit()
                        skipped += 1
                    else:
                        old_wf = s.workflow
                        s.workflow = fallback
                        s.visual_type = fallback
                        params = s.params_json or {}
                        params["fallback_reason"] = f"{pipeline_tag}_pipeline_disabled"
                        s.params_json = params
                        db.commit()

                        # 检查 fallback 是否回退到已处理过的 phase
                        fb_phase = _wf_phase_idx.get(fallback, 99)
                        if fb_phase < phase_idx:
                            # 回退到已过的 phase → 立即执行此 slot
                            logger.info("[execute] slot %s 降级 %s→%s (回退到 phase %d), 立即执行",
                                        s.id, old_wf, fallback, fb_phase)
                            try:
                                execute_slot(db, s)
                            except Exception:
                                logger.exception("[execute] slot %s 降级即时执行失败", s.id)
                        else:
                            # 加入目标 workflow 桶, 后续 phase 处理
                            by_wf.setdefault(fallback, []).append(s)
                        downgraded += 1

            _evt(job_id, {"type": "phase_skip", "phase": phase_names[phase_idx] if phase_idx < len(phase_names) else "", "phase_idx": phase_idx + 1, "pipeline": pipeline_tag, "msg": f"管线 {tag_label} 已禁用, {downgraded} 降级 / {skipped} 跳过"})
            continue

        # 检查取消标志
        if _is_cancelled(job_id):
            cancelled = True
            logger.info("[execute] Cancel signal detected, stopping after phase %d", phase_idx)
            _evt(job_id, {"type": "exec_cancelled", "msg": "用户取消，停止执行"})
            break
        phase_slots: list[DirectorSlot] = []
        for wf in phase["workflows"]:
            phase_slots.extend(by_wf.get(wf, []))
        if not phase_slots:
            continue

        gpu = phase["gpu"]
        wf_names = ", ".join(sorted(phase["workflows"]))
        p_name = phase_names[phase_idx] if phase_idx < len(phase_names) else wf_names
        logger.info("[execute] Phase %s (%d slots, gpu=%s)", wf_names, len(phase_slots), gpu or "none")
        _evt(job_id, {"type": "phase_start", "phase": p_name, "phase_idx": phase_idx + 1, "slot_count": len(phase_slots), "gpu": gpu or "none"})
        append_trace(db, job, "execute_phase", "start",
                     f"阶段 {phase_idx + 1} {p_name}: {len(phase_slots)} slots (gpu={gpu or 'none'})")

        if gpu:
            _evt(job_id, {"type": "service_start", "phase": p_name, "service": gpu, "msg": f"{gpu} 服务拉起中…"})
            try:
                with manager.session(gpu):
                    _evt(job_id, {"type": "service_ready", "phase": p_name, "service": gpu, "msg": f"{gpu} 就绪"})
                    all_failed.extend(_run_phase(db, phase_slots, auto_replace=auto_replace, job_id=job_id, phase_name=p_name))
                _evt(job_id, {"type": "service_stop", "phase": p_name, "service": gpu, "msg": f"{gpu} 空闲释放"})
            except Exception as gpu_exc:
                logger.error("[execute] GPU service %s failed to start: %s", gpu, gpu_exc)
                _evt(job_id, {"type": "service_stop", "phase": p_name, "service": gpu, "msg": f"{gpu} 启动失败: {str(gpu_exc)[:100]}"})
                # 标记该阶段所有 slot 为 failed，不阻断后续阶段
                for slot in phase_slots:
                    if slot.status == "queued":
                        slot.status = "failed"
                        slot.error_code = "GPU_SERVICE_UNAVAILABLE"
                        slot.error_message = str(gpu_exc)[:512]
                        db.commit()
                        all_failed.append(slot)
                        _evt(job_id, {"type": "slot_fail", "phase": p_name, "slot_index": slot.slot_index, "workflow": slot.workflow, "error": slot.error_message or ""})
        else:
            all_failed.extend(_run_phase(db, phase_slots, auto_replace=auto_replace, job_id=job_id, phase_name=p_name))

        _evt(job_id, {"type": "phase_end", "phase": p_name, "phase_idx": phase_idx + 1})
        ph_failed = sum(1 for s in phase_slots if s.status == "failed")
        append_trace(db, job, "execute_phase", "done" if ph_failed == 0 else "error",
                     f"阶段 {phase_idx + 1} {p_name} 结束: {len(phase_slots)} slots, {ph_failed} 失败")

    if auto_replace and all_failed and not cancelled:
        logger.info("[execute] %d slot(s) failed, running fallback chains", len(all_failed))
        _evt(job_id, {"type": "phase_start", "phase": "Fallback 兜底", "phase_idx": 99, "slot_count": len(all_failed), "gpu": "on_demand"})
        append_trace(db, job, "fallback", "start", f"Fallback 兜底: {len(all_failed)} 个失败 slot")
        _handle_fallbacks(db, all_failed, job_id=job_id, enabled_pipelines=enabled_pipelines)
        _evt(job_id, {"type": "phase_end", "phase": "Fallback 兜底", "phase_idx": 99})
        fbd_failed = sum(1 for s in all_failed if s.status == "failed")
        append_trace(db, job, "fallback", "done" if fbd_failed == 0 else "error",
                     f"Fallback 兜底结束, 仍失败 {fbd_failed}/{len(all_failed)}")

    _cancel_flags.discard(job_id)  # 清除取消标志
    # 强制停止兜底: 后台线程返回时若已被 force-stop, 跳过 exec_done 事件与
    # complete_job_if_slots_done — 终态已由端点写入, 防止覆盖/误导.
    if is_force_stopped(job_id):
        logger.info("[execute] job %s force-stopped, skip terminal state write", job_id)
        return job
    _evt(job_id, {"type": "exec_done", "msg": "所有 Slots 执行完毕"})
    append_trace(db, job, "execute", "done", "所有 Slots 执行完毕")
    from app.services.director_service import complete_job_if_slots_done
    return complete_job_if_slots_done(db, job_id)
