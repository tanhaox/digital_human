# -*- coding: utf-8 -*-
"""静姐读书 logo 台标 — 右下角间歇出现, 防伪+品牌识别 (2026-09-03).

学电视访谈节目角标: 跳过片头, 之后每 ~50s 亮 8s, 渐显渐隐.
源图 (白底 RGB 无透明通道) 预处理: 裁内容边 + 白底转透明 → 透明 PNG
落盘 assets/watermark/, 幂等 (已在则直接用; 换 logo 删该文件重生成).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft_mod
from pyJianYingDraft import ClipSettings, trange

from app.services.jy_draft_service.common import _US

logger = logging.getLogger(__name__)

__all__ = ["prepare_watermark", "add_watermark", "WATERMARK_ASSET"]

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
WATERMARK_ASSET = _PROJECT_ROOT / "assets" / "watermark" / "jingbook_logo.png"
# 原始 logo 源 (2026-09-03 换标: 静听书圆章版, 源图入仓防外部目录被清);
# assets 副本缺失且此源在 → 现场处理生成
WATERMARK_SOURCE = _PROJECT_ROOT / "assets" / "watermark" / "jingbook_logo_source.png"

# 台标节奏/位置参数 (2026-09-03 用户定稿: 每 50s 亮 8s, 右下角)
_SKIP_SEC = 15.0       # 跳过片头封面
_INTERVAL_SEC = 50.0   # 出现间隔
_VISIBLE_SEC = 8.0     # 每次停留
_HEIGHT_PX = 96        # 0910 用户令: 竖版书法 logo 176px 过重 (压人物图), 降 ~9% 画布高
_MARGIN_PX = 46        # 右/下留边


def prepare_watermark(dst: Path = WATERMARK_ASSET,
                      src: Path | None = None) -> Path | None:
    """logo 预处理 (幂等): 裁内容边 + 白底转透明 → 透明 PNG 落盘.

    dst 已在 → 直接返回; 否则读 src (缺省 WATERMARK_SOURCE), 白底
    (min 通道 ≥245) → alpha 0, 内容 (≤120) → alpha 255, 中间线性过渡,
    颜色同步 un-multiply 去白. src 也不在 → None (调用方跳过台标).
    """
    if dst.exists():
        return dst
    src = src or WATERMARK_SOURCE
    if not src.exists():
        logger.warning("[watermark] 源 logo 缺失: %s", src)
        return None
    try:
        import numpy as np
        from PIL import Image, ImageChops

        im = Image.open(src).convert("RGB")
        # 裁内容边: 与纯白差分 > 20 的包围盒
        white = Image.new("RGB", im.size, (255, 255, 255))
        diff = ImageChops.difference(im, white).convert("L")
        bbox = diff.point(lambda p: 255 if p > 20 else 0).getbbox()
        if bbox:
            im = im.crop(bbox)
        arr = np.asarray(im).astype("float32")
        mn = arr.min(axis=2)
        # 白 → 透明: min 通道 245+ 全透明, 120- 全不透明, 中间线性
        a = np.clip((245.0 - mn) / (245.0 - 120.0), 0.0, 1.0)
        alpha = (a * 255).astype("uint8")
        # un-multiply: 半透明区去掉白底混色
        safe_a = np.maximum(a[..., None], 1e-3)
        unm = np.clip((arr - 255.0 * (1.0 - safe_a)) / safe_a, 0, 255)
        out = np.dstack([unm.astype("uint8"), alpha])
        # 二次裁边: 初次 bbox 按白底差分, 会吸进浅噪点 (alpha 转换后透明),
        # 按 alpha>30 再裁一次, 素材紧贴内容 (实测省掉底部 1/3 空白).
        ys, xs = np.nonzero(alpha > 30)
        if len(ys) > 8:
            out = out[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        dst.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(out, "RGBA").save(dst)
        logger.info("[watermark] logo 预处理落盘: %s (%s)", dst, im.size)
        return dst
    except Exception as exc:
        logger.warning("[watermark] logo 预处理失败: %s", exc)
        return None


def add_watermark(script: Any, total_us: int, logo_png: Path,
                  width: int = 1920, height: int = 1080) -> int:
    """右下角间歇台标 → 'logo' video 轨 (调用方须先建轨).

    每 _INTERVAL_SEC 出现 _VISIBLE_SEC, 渐显入场+渐隐出场; 首次跳过
    _SKIP_SEC (封面页不压标). 末次至少亮 1s 才放, 贴片尾不留残段.
    Returns: 放置段数 (素材缺失/时长不足为 0).
    """
    if not logo_png or not Path(logo_png).exists() or total_us <= 0:
        return 0
    try:
        from PIL import Image
        with Image.open(logo_png) as im:
            iw, ih = im.size
    except Exception:
        iw = ih = 512
    if iw <= 0 or ih <= 0:
        return 0
    scale = _HEIGHT_PX / ih
    # 中心点: 右下角留边 (transform 单位 = 半画布, y 正=上)
    cx = width - _MARGIN_PX - iw * scale / 2
    cy = height - _MARGIN_PX - ih * scale / 2
    tx = (cx - width / 2) / (width / 2)
    ty = -(cy - height / 2) / (height / 2)
    cs = ClipSettings(scale_x=scale, scale_y=scale, transform_x=tx, transform_y=ty)

    mat = draft_mod.VideoMaterial(str(logo_png))
    visible_us = int(_VISIBLE_SEC * _US)
    n = 0
    t_us = int(_SKIP_SEC * _US)
    while t_us + visible_us <= total_us - 500_000:
        try:
            seg = draft_mod.VideoSegment(
                mat, trange(t_us, visible_us), volume=0, clip_settings=cs)
            seg.add_animation(draft_mod.IntroType.渐显)
            seg.add_animation(draft_mod.OutroType.渐隐)
            script.add_segment(seg, "logo")
            n += 1
        except Exception as exc:
            logger.warning("[watermark] 台标段失败 @%.1fs: %s", t_us / _US, exc)
        t_us += int(_INTERVAL_SEC * _US)
    logger.info("[watermark] logo 台标 %d 段 (每 %.0fs 亮 %.0fs, 右下角)",
                n, _INTERVAL_SEC, _VISIBLE_SEC)
    return n
