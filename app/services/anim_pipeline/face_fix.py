# -*- coding: utf-8 -*-
"""FaceDetailer 二段修脸 (1015 无脸战役收官) — K2 出图后自动检测脸部并局部重绘.

不依赖提示词胜负 (英文切换/FACE_LAW/负词三役均只部分见效后的兜底主力):
Impact-Pack FaceDetailer + face_yolov8m 检测, guide 768 逐脸 inpaint, 基模裸跑
(不挂 25D LoRA — 实验矩阵定罪其独立压脸), krea2 turbo 配方 (euler/simple/cfg2).
顺序约定: K2 渲染 → 本 pass 修脸 → text_pass 入字 (文字层最后落).
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
UNET = "krea2_turbo_fp8_scaled.safetensors"
CLIP = "qwen3vl_4b_fp8_scaled.safetensors"
VAE = "krea2RealVae_v10.safetensors"
DETECTOR = "bbox/face_yolov8m.pt"

STEPS = 10
CFG = 2.0          # krea2 turbo 配方 (K2 主链同款, 非 SD 默认 8)
DENOISE = 0.5      # 脸部局部重绘: 保留构图身份, 重画五官
GUIDE = 768        # 社区建议: 小脸放大到 512-768 再画


def _api(path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(
        COMFY + path,
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _upload(image_path: Path, comfy_name: str) -> str:
    """上传底图, 返回 ComfyUI 实存文件名 (1016 修: 撞名覆盖 + 吃响应)。

    ComfyUI /upload/image 默认不覆盖同名 input 文件 — 撞名落成 'xxx (1).png'
    且只在响应 name 里告知真名。旧版丢响应且工作流引原名 = LoadImage 加载到
    input 里的陈年旧图 (1016 实锤: 22 镜被 10-15 旧图覆盖当日新图, K2 图与
    H3 提示词两界脱节)。overwrite=true + 以响应名为准 (同 comfy.upload_image)。"""
    data = image_path.read_bytes()
    boundary = "----facefix"
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
        logger.warning("[face-fix] 撞名落盘 input/%s (原引 %s)", name, comfy_name)
    return name


def _build_wf(image_name: str, positive: str, negative: str,
              seed: int, prefix: str) -> dict:
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "krea2", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["3", 0]}},
        "7": {"class_type": "UltralyticsDetectorProvider", "inputs": {"model_name": DETECTOR}},
        "8": {"class_type": "FaceDetailer", "inputs": {
            "image": ["1", 0], "model": ["2", 0], "clip": ["3", 0], "vae": ["4", 0],
            "guide_size": GUIDE, "guide_size_for": True, "max_size": 1536,
            "seed": seed, "steps": STEPS, "cfg": CFG,
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["5", 0], "negative": ["6", 0],
            "denoise": DENOISE, "feather": 5, "noise_mask": True, "force_inpaint": True,
            "bbox_threshold": 0.5, "bbox_dilation": 10, "bbox_crop_factor": 3.0,
            "sam_detection_hint": "center-1", "sam_dilation": 0, "sam_threshold": 0.93,
            "sam_bbox_expansion": 0, "sam_mask_hint_threshold": 0.7,
            "sam_mask_hint_use_negative": "False", "drop_size": 10,
            "bbox_detector": ["7", 0], "wildcard": "", "cycle": 1}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
    }


def run_face_fix(shot: dict[str, Any], doc: dict[str, Any], base_image: Path) -> Path | None:
    """单镜修脸: 原图检测脸部 → 逐脸 inpaint → 覆盖 base_image。返回路径或 None。

    检不到脸 = 返回 None (无人脸可修, 静默); ComfyUI 失败 = 保持原图 fail-open。"""
    if not base_image.exists():
        logger.warning("[face-fix] %s 底图不存在 %s", shot.get("shot_id"), base_image)
        return None
    sid = str(shot.get("shot_id") or "unknown")
    from . import k2
    # 修脸正向: 画面句(EN 优先) + 脸部强化 + 风格一致性锚; 负向: 产线负词(faceless 族在内)
    scene = str(shot.get("keyframe_en") or "").strip() or str(shot.get("image_prompt_zh") or "")
    # 1016 W1 单源化: 删硬编码 "3D chibi cartoon style" — 风格锚从风格卡派生
    # (非 chibi 书被反向拉风格实锤); 风格卡无值降级中性句, 宁中性勿发明。
    from .config import resolve_style
    _style = str(resolve_style(str(doc.get("book_title") or "")).get("h3_style")
                 or "").strip() or "preserve the original image's art style exactly"
    logger.info("[face-fix][style] %s anchor=%s", shot.get("shot_id"), _style[:40])
    positive = (f"{scene}\n"
                "Close-up emphasis on the faces: expressive detailed cartoon faces with clear "
                "eyes, eyebrows, nose and a smiling mouth, complete facial features, "
                f"consistent with the rest of the image ({_style})")
    negative = k2._negative_prompt(str(doc.get("book_title") or ""), "k2", scene)
    comfy_name = f"facefix_{sid}.png"
    seed = int(shot.get("seed") or 20261600)
    prefix = f"anim/facefix/{doc.get('book_title', 'unknown')}/ep{doc.get('ep', 0)}/{sid}"
    try:
        actual_name = _upload(base_image, comfy_name)  # 1016 修: 吃响应真名 (防撞名旧图)
        wf = _build_wf(actual_name, positive, negative, seed, prefix)
        pid = _api("/prompt", {"prompt": wf})["prompt_id"]
        logger.info("[face-fix] %s submitted (guide=%d denoise=%.1f)", sid, GUIDE, DENOISE)
        t0 = time.monotonic()
        while True:
            time.sleep(5)
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
                        shutil.copy2(str(src), str(base_image))
                        ms = int((time.monotonic() - t0) * 1000)
                        shot["face_fixed"] = True
                        logger.info("[face-fix] %s ✓ 修脸完成 (%dms)", sid, ms)
                        return base_image
                st = (h[pid].get("status") or {}).get("status_str")
                if st == "error":
                    logger.warning("[face-fix] %s ComfyUI 执行失败 (保持原图)", sid)
                    return None
                # success 无图 = 检不到脸 (FaceDetailer 直通)
                if st == "success":
                    logger.info("[face-fix] %s 未检出人脸 (直通)", sid)
                    return None
            if time.monotonic() - t0 > 240:
                logger.warning("[face-fix] %s 超时 4min (保持原图)", sid)
                return None
    except Exception as exc:  # noqa: BLE001 — fail-open 保持原图
        logger.warning("[face-fix] %s 异常 (保持原图): %s", sid, exc)
        return None


def batch_face_fix(doc: dict[str, Any], only: set[str] | None = None) -> int:
    """对 img_done 且未修过脸的人物镜批量修脸 (幂等: face_fixed 跳过)。"""
    from . import k2
    from .shots import ep_dir as _ep_dir
    n = 0
    for shot in doc.get("shots") or []:
        if shot.get("face_fixed") or not shot.get("image_file"):
            continue
        if only is not None and str(shot.get("shot_id")) not in only:
            continue
        scene = str(shot.get("keyframe_en") or "").strip() or str(shot.get("image_prompt_zh") or "")
        if not k2._person_in(scene, doc.get("book_title") or ""):
            continue
        img = _ep_dir(doc["book_title"], int(doc.get("ep") or 0)) / str(shot.get("image_file"))
        if img.exists() and run_face_fix(shot, doc, img):
            n += 1
    if n:
        logger.info("[face-fix] %d 镜修脸完成", n)
    return n
