"""LTX 2.3 音频→视频 workflow builder — 复用预置双版 ComfyUI workflow JSON.

为什么复用 JSON 而非 Python 从 0 构造:
  - ComfyUI 端 workflow 是 SSOT (Hermes 已手动验证: 105s 渲染 9.96s/239 帧/24fps)
  - Python 构造节点 schema 极易跟 ComfyUI 实际 custom node 不匹配 (2026-07-26 P0-3 教训)
  - 节点 ID / class_type / inputs 全是 ComfyUI 实际产物,不允许假设

约定:
  - 读取项目内 digital_human/data/workflows/ltx23_video_{orientation}.json
  - 改 5 个字段: node 36 帧数 / node 39 fps / node 301 图片 / node 423 音频 / node 369 前缀
  - 返回 dict-format workflow {node_id(str): {class_type, inputs}}
  - 帧数公式: round(duration*fps)+1, 对齐 8n+1
  - 分辨率由 ImageScale node 440 在 JSON 模板中预置,builder 不再改写
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_WORKFLOW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "workflows"


def _resolve_workflow_path(orientation: str) -> Path:
    """解析 workflow JSON 路径 — 项目内 data/workflows/ 目录."""
    if orientation not in ("portrait", "landscape"):
        raise ValueError(f"orientation 必须为 'portrait' 或 'landscape', 收到: {orientation!r}")
    path = _WORKFLOW_DIR / f"ltx23_video_{orientation}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"LTX23 workflow 模板不存在: {path}\n"
            f"请确保 digital_human/data/workflows/ 下存在两份 JSON 模板"
        )
    return path


def load_base_workflow(orientation: str = "portrait") -> dict[str, dict[str, Any]]:
    """读取预置 workflow dict (顶层 key 是字符串节点 ID)."""
    path = _resolve_workflow_path(orientation)
    with open(path, "r", encoding="utf-8") as f:
        wf = json.load(f)
    if not isinstance(wf, dict):
        raise ValueError(f"workflow 顶层必须是 dict,实际 {type(wf).__name__}")
    # 校验关键节点都存在
    for nid in ("36", "39", "301", "423", "369", "440"):
        if nid not in wf:
            raise KeyError(f"workflow 缺 node {nid},实际节点 {sorted(wf.keys())[:10]}...")
    return wf


def build_ltx23_video_workflow(
    *,
    audio_filename: str,
    storyboard: list[dict[str, Any]],
    duration_sec: float,
    orientation: str = "portrait",
    fps: int = 30,
    seed: int | None = None,   # noqa: ARG001 — workflow 内已固化 sampler seed,builder 不改
    ckpt_name: str = "ltx-2.3-22b-distilled-q8_0.gguf",  # noqa: ARG001
    audio_vae_name: str = "ltx-2.3-audio-vae.safetensors",  # noqa: ARG001
    dual_clip_name1: str = "t5xxl_fp8mixed.safetensors",  # noqa: ARG001
    dual_clip_name2: str = "t5xxl_fp8mixed.safetensors",  # noqa: ARG001
    dual_clip_type: str = "ltxv",  # noqa: ARG001
    filename_prefix: str = "dhv_",
    steps: int = 20,  # noqa: ARG001
    cfg: float = 1.5,  # noqa: ARG001
    sampler_name: str = "euler",  # noqa: ARG001
    scheduler: str = "simple",  # noqa: ARG001
    positive_prompt: str | None = None,  # noqa: ARG001
    negative_prompt: str | None = None,  # noqa: ARG001
) -> dict[str, dict[str, Any]]:
    """复用预置 workflow JSON 模板, 改 5 个参数后返回.

    Args:
        audio_filename: ComfyUI input/ 下已就位的 wav 文件名 (例如 'test_audio.wav')
        storyboard:     [{'filename': '正视图_00002_.png'}, ...] — 当前只取第 1 张
        duration_sec:   目标时长(秒)
        orientation:    'portrait'(9:16) | 'landscape'(16:9) — 选择对应 JSON 模板
        fps:            帧率(默认 30, 写入 node 39)
        filename_prefix: 输出文件名前缀 (默认 'dhv_')

    Returns:
        dict-format workflow, 顶层 key 是字符串节点 ID
    """
    frame_count = _frame_count(duration_sec, fps)

    wf = load_base_workflow(orientation)

    # 参数 1: node 36 INTConstant value = frame_count
    wf["36"]["inputs"]["value"] = int(frame_count)

    # 参数 2: node 39 INTConstant value = fps (写回 workflow 模板; 模板默认 30)
    wf["39"]["inputs"]["value"] = int(fps)

    # 参数 3: node 301 LoadImage image = 第一张分镜图
    if not storyboard:
        raise ValueError("storyboard 不能为空,至少需要 1 张图")
    first_sb = storyboard[0]
    image_filename = first_sb.get("filename")
    if not image_filename:
        raise ValueError(f"storyboard[0] 缺 filename: {first_sb}")
    wf["301"]["inputs"]["image"] = image_filename

    # 参数 4: node 423 LoadAudio audio
    wf["423"]["inputs"]["audio"] = audio_filename

    # 参数 5: node 369 SaveVideo filename_prefix
    wf["369"]["inputs"]["filename_prefix"] = str(filename_prefix)

    return wf


def _frame_count(duration_sec: float, fps: int) -> int:
    """round(duration * fps) + 1, 对齐 8n+1.

    例: 10s @ 30fps → round(300)+1 = 301, 对齐 → 305 (8*38+1)
    """
    n = round(duration_sec * fps) + 1
    while (n - 1) % 8 != 0:
        n += 1
    if n < 9:
        n = 9
    return n
