"""DigitalHumanVideo service — LTX23 音频+分镜 → MP4 的 6 步编排与纯逻辑.

从 `app/routers/digital_human_video.py::generate_video` 下沉.
错误消息逐字保留 (ComfyUI 不可达/HTTP/响应异常/task errored/task timeout/无 mp4/...).
DB session 由调用方传入; ORM 对象以 Any 标注 (避免顶层依赖 app.models).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.audio_aggregator import aggregate_segments
from app.services.comfyui_client import poll_history, submit_prompt
from app.services.lit_video_builder import build_ltx23_video_workflow
from app.services.video_validator import validate_mp4

logger = logging.getLogger(__name__)

__all__ = ["dhv_root", "now", "generate_dhv_video"]

_COMFY_INPUT = Path(r"E:/AI/ComfyUI_windows_portable/ComfyUI/input")
_COMFY_OUTPUT = Path(r"E:/AI/ComfyUI_windows_portable/ComfyUI/output")
_COMFY_TEMP = Path(r"E:/AI/ComfyUI_windows_portable/ComfyUI/temp")


def dhv_root() -> Path:
    """DigitalHumanVideo 产物根目录 (E:/数字人计划/dhv)."""
    return Path(r"E:/数字人计划/dhv")


def now() -> datetime:
    """无参无时区 utcnow (与模型默认一致)."""
    return datetime.utcnow()


def find_mp4_output(history_entry: dict[str, Any]) -> Path | None:
    """从 ComfyUI /history outputs 找到第一个 .mp4 产物.

    VHS_VideoCombine 产物形态:
      outputs: {"800": {"gifs": [{"filename": "xxx.mp4", "type": "output", "subfolder": ""}]}}
    """
    outputs = history_entry.get("outputs") or {}
    for _nid, payload in outputs.items():
        for key in ("gifs", "images", "files"):
            for f in payload.get(key) or []:
                fn = f.get("filename", "")
                if fn.lower().endswith((".mp4", ".mov", ".webm")):
                    sub = f.get("subfolder", "")
                    base = {"output": _COMFY_OUTPUT, "input": _COMFY_INPUT}.get(
                        f.get("type", "output"), _COMFY_TEMP
                    )
                    cand = base / sub / fn if sub else base / fn
                    if cand.exists():
                        return cand
    return None


def aggregate_audio(v: Any, out_root: Path) -> tuple[dict[str, Any], Path]:
    """聚合音频源到 audio_aggregated.wav, 返回 (aggregate_segments 结果, agg_path)."""
    agg_path = out_root / "audio_aggregated.wav"
    return (
        aggregate_segments(
            v.audio_source_paths,
            target_duration_sec=v.target_duration_sec,
            min_duration_sec=5.0,
            output_path=agg_path,
            silence_gap_sec=0.3,
        ),
        agg_path,
    )


def copy_assets_to_comfy(
    video_id: str, audio_src: Path, storyboard_paths: list[str]
) -> tuple[Path, list[dict[str, Any]], list[str]]:
    """拷贝 audio + storyboard 到 ComfyUI input/, 返回 (audio_dst, storyboard_for_wf, issues)."""
    comfy_input = _COMFY_INPUT
    comfy_input.mkdir(parents=True, exist_ok=True)
    audio_dst = comfy_input / f"dhv_{video_id}_{audio_src.name}"
    shutil.copy2(str(audio_src), str(audio_dst))

    storyboard_for_wf: list[dict[str, Any]] = []
    issues: list[str] = []
    for i, src in enumerate(storyboard_paths):
        src_p = Path(src)
        if not src_p.exists():
            issues.append(f"storyboard file missing: {src}")
            continue
        img_dst = comfy_input / f"dhv_{video_id}_sb{i}_{src_p.name}"
        shutil.copy2(str(src_p), str(img_dst))
        storyboard_for_wf.append(
            {"filename": img_dst.name, "frame_idx": i * 60, "strength": 0.85}
        )
    return audio_dst, storyboard_for_wf, issues


def persist_mp4(history_entry: dict[str, Any], out_root: Path, video_id: str) -> Path:
    """移动 ComfyUI 首个 mp4 产物到 out_root, 返回目标路径."""
    mp4_src = find_mp4_output(history_entry)
    if not mp4_src or not mp4_src.exists():
        raise RuntimeError(f"ComfyUI outputs 中无 mp4: {history_entry.get('outputs')}")
    mp4_dst = out_root / f"{video_id}.mp4"
    try:
        os.replace(str(mp4_src), str(mp4_dst))
    except OSError:
        shutil.copy2(str(mp4_src), str(mp4_dst))
        mp4_src.unlink(missing_ok=True)
    return mp4_dst


def validate_output(v: Any, mp4_dst: Path) -> dict[str, Any]:
    """ffprobe 校验 + 回写 v 的验证元数据, 返回 {val, issues, warnings}."""
    val = validate_mp4(
        mp4_dst,
        expected_duration=v.target_duration_sec,
        expected_fps=v.fps,
        min_duration=5.0,
    )
    issues = list(val["issues"])
    warnings = list(val.get("warnings") or [])
    meta = val["meta"] or {}
    v.duration_actual = meta.get("duration_actual")
    v.fps_actual = meta.get("fps_actual")
    v.has_audio_stream = bool(meta.get("has_audio_stream"))
    v.frame_count = meta.get("frame_count")
    if warnings:
        v.comfy_log = {**(v.comfy_log or {}), "validation_warnings": warnings}
    return {"val": val, "issues": issues, "warnings": warnings}


def _response_dict(
    v: Any,
    *,
    validation_issues: list[str],
    validation_warnings: list[str],
    elapsed_sec: float,
    error: str | None,
) -> dict[str, Any]:
    """构造 GenerateVideoResponse 字段 dict (router 用 ** 展开)."""
    return {
        "video_id": v.id,
        "status": v.status,
        "prompt_id": v.prompt_id,
        "output_video_path": v.output_video_path,
        "aggregated_audio_path": v.aggregated_audio_path,
        "aggregated_duration_sec": v.aggregated_duration_sec,
        "duration_actual": v.duration_actual,
        "fps_actual": v.fps_actual,
        "has_audio_stream": v.has_audio_stream,
        "frame_count": v.frame_count,
        "validation_issues": validation_issues,
        "validation_warnings": validation_warnings,
        "elapsed_sec": elapsed_sec,
        "error": error,
    }


async def generate_dhv_video(db: Any, v: Any, cfg: Any) -> dict[str, Any]:
    """6 步编排(行为不变): 聚合→workflow→提交→轮询→落盘→ffprobe 校验.

    issues 全程追踪, 异常兜底返回最新的 issues (原 router 行为).
    """
    issues: list[str] = []
    t0 = time.time()
    try:
        out_root = dhv_root() / v.id
        out_root.mkdir(parents=True, exist_ok=True)
        agg, agg_path = aggregate_audio(v, out_root)
        if not agg["ok"]:
            v.status = "failed"
            v.error_message = " | ".join(agg["issues"])
            db.commit()
            return _response_dict(
                v, validation_issues=agg["issues"], validation_warnings=[],
                elapsed_sec=time.time() - t0, error=v.error_message,
            )
        v.aggregated_audio_path = str(agg_path)
        v.aggregated_duration_sec = agg["actual_duration_sec"]
        db.commit()

        audio_dst, sb_wf, missing = copy_assets_to_comfy(v.id, agg_path, v.storyboard_paths)
        issues = missing
        if not sb_wf:
            raise RuntimeError("no storyboard images available")
        workflow = build_ltx23_video_workflow(
            audio_filename=audio_dst.name, storyboard=sb_wf,
            duration_sec=v.target_duration_sec, fps=v.fps,
            orientation="landscape" if v.width > v.height else "portrait",
            seed=v.seed, filename_prefix=f"dhv_{v.id}_",
        )

        base_url = cfg.defaults.base_url_comfyui.rstrip("/")
        prompt_id, client_id = await submit_prompt(base_url, workflow)
        v.prompt_id = prompt_id
        v.comfy_log = {"submitted_at": time.time(), "client_id": client_id}
        db.commit()
        logger.info("[dhv %s] submitted prompt_id=%s", v.id, prompt_id)

        history_entry = await poll_history(
            base_url, prompt_id, cfg.defaults.comfyui_timeout_sec
        )
        mp4_dst = persist_mp4(history_entry, out_root, v.id)
        v.output_video_path = str(mp4_dst)

        out = validate_output(v, mp4_dst)
        issues = out["issues"]  # 校验通过后 issues 反映最新 (原 router 行为)
        if out["val"]["ok"]:
            v.status = "completed"
            v.completed_at = now()
        else:
            v.status = "validation_failed"
            v.error_message = " | ".join(out["issues"])
        db.commit()
        db.refresh(v)
        return _response_dict(
            v, validation_issues=out["issues"], validation_warnings=out["warnings"],
            elapsed_sec=time.time() - t0,
            error=v.error_message if v.status != "completed" else None,
        )

    except Exception as exc:
        logger.exception("[dhv %s] generate failed", v.id)
        v.status = "failed"
        v.error_message = str(exc)[:1000]
        db.commit()
        db.refresh(v)
        return _response_dict(
            v, validation_issues=issues, validation_warnings=[],
            elapsed_sec=time.time() - t0, error=v.error_message,
        )
