"""Host / mixed workflow: 角色图解析 + 输入准备 + workflow 构建 (C 线).

ComfyUI 提交/轮询见 host_comfy.py, 分镜图 QA 见 host_qa.py。
注意: 本模块只做组装, 不与 slot_executor 互导 → 无循环导入。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_config
from app.infrastructure import extract_audio_slice_wav
from app.models import DirectorSlot, Persona
from app.schemas import get_video_format_spec
from app.services.lit_video_builder import build_ltx23_video_workflow
from app.services.slot_workflows.common import audio_slice_path, ensure_slot_dir
from app.services.slot_workflows.host_comfy import (
    _submit_comfyui_workflow,
    poll_comfyui_and_collect,
)
from app.services.slot_workflows.host_qa import prepare_comfyui_storyboard

logger = logging.getLogger(__name__)

__all__ = [
    "execute_host_slot",
    "_resolve_host_role",
    "_pick_storyboard_image",
    "_prepare_host_inputs",
    "_build_host_workflow",
]


def _resolve_host_role(db: Session, slot: DirectorSlot) -> tuple[list, str | None]:
    """Resolve view_groups + reference_image via Host → Persona → Role chain.

    2026-08-08 整合: Persona 显式 host_id FK 1:1 绑定 Host (修复旧逻辑
    prompt_template 模糊匹配的弱关联断裂). 取数优先级:
      1. host.reference_image (Host 直挂主参考图)
      2. persona.role.view_groups / reference_image (人物页绑定的形象)
    """
    role = slot.director_job.script.host
    if not role:
        return [], None

    reference_image = role.reference_image
    view_groups = getattr(role, "view_groups", None) or []
    # 通过显式 host_id 查找绑定的 Persona → Role
    if not view_groups:
        persona = (
            db.query(Persona).filter(Persona.host_id == role.id).first()
        )
        if persona and persona.role:
            view_groups = persona.role.view_groups or []
            # 如果 Host 没有 reference_image，也用 Role 的
            if not reference_image and persona.role.reference_image:
                reference_image = persona.role.reference_image
    return view_groups, reference_image


def _pick_storyboard_image(
    slot: DirectorSlot, view_groups: list, reference_image: str | None,
) -> Path | None:
    """Select camera image from view_groups; fall back to reference_image.

    与原逻辑一致: view_groups 取到图但文件不存在 (或未取到) 时,
    只要 reference_image 存在即回退到它; 最终存在性由调用方再校验。
    """
    candidate: Path | None = None
    if view_groups:
        group_idx = slot.view_group_index or 0
        cam_key = str(slot.camera_angle or 1)
        if group_idx < len(view_groups):
            cameras = view_groups[group_idx].get("cameras") or {}
            img_path = cameras.get(cam_key)
            if img_path:
                candidate = Path(img_path)
    if (candidate is None or not candidate.exists()) and reference_image:
        return Path(reference_image)
    return candidate


def _prepare_host_inputs(
    cfg, spec: dict, uniq: str, audio_path: Path,
    slot: DirectorSlot, storyboard_image: Path,
) -> tuple[Path, Path]:
    """Write WAV slice + QA'd storyboard into ComfyUI input dir."""
    comfy_input = Path(cfg.defaults.comfyui_input_dir)
    comfy_input.mkdir(parents=True, exist_ok=True)

    slot_wav = comfy_input / f"{uniq}_audio.wav"
    extract_audio_slice_wav(audio_path, slot_wav, slot.start_sec, slot.end_sec)

    prepared_sb = prepare_comfyui_storyboard(
        storyboard_image,
        target_w=spec["comfyui_w"],
        target_h=spec["comfyui_h"],
    )
    sb_comfy = comfy_input / f"{uniq}_sb.png"
    shutil.copy2(prepared_sb, sb_comfy)
    return slot_wav, sb_comfy


def _build_host_workflow(
    cfg, uniq: str, slot: DirectorSlot, spec: dict, sb_comfy: Path, slot_wav: Path,
) -> dict:
    """Build LTX23 workflow with storyboard + audio slice (square → portrait)."""
    duration = round(slot.end_sec - slot.start_sec, 3)
    orientation = slot.director_job.video_format or "portrait"
    if orientation not in ("portrait", "landscape"):
        orientation = "portrait"
    return build_ltx23_video_workflow(
        audio_filename=slot_wav.name,
        storyboard=[{"filename": sb_comfy.name}],
        duration_sec=duration, fps=30,
        orientation=orientation,
        filename_prefix=f"{uniq}_",
    )


def execute_host_slot(db: Session, slot: DirectorSlot) -> str:
    """Generate host video via LTX23 for the slot, returning mp4 path."""
    ensure_slot_dir(slot)
    cfg = get_config()
    spec = get_video_format_spec(slot.director_job.video_format)

    audio_path = audio_slice_path(slot.director_job)
    if not audio_path or not audio_path.exists():
        raise RuntimeError("whole audio missing for host slot")

    view_groups, reference_image = _resolve_host_role(db, slot)
    storyboard_image = _pick_storyboard_image(slot, view_groups, reference_image)
    if storyboard_image is None or not storyboard_image.exists():
        raise RuntimeError("host slot requires role reference_image or view_groups camera image")

    uniq = f"slot_{slot.director_job_id[:8]}_{slot.slot_index:03d}"
    slot_wav, sb_comfy = _prepare_host_inputs(cfg, spec, uniq, audio_path, slot, storyboard_image)
    workflow = _build_host_workflow(cfg, uniq, slot, spec, sb_comfy, slot_wav)
    prompt_id = _submit_comfyui_workflow(cfg, workflow)

    # 保留 ComfyUI 原生音频 — LTX23 已根据 WAV 切片生成了口型同步的视频+音频，
    # 不再用 TTS 切片替换，避免 TTS 切片边界累积误差导致影音不同步。
    return str(poll_comfyui_and_collect(
        prompt_id=prompt_id,
        history_url=f"{cfg.defaults.base_url_comfyui}/history/{prompt_id}",
        prefix=f"{uniq}_",
        timeout_sec=cfg.defaults.comfyui_timeout_sec,
        job_id=slot.director_job_id,
        slot_index=slot.slot_index,
    ))
