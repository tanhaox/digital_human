# -*- coding: utf-8 -*-
"""图内文字入字 pass (1014 验证通过) — K2 干净底图 → Qwen-Edit 2511 入中文字.

配方 (comfyuitext/workflows/qwen-zwtest v1.6 验证): 2511 fp8 + EditPlus +
cfg 2.5 + 20步 + shift 3.1 + 1328×1328. 关键参数是 cfg 2.5 (不是 1.0).

前置: writer 在画面句中把文字面写为"空白" + in_image_text 字段给出文字行。
本 pass 在 K2 出图后自动运行, 将文字写入对应位置, 产出最终图 (H3 Glyph lock
会锁字全程稳定)。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COMFY = "http://127.0.0.1:8188"

UNET = "qwen image\\qwen_image_edit_2511_fp8_fix_by_FeiHou.safetensors"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "000_qwen_image_vae.safetensors"
NEG = "低质量，模糊，乱码，错别字"
STEPS = 20
CFG = 2.5  # ← 关键: 1.0 文字模糊 (v1.1-v1.4 全败的根因)
SHIFT = 3.1
SIZE = 1328


def _api(path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(
        COMFY + path,
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _upload(image_path: Path, comfy_name: str) -> str:
    """上传底图, 返回 ComfyUI 实存文件名 (1016 修: 撞名覆盖 + 吃响应)。

    旧注释称"覆盖同名"但实发无 overwrite 字段 — ComfyUI 默认撞名落
    'xxx (1).png', LoadImage 引原名会加载陈年旧图 (同 face_fix 坑,
    1016 s03 实锤)。overwrite=true + 以响应 name 为准。"""
    data = image_path.read_bytes()
    boundary = "----zwtext"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
        "true\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
        f"filename=\"{comfy_name}\"\r\nContent-Type: image/png\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        COMFY + "/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        name = json.loads(r.read()).get("name") or comfy_name
    if name != comfy_name:
        logger.warning("[text-pass] 撞名落盘 input/%s (原引 %s)", name, comfy_name)
    return name



def _latent_size(base_image: Path) -> tuple[int, int]:
    """2511 编辑 latent 尺寸: 随输入图纵横比等比 ~SIZE² 像素, 16 对齐.

    1015 修 (s15/s16 尺寸突变实锤): 旧版固定 1328x1328 方形 latent, 16:9 输入被掰方."""
    from PIL import Image
    with Image.open(base_image) as im:
        w, h = im.size
    target = SIZE * SIZE
    scale = (target / (w * h)) ** 0.5
    W = max(16, round(w * scale / 16) * 16)
    H = max(16, round(h * scale / 16) * 16)
    return W, H

def _build_wf(image_name: str, prompt: str, seed: int, prefix: str, width: int = SIZE, height: int = SIZE) -> dict:
    return {
        "101": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "102": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "103": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "110": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "120": {"class_type": "ImageScaleToTotalPixels",
                "inputs": {"image": ["110", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1}},
        "140": {"class_type": "TextEncodeQwenImageEditPlus",
                "inputs": {"clip": ["102", 0], "prompt": prompt, "vae": ["103", 0], "image1": ["120", 0]}},
        "141": {"class_type": "TextEncodeQwenImageEditPlus",
                "inputs": {"clip": ["102", 0], "prompt": NEG, "vae": ["103", 0], "image1": ["120", 0]}},
        "150": {"class_type": "FluxKontextMultiReferenceLatentMethod",
                "inputs": {"conditioning": ["140", 0], "reference_latents_method": "index_timestep_zero"}},
        "151": {"class_type": "FluxKontextMultiReferenceLatentMethod",
                "inputs": {"conditioning": ["141", 0], "reference_latents_method": "index_timestep_zero"}},
        "160": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["101", 0], "shift": SHIFT}},
        "161": {"class_type": "CFGNorm", "inputs": {"model": ["160", 0], "strength": 1.0}},
        "162": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "170": {"class_type": "KSampler", "inputs": {
            "model": ["161", 0], "positive": ["150", 0], "negative": ["151", 0],
            "latent_image": ["162", 0], "seed": seed, "control_after_generate": "fixed",
            "steps": STEPS, "cfg": CFG, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "180": {"class_type": "VAEDecodeTiled",
                "inputs": {"samples": ["170", 0], "vae": ["103", 0],
                           "tile_size": 512, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}},
        "190": {"class_type": "SaveImage", "inputs": {"images": ["180", 0], "filename_prefix": prefix}},
    }


def _make_prompt(lines: list[str], surface_hint: str = "") -> str:
    """构造入字指令: 在空白面上写入指定行, 只写这些行.

    1015 加硬 (s37 实锤: 多渲一行乱码残笔「只1技能»): 行数点名 + 禁额外行/装饰。"""
    rows = "\n".join(lines[:4])  # ≤4 行 (验证过的上限)
    hint = f"（{surface_hint}）" if surface_hint else ""
    return (
        f"在{hint}空白面上用黑色墨水手写体工整地写入以下**恰好 {len(lines[:4])} 行**文字，"
        f"从上到下逐行排列：\n{rows}\n"
        f"严格只写上面这 {len(lines[:4])} 行，一行不多一行不少；除这些汉字外禁止出现任何"
        "额外的文字、字母、数字、乱码或残笔。字迹清晰，汉字笔画正确。画面其余部分保持原样。"
    )


def run_text_pass(shot: dict[str, Any], doc: dict[str, Any],
                  base_image: Path) -> Path | None:
    """对有 in_image_text 的镜运行 2511 入字 pass.

    返回: 入字后的图路径 (base_image 被覆盖) 或 None (无文字/失败).
    前提: ComfyUI 在线; 2511 模型在盘; base_image 存在."""
    lines = shot.get("in_image_text") or []
    if not lines or not isinstance(lines, list):
        return None
    if not base_image.exists():
        logger.warning("[text-pass] %s 底图不存在 %s", shot.get("shot_id"), base_image)
        return None

    sid = str(shot.get("shot_id") or "unknown")
    comfy_name = f"textpass_{sid}.png"
    seed = int(shot.get("seed") or 20261600)

    # 从画面句提取面提示 (如"账页"/"标语"等)
    kf = str(shot.get("keyframe_zh") or "")
    surface = ""
    for w in ("账页", "书页", "纸张", "屏幕", "标语牌", "封面", "墙面", "招牌"):
        if w in kf:
            surface = w
            break

    prompt = _make_prompt([str(l) for l in lines], surface)
    prefix = f"anim/textpass/{doc.get('book_title', 'unknown')}/ep{doc.get('ep', 0)}/{sid}"
    W, H = _latent_size(base_image)

    try:
        actual_name = _upload(base_image, comfy_name)  # 1016 修: 吃响应真名 (防撞名旧图)
        wf = _build_wf(actual_name, prompt, seed, prefix, W, H)
        pid = _api("/prompt", {"prompt": wf})["prompt_id"]
        logger.info("[text-pass] %s submitted (cfg=%.1f, %d行)", sid, CFG, len(lines[:4]))
        t0 = time.monotonic()
        while True:
            time.sleep(8)
            h = _api(f"/history/{pid}")
            if pid in h:
                imgs = [i for o in (h[pid].get("outputs") or {}).values()
                        for i in (o.get("images") or [])]
                if imgs:
                    # 从 ComfyUI output 拷回覆盖 base_image
                    comfy_out = Path(r"E:\AI\ComfyUI_windows_portable\ComfyUI\output")
                    src = comfy_out / imgs[0]["subfolder"] / imgs[0]["filename"] \
                        if imgs[0].get("subfolder") else comfy_out / imgs[0]["filename"]
                    if src.exists():
                        import shutil

                        # RESIZE-BACK (1015 尺寸统一令): pass 产物回源图尺寸 — 全集恒 1920×1080
                        from PIL import Image
                        with Image.open(base_image) as _orig:
                            _ow, _oh = _orig.size
                        with Image.open(src) as _out:
                            if _out.size != (_ow, _oh):
                                _out = _out.resize((_ow, _oh), Image.LANCZOS)
                                _buf = __import__('io').BytesIO()
                                _out.save(_buf, 'PNG')
                                base_image.write_bytes(_buf.getvalue())
                            else:
                                import shutil
                                shutil.copy2(str(src), str(base_image))
                        ms = int((time.monotonic() - t0) * 1000)
                        logger.info("[text-pass] %s ✓ 入字完成 (%dms)", sid, ms)
                        shot["text_passed"] = True
                        return base_image
                if h[pid].get("status", {}).get("status_str") == "error":
                    logger.warning("[text-pass] %s ComfyUI 执行失败", sid)
                    return None
            if time.monotonic() - t0 > 300:
                logger.warning("[text-pass] %s 超时 5min", sid)
                return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[text-pass] %s 异常 (保持底图): %s", sid, exc)
        return None


def batch_text_pass(doc: dict[str, Any], only: set[str] | None = None) -> int:
    """对 doc 中有 in_image_text 的 img_done 镜批量运行入字 pass."""
    n = 0
    for shot in doc.get("shots") or []:
        if not shot.get("in_image_text"):
            continue
        if only is not None and str(shot.get("shot_id")) not in only:
            continue
        if shot.get("text_passed"):
            continue  # 幂等
        img_path = None
        # 用 shots_mod 的路径逻辑更稳
        from .shots import ep_dir as _ep_dir
        img_path = _ep_dir(doc["book_title"], int(doc.get("ep") or 0)) / str(shot.get("image_file") or "")
        if img_path and img_path.exists():
            result = run_text_pass(shot, doc, img_path)
            if result:
                n += 1
        else:
            logger.warning("[text-pass] %s 无图文件, 跳过", shot.get("shot_id"))
    if n:
        logger.info("[text-pass] %d 镜入字完成", n)
    return n
