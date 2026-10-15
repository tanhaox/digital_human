# -*- coding: utf-8 -*-
"""J 线草稿导出公共层 — 微秒常量 / 时间轴 / manifest / jy 配置加载.

拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft_mod
from pyJianYingDraft import trange

from app.config import get_config

logger = logging.getLogger(__name__)

__all__ = ["_US", "_trange_sec", "_drafts_dir", "_load_manifest"]

_US = 1_000_000  # 秒 → 微秒

# ⚠️ 拆包注记: 原单文件在 app/services/ 下 parents[2]=digital_human/;
# 包内模块多一层目录, 须 parents[3]. 移动本文件时同步校准.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ── PPT 页整页动画 (2026-08-21 剪映分流) ──
# 每页 = 1 静态帧, 动效由剪映原生给: 整页入场动画 + 页间转场.
# 渐显最稳 (专业稿标配); 想更"活"可换 IntroType.向上滑动/轻微放大.
_JY_PPT_ENTRANCE = draft_mod.IntroType.渐显
_JY_PPT_TRANSITION = draft_mod.TransitionType.上移

# R9 v3 (2026-08-18): HF 卡文字族 (job_draft 边界音效用)
_HF_TEXT_FAMILIES = {"hf_title", "hf_chart", "hf_opening", "hf_quote",
                     "hf_identity", "hf_follow"}


def _load_jy_config(name: str) -> dict:
    p = _PROJECT_ROOT / "config" / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[jy_export] 配置加载失败 %s: %s", name, exc)
        return {}


# 知识源: config/jy_animation_sound_pairs.json (动画↔同帧音效配对, 库缺回退 _SFX_FAMILY)
#         config/jy_sound_semantics.json (title_in 族 = HF 边界转场音)
_ANIM_PAIRS = _load_jy_config("jy_animation_sound_pairs.json").get("animation_sound_family", {})
_TITLE_IN_SOUNDS = (
    _load_jy_config("jy_sound_semantics.json")
    .get("categories", {}).get("title_in", {}).get("sounds", [])
)


def _drafts_dir() -> Path:
    cfg = get_config()
    return Path(cfg.defaults.jianying_drafts_dir)


def _load_manifest(audio_path: str | Path) -> dict[str, Any] | None:
    """TTS manifest 与整段 wav 同目录 (projects/*/audio/manifest.json)."""
    p = Path(audio_path).parent / "manifest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[jy_export] manifest 解析失败 %s: %s", p, exc)
        return None


def _trange_sec(start_sec: float, dur_sec: float) -> draft_mod.Timerange:
    return trange(int(round(start_sec * _US)), int(round(dur_sec * _US)))
