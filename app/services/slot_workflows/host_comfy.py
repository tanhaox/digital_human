"""Host 线 ComfyUI 交互: 提交 prompt / 中断 / 心跳 / 轮询收集.

轮询时 ``_is_cancelled`` 从 slot_executor 延迟导入 (slot_executor 顶层 import
WORKFLOW_HANDLERS, 模块级导入会成环), ``publish`` 从 director_events 延迟导入。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from app.config import get_config

logger = logging.getLogger(__name__)

__all__ = [
    "poll_comfyui_and_collect",
    "_find_comfy_output",
    "_submit_comfyui_workflow",
    "_send_comfyui_interrupt",
    "_emit_heartbeat",
    "_check_cancel",
    "_maybe_emit_heartbeat",
]


def _submit_comfyui_workflow(cfg, workflow: dict) -> str:
    """POST the prompt to ComfyUI; return prompt_id or raise RuntimeError."""
    comfy_url = f"{cfg.defaults.base_url_comfyui}/prompt"
    try:
        r = httpx.post(comfy_url, json={"prompt": workflow}, timeout=10)
        r.raise_for_status()
        prompt_id = r.json().get("prompt_id")
    except Exception as exc:
        raise RuntimeError(f"ComfyUI submit failed: {exc}") from exc
    if not prompt_id:
        raise RuntimeError("ComfyUI did not return prompt_id")
    return prompt_id


def _send_comfyui_interrupt(cfg, job_id: str) -> None:
    """POST /interrupt to ComfyUI when the user cancels."""
    try:
        httpx.post(f"{cfg.defaults.base_url_comfyui}/interrupt", timeout=5)
        logger.info("[comfyui] interrupt sent for job %s", job_id)
    except Exception:
        pass


def _emit_heartbeat(job_id: str, slot_index: int | None, elapsed: int) -> None:
    """Send slot_progress heartbeat so the UI does not go silent."""
    from app.services.director_events import publish as _evt

    _evt(job_id, {
        "type": "slot_progress",
        "phase": "ComfyUI (host/mixed)",
        "slot_index": slot_index,
        "workflow": "host",
        "msg": f"ComfyUI 生成中… 已 {elapsed}s",
        "elapsed_sec": elapsed,
    })


def _find_comfy_output(data: dict, prompt_id: str, prefix: str, cfg) -> Path | None:
    """Locate the prefixed .mp4 in ComfyUI history outputs (zero-copy)."""
    outputs = data.get(prompt_id, {}).get("outputs", {})
    for node_outputs in outputs.values():
        for item in node_outputs.get("gifs", node_outputs.get("images", [])):
            filename = item.get("filename", "")
            if filename.startswith(prefix) and filename.endswith(".mp4"):
                comfy_output = Path(cfg.defaults.comfyui_output_dir) / filename
                if comfy_output.exists():
                    return comfy_output
    return None


def _check_cancel(cfg, job_id: str) -> None:
    """If the job is cancelled, send interrupt and abort the poll loop."""
    if not job_id:
        return
    from app.services.slot_executor import _is_cancelled

    if _is_cancelled(job_id):
        _send_comfyui_interrupt(cfg, job_id)
        raise RuntimeError("用户取消，ComfyUI 已发送 interrupt")


def _maybe_emit_heartbeat(
    job_id: str, slot_index: int | None,
    last_heartbeat: float, start: float, now: float,
) -> float:
    """Emit slot_progress heartbeat at most every 10s; returns new last_heartbeat."""
    if not job_id or now - last_heartbeat < 10.0:
        return last_heartbeat
    _emit_heartbeat(job_id, slot_index, int(now - start))
    return now


def poll_comfyui_and_collect(
    *, prompt_id: str, history_url: str, prefix: str,
    timeout_sec: int, poll_interval: float = 2.0,
    job_id: str = "",
    slot_index: int | None = None,
) -> Path:
    """Poll ComfyUI history until prompt is done, then find output video.

    Checks cancel flag each iteration; sends POST /interrupt if cancelled.
    Emits slot_progress heartbeat every ~10s so the UI does not go silent.
    """
    cfg = get_config()
    deadline = time.monotonic() + timeout_sec
    start = time.monotonic()
    last_heartbeat = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        _check_cancel(cfg, job_id)
        last_heartbeat = _maybe_emit_heartbeat(job_id, slot_index, last_heartbeat, start, now)
        try:
            r = httpx.get(history_url, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("ComfyUI history poll error: %s", exc)
            time.sleep(poll_interval)
            continue
        found = _find_comfy_output(data, prompt_id, prefix, cfg)
        if found:
            return found  # 零搬运 — 直接返回 ComfyUI output 原始路径
        time.sleep(poll_interval)
    raise RuntimeError(f"ComfyUI timeout after {timeout_sec}s for prompt {prompt_id}")
