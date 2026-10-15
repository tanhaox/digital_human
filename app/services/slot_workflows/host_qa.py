"""ComfyUI 分镜图 QA 预处理 (C 线 host 专用).

按三阶段拆分: 转 RGB → 缩放 → letterbox, 最后 PNG 落盘。
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

__all__ = [
    "prepare_comfyui_storyboard",
]


def _qa_convert_rgb(img: Image.Image) -> Image.Image:
    """Ensure RGB/RGBA 8-bit for the VAE pipeline (flatten alpha onto white)."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    return img


def _qa_resize(img: Image.Image, max_source_pixels: int, src_name: str) -> Image.Image:
    """Scale down if pixel count exceeds max_source_pixels (preserve aspect)."""
    w, h = img.size
    pixels = w * h
    if pixels <= max_source_pixels:
        return img
    scale = math.sqrt(max_source_pixels / pixels)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    logger.info(
        "[comfyui-qa] resizing %s %dx%d -> %dx%d (%.2f Mpx -> %.2f Mpx)",
        src_name, w, h, new_w, new_h,
        pixels / 1e6, (new_w * new_h) / 1e6,
    )
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _qa_letterbox(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Pad to target aspect ratio (no upscale) so LTX crop is predictable."""
    current_ratio = img.width / img.height
    target_ratio = target_w / target_h
    if abs(current_ratio - target_ratio) <= 0.05:
        return img
    logger.info(
        "[comfyui-qa] aspect ratio %.3f != target %.3f; padding to target",
        current_ratio, target_ratio,
    )
    new_w = img.width
    new_h = int(new_w / target_ratio)
    if new_h < img.height:
        new_w = int(img.height * target_ratio)
        new_h = img.height
    canvas = Image.new("RGB", (new_w, new_h), (255, 255, 255))
    canvas.paste(img, ((new_w - img.width) // 2, (new_h - img.height) // 2))
    return canvas


def _qa_finalize(src: Path, img: Image.Image, max_file_size_mb: float) -> Path:
    """Write PNG to a temp path; warn if still oversized."""
    tmp = src.with_suffix(".qa.png")
    img.save(tmp, format="PNG", optimize=True)
    final_size_mb = tmp.stat().st_size / (1024 * 1024)
    if final_size_mb > max_file_size_mb:
        logger.warning(
            "[comfyui-qa] prepared %s still %.1f MB; consider lowering max_source_pixels",
            tmp.name, final_size_mb,
        )
    return tmp


def prepare_comfyui_storyboard(
    src: Path,
    *,
    target_w: int,
    target_h: int,
    max_source_pixels: int = 2_000_000,
    max_file_size_mb: float = 10.0,
) -> Path:
    """QA-prepare a storyboard image for ComfyUI / LTX23.

    Performs three conformance checks:
      1. Resize: if source pixel count > max_source_pixels, scale down
         preserving aspect ratio before ComfyUI sees it (saves VAE encode).
      2. Format: always convert to PNG 8-bit RGB/RGBA; strips ICC/EXIF junk.
      3. File size: if source is > max_file_size_mb, log a warning but still
         resize, so we don't feed ComfyUI a 50 MB phone photo.

    Returns the path of the prepared PNG (may equal src if already compliant).
    """
    if not src.exists():
        raise FileNotFoundError(f"storyboard source not found: {src}")

    file_size_mb = src.stat().st_size / (1024 * 1024)
    if file_size_mb > max_file_size_mb:
        logger.warning(
            "[comfyui-qa] storyboard %s is %.1f MB (limit %.1f MB); will resize",
            src.name, file_size_mb, max_file_size_mb,
        )

    img = _qa_convert_rgb(Image.open(src))
    img = _qa_resize(img, max_source_pixels, src.name)
    img = _qa_letterbox(img, target_w, target_h)
    return _qa_finalize(src, img, max_file_size_mb)
