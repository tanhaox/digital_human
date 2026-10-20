# -*- coding: utf-8 -*-
"""伪文字抹除 pass (1015, 用户令) — text_pass 的镜像.

治物件先验涂写 (ep4 s01-s03 酒桶标签/s36 磨石刻字/s49 实锤: 剔词+空白素面指令都
压不住「酒桶=标签=手写涂记」的物件身份): 用 2511 编辑能力把标签/牌匾/刻面上的
涂写抹成空白素面, 保留纸面/木面质感与边缘, 其余画面逐字不动。
管线位置: K2 → face_fix → **erase (本 pass, 未申报镜)** → text_pass (申报镜入字) —
erase 与 text_pass 互斥 (erase 会抹掉申报位, 申报镜跳过)。
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
NEG = "低质量，模糊，多余的文字，新的文字，乱码，错别字，装饰花纹"
STEPS = 20
CFG = 2.5
SHIFT = 3.1
SIZE = 1328

ERASE_PROMPT = (
    "把画面中所有标签、纸条、牌匾、刻面上的涂写、伪文字、乱码笔画完全抹除，"
    "这些位置改成干净的空白素面：保留纸张/木面/石面本身的材质、颜色、边缘和阴影，"
    "只是上面没有任何文字、字母、数字或涂写痕迹。"
    "画面其余所有部分（人物、物件、背景、光线）保持原样，一处不改。"
)


def _api(path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(
        COMFY + path,
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _upload(image_path: Path, comfy_name: str) -> str:
    """上传底图, 返回 ComfyUI 实存文件名 (1016 修: 撞名覆盖 + 吃响应)。

    ComfyUI 默认不覆盖同名 input 文件 — 撞名落 'xxx (1).png', LoadImage 引原名
    会加载陈年旧图 (同 face_fix/text_pass 坑; erase_s15 (1)/(2) 撞名实锤,
    s07/s15/s16 方图事故链)。overwrite=true + 以响应 name 为准。"""
    data = image_path.read_bytes()
    boundary = "----zwerase"
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
        logger.warning("[erase] 撞名落盘 input/%s (原引 %s)", name, comfy_name)
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

def _build_wf(image_name: str, seed: int, prefix: str, width: int = SIZE, height: int = SIZE) -> dict:
    # 与 text_pass 同栈 (2511 + EditPlus + cfg2.5 + shift3.1 + 1328) — 镜像工作流
    return {
        "101": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "102": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "103": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "110": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "120": {"class_type": "ImageScaleToTotalPixels",
                "inputs": {"image": ["110", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1}},
        "140": {"class_type": "TextEncodeQwenImageEditPlus",
                "inputs": {"clip": ["102", 0], "prompt": ERASE_PROMPT, "vae": ["103", 0], "image1": ["120", 0]}},
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
        "190": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["180", 0]}},
    }


def run_erase_pass(shot: dict[str, Any], doc: dict[str, Any], base_image: Path) -> Path | None:
    """单镜抹除: 标签/刻面涂写 → 空白素面, 覆盖 base_image。fail-open。"""
    if not base_image.exists():
        logger.warning("[erase] %s 底图不存在 %s", shot.get("shot_id"), base_image)
        return None
    sid = str(shot.get("shot_id") or "unknown")
    comfy_name = f"erase_{sid}.png"
    seed = int(shot.get("seed") or 20261600) + 7
    prefix = f"anim/erase/{doc.get('book_title', 'unknown')}/ep{doc.get('ep', 0)}/{sid}"
    W, H = _latent_size(base_image)
    try:
        actual_name = _upload(base_image, comfy_name)  # 1016 修: 吃响应真名 (防撞名旧图)
        wf = _build_wf(actual_name, seed, prefix, W, H)
        pid = _api("/prompt", {"prompt": wf})["prompt_id"]
        logger.info("[erase] %s submitted (cfg=%.1f)", sid, CFG)
        t0 = time.monotonic()
        while True:
            time.sleep(8)
            h = _api(f"/history/{pid}")
            if pid in h:
                imgs = [i for o in (h[pid].get("outputs") or {}).values()
                        for i in (o.get("images") or [])]
                if imgs:
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
                        shot["text_erased"] = True
                        shot.pop("text_suspect", None)
                        logger.info("[erase] %s ✓ 抹除完成 (%dms)", sid, int((time.monotonic() - t0) * 1000))
                        return base_image
                if h[pid].get("status", {}).get("status_str") == "error":
                    logger.warning("[erase] %s ComfyUI 执行失败 (保持原图)", sid)
                    return None
            if time.monotonic() - t0 > 300:
                logger.warning("[erase] %s 超时 5min (保持原图)", sid)
                return None
    except Exception as exc:  # noqa: BLE001 — fail-open 保持原图
        logger.warning("[erase] %s 异常 (保持原图): %s", sid, exc)
        return None


def batch_erase_pass(doc: dict[str, Any], only: set[str] | None = None) -> int:
    """对「有文字污染且未申报二段式」的 img_done 镜批量抹除 (幂等: text_erased 跳过).

    1021 用户令: 触发 = 图上实抓 ∪ 提示词嫌疑 — ①实抓 (text_suspect/文字类
    gate_rejects, 出口闸 VLM 伪字+OCR 真字每镜必扫) ②提示词嫌疑 (画面句含文字
    载体名词/涂写动词且未申报 — 生图前空白素面指令压不住 ep4 实锤, 实抓有
    假阴性, 嫌疑镜主动抹; ep7 s05/s07 漏网模式根治)。"""
    from .scene_writer import TEXT_CARRIER_NOUN_RE, TEXT_CARRIER_VERB_RE
    from .shots import ep_dir as _ep_dir
    n = 0
    for shot in doc.get("shots") or []:
        if shot.get("text_erased") or shot.get("in_image_text") or not shot.get("image_file"):
            continue
        if only is not None and str(shot.get("shot_id")) not in only:
            continue
        # 文字污染信号: ①实抓 ②提示词嫌疑 (未申报镜)
        _txt = sum(1 for r in (shot.get("gate_rejects") or [])
                   if not r.get("hard") and "文字" in str(r.get("reason") or ""))
        _kf = str(shot.get("keyframe_zh") or "")
        _sus = bool(shot.get("text_suspect") or _txt >= 1
                    or TEXT_CARRIER_NOUN_RE.search(_kf)
                    or TEXT_CARRIER_VERB_RE.search(_kf))
        if not _sus:
            continue
        img = _ep_dir(doc["book_title"], int(doc.get("ep") or 0)) / str(shot.get("image_file"))
        if img.exists() and run_erase_pass(shot, doc, img):
            n += 1
    if n:
        logger.info("[erase] %d 镜抹除完成", n)
    return n
