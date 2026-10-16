# -*- coding: utf-8 -*-
"""r2v 视频工位 (1009 产线接入) — 参考图生视频, 身份零漂.

架构: 工位矩阵 · 路由表在 h3.run_batch 层 (本模块不选路, 只执行).
配方: minimax_h3_ref2va_pruned_int8 + ref2v_turbo_4step 弹匣 @1.0
      + MiniMaxH3TurboSampler + BasicScheduler(beta, 4步) — 已验证 44s/镜 (⑤b).
双档: 量产 1024×576 (~44s) / 精品 1344×768 (~89s, CU/ECU).
参考: 场景=K2 贴膜帧 (shot.image_file), 人物=keyframe_zh 匹配的定妆照.
      API 参考图必须点分命名 ref_images.ref_image_0 (0起) — 接线坑已录档.
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from . import (
    comfy,
    video_qc,  # 视频产物机检 (观察模式, 同 h3)
)
from . import ledger as _ledger
from . import shots as shots_mod

logger = logging.getLogger(__name__)

# ── 配方常量 (⑤b 定稿) ──
MODEL = r"minimax_r2v\minimax_h3_ref2va_pruned_int8_convrot.safetensors"
LORA = r"H3\minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
CLIP = r"minimax\qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
VAE_V = r"minimax\minimax_h3_video_vae_fp16.safetensors"
VAE_A = r"minimax\minimax_h3_audio_vae_fp32.safetensors"

# 量产档 / 精品档
PROFILE = {
    "standard": {"width": 1024, "height": 576},   # ~44s/镜
    "premium": {"width": 1344, "height": 768},    # ~89s/镜 (CU/ECU)
}

# 人物匹配表 (keyframe_zh 含名字 → 定妆照 png 名)
_CHAR_SHEETS: dict[str, str] | None = None


def _char_sheet_map(book_title: str) -> dict[str, str]:
    """圣经角色名 → 定妆照文件名 (进程缓存)."""
    global _CHAR_SHEETS
    if _CHAR_SHEETS is not None:
        return _CHAR_SHEETS
    try:
        import json

        from .director2 import _bible_cache_path
        bible = json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
        sheets_dir = shots_mod.anim_output_root() / "_资产" / "角色设计" / book_title
        m: dict[str, str] = {}
        for c in bible.get("characters") or []:
            name = str(c.get("name") or "").strip()
            if not name:
                continue
            png = sheets_dir / f"{name}.png"
            if png.exists():
                m[name] = str(png)
        _CHAR_SHEETS = m
        logger.info("[r2v] 角色定妆照 %d 张就绪: %s", len(m), "/".join(m.keys()))
    except Exception:  # noqa: BLE001 — 无圣经/无照 = 无人物参考, 不炸
        _CHAR_SHEETS = {}
    return _CHAR_SHEETS


def _match_chars(keyframe_zh: str, book_title: str, max_refs: int = 3) -> list[str]:
    """keyframe_zh 匹配角色名 → 定妆照路径列表."""
    m = _char_sheet_map(book_title)
    out = []
    for name, path in m.items():
        if name in keyframe_zh and len(out) < max_refs:
            out.append(path)
    return out


def _build_prompt(shot: dict[str, Any], char_names: list[str],
                  book_title: str = "") -> str:
    """r2v prompt — 谁 + 在哪 + 做什么; 风格/运动全跟参考帧走 (权限定律).
    1012: 书级动画律 (圣经 motion_style) 同源注入 — 参考帧锁长相, 动画律锁运动气质
    (H3 侧同款, 双工位一口径)。"""
    a = shot.get("anim") or {}
    beats = " ".join(
        str(b.get("motion") or "").strip() for b in (a.get("beats") or [])
    )
    opening = str(a.get("opening_desc") or "").strip()

    prompt = f"{opening} {beats} "

    if char_names:
        prompt += (
            "Each person keeps his own face, hair, glasses and outfit exactly "
            "consistent with his own reference image. "
        )

    prompt += (
        "The scene stays consistent with the scene reference image. "
        "Camera stays stable, only a very slight push-in. "
        "No text, no human voice."
    )
    from .h3 import motion_style  # 惰性: h3 顶部引 brand, 避免环
    _motion = motion_style(book_title)
    if _motion:
        prompt += (f" Motion style law for the whole video: {_motion} "
                   "— every beat obeys this motion language.")
    from .h3 import era_anchor as _era_fn  # 1012 场景时代锁 (同 h3, 治风格卡场景越权)
    _era = _era_fn(book_title)
    if _era:
        prompt += (f" Scene era lock (overrides any style wording): {_era} "
                   "— costumes and environments obey this and the reference "
                   "images; the art style governs texture ONLY, never re-stages "
                   "the scene into its stereotypical setting.")
    return prompt


def _build_workflow(prompt: str, ref_paths: list[str], scene_path: Path,
                    width: int, height: int, length: int, seed: int) -> dict[str, Any]:
    """r2v ComfyUI 工作流 (API 格式) — ep1_r2v_batch.py 已验证配方."""
    # 上传参考图 (人物照 ×N + 场景帧)
    load_nodes: dict[str, Any] = {}
    ref_inputs: dict[str, list] = {}
    all_refs = ref_paths + [str(scene_path)]
    for i, rp in enumerate(all_refs):
        name = comfy.upload_image(Path(rp))
        nid = str(140 + i)
        load_nodes[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
        ref_inputs[f"ref_images.ref_image_{i}"] = [nid, 0]

    return {
        "129": {"class_type": "UNETLoader", "inputs": {
            "unet_name": MODEL, "weight_dtype": "default"}},
        "348": {"class_type": "ModelAttentionBackend", "inputs": {
            "attention": "comfy kitchen attention", "model": ["129", 0]}},
        "244": {"class_type": "MiniMaxLowVRAMAttention", "inputs": {
            "head_chunks": 10, "model": ["348", 0]}},
        "243": {"class_type": "MiniMaxChunkFeedForward", "inputs": {
            "chunks": 2, "seq_threshold": 4096, "model": ["244", 0]}},
        "341": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "lora_name": LORA, "strength_model": 1.0, "model": ["243", 0]}},
        "130": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": CLIP, "type": "minimax", "device": "default"}},
        "121": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_V}},
        "122": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_A}},
        **load_nodes,
        "133": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "clip": ["130", 0], "vae": ["121", 0], "audio_vae": ["122", 0],
            "prompt": prompt, "width": width, "height": height, "length": length,
            "ref_image_size": "match", **ref_inputs}},
        "128": {"class_type": "BasicGuider", "inputs": {
            "model": ["341", 0], "conditioning": ["133", 0]}},
        "131": {"class_type": "RandomNoise", "inputs": {
            "noise_seed": seed, "control_after_generate": "fixed"}},
        "221": {"class_type": "MiniMaxH3TurboSampler", "inputs": {}},
        "361": {"class_type": "BasicScheduler", "inputs": {
            "model": ["341", 0], "scheduler": "beta", "steps": 4, "denoise": 1.0}},
        "127": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["131", 0], "guider": ["128", 0], "sampler": ["221", 0],
            "sigmas": ["361", 0], "latent_image": ["133", 1]}},
        "124": {"class_type": "VAEDecode", "inputs": {
            "samples": ["127", 0], "vae": ["121", 0]}},
        "138": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["124", 0], "frame_rate": 24, "loop_count": 0,
            "filename_prefix": "r2v_auto", "format": "video/h264-mp4",
            "pix_fmt": "yuv420p", "crf": 19, "save_metadata": True,
            "trim_to_audio": False, "pingpong": False, "save_output": True}},
    }


def _profile_for(shot: dict[str, Any]) -> str:
    """路由档位: 法层 size=CU/ECU → 精品, 其余量产."""
    fa = shot.get("fa") or {}
    size = str(fa.get("size") or "").upper()
    return "premium" if size in ("CU", "ECU", "MCU") else "standard"


def run_shot(shot: dict[str, Any], doc: dict[str, Any]) -> Path:
    """跑单镜 r2v: K2 贴膜帧 + 定妆照参考 → mp4, 状态 → anim_done.

    接口同 h3.run_shot (编排器可直换). 产线配方=⑤b 定稿 (4步弹匣 44s/镜).
    """
    t0 = time.monotonic()
    base_dir = shots_mod.ep_dir(doc["book_title"], doc["ep"])
    dest = base_dir / "anim" / f"{shot['shot_id']}.mp4"

    # 首帧 (K2 已产)
    src_img = base_dir / str(shot.get("image_file") or "")
    if not src_img.exists():
        raise comfy.ComfyRunError(f"首帧图不存在: {src_img}")

    # 匹配人物定妆照
    char_paths = _match_chars(
        str(shot.get("keyframe_zh") or ""), doc["book_title"])

    # prompt + 配方
    char_names = [Path(cp).stem for cp in char_paths]
    prompt = _build_prompt(shot, char_names, book_title=doc["book_title"])
    prof = PROFILE[_profile_for(shot)]

    # 帧数 (同 h3 公式)
    span = float(shot["t_end"]) - float(shot["t_start"])
    length = max(5, round(span * 24))
    length = min(362, length + (5 - (length % 17)) % 17)

    wf = _build_workflow(
        prompt, char_paths, src_img,
        prof["width"], prof["height"], length,
        int(shot.get("h3_seed") or 711))

    comfy.run_workflow(
        wf, dest=dest, timeout_sec=600 + int(240 * min(span, 10.0)),
        label=f"r2v {shot['shot_id']}",
        soft_timeout_sec=max(90 * min(span, 10.0) + 120, 300),
        interrupt_grace_sec=180)

    # 同 h3: 状态推进 + 指纹 + 机检 + ledger
    shot["video_file"] = f"anim/{shot['shot_id']}.mp4"
    shot["gen_narr_sha"] = hashlib.md5(
        shots_mod.norm_for_match(shot.get("narration") or "").encode("utf-8")
    ).hexdigest()[:10]
    shot.pop("video_suspect", None)
    _sus = video_qc.check(dest, shot, doc)
    if _sus:
        shot["video_suspect"] = _sus
        _r = shot.setdefault("qc_rejects", [])
        _r.append({"kind": ",".join(_sus.get("kinds") or []),
                   "what": str(_sus.get("what") or "")[:60],
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        del _r[:-8]
    shot["attempts"]["h3"] += 1
    shot["error"] = None
    shots_mod.transition(shot, "anim_done")
    _ledger.record(doc["book_title"], doc["ep"], "r2v", shot["shot_id"],
                   trigger=str(doc.get("_ledger_trigger") or ""),
                   ms=int((time.monotonic() - t0) * 1000))
    logger.info("[r2v] %s done (%d 人物参考, %s档, %.0fs)",
                shot["shot_id"], len(char_paths), _profile_for(shot),
                time.monotonic() - t0)
    return dest
