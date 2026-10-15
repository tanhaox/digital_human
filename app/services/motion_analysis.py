"""运动分析服务 — 基于 ffmpeg scdet 低阈值场景变化频率, 确定性判断运动强度.

零 AI 依赖, 纯 ffmpeg + 统计算法.
输出 motion_level (static/slow/medium/fast) 和 speed_category (normal/timelapse/slow_motion).
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MotionResult:
    motion_level: str = "medium"        # static | slow | medium | fast
    speed_category: str = "normal"      # normal | timelapse | slow_motion
    scene_change_rate: float = 0.0      # 场景变化次数/秒
    scene_change_count: int = 0
    confidence: float = 0.5


def _count_scene_changes(video_path: Path, threshold: float = 2.0) -> int:
    """用 ffmpeg scdet 低阈值统计全片的场景变化次数.

    Args:
        video_path: 视频文件路径.
        threshold: scdet 阈值, 越低越敏感. 默认 2.0 可捕获帧间微小差异.

    Returns:
        检测到的场景变化次数.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not on PATH; motion analysis unavailable")
        return -1

    cmd = [
        "ffmpeg", "-y", "-loglevel", "info",
        "-i", str(video_path),
        "-vf", f"scdet=threshold={threshold},metadata=print",
        "-an", "-f", "null", "-",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120, check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg scdet (threshold=%.1f) timed out for %s", threshold, video_path.name)
        return -1

    count = 0
    for line in r.stderr.splitlines():
        if re.search(r"lavfi\.scd\.time=", line):
            count += 1
    return count


def _classify(change_count: int, duration: float) -> MotionResult:
    """基于场景变化频率分类运动强度."""
    if change_count < 0 or duration <= 0:
        return MotionResult(
            motion_level="medium",
            speed_category="normal",
            scene_change_count=change_count,
            confidence=0.3,
        )

    rate = change_count / duration

    # 分类阈值 (经验值, 可根据实际数据调优)
    if rate > 2.5:
        # 极高频率 → 可能是延时摄影
        motion_level = "fast"
        speed_category = "timelapse"
        confidence = min(0.95, 0.5 + rate * 0.1)
    elif rate > 1.0:
        # 高频率 → 快速运动 (动作片/体育/快速摇镜)
        motion_level = "fast"
        speed_category = "normal"
        confidence = min(0.9, 0.5 + rate * 0.15)
    elif rate > 0.3:
        # 中等频率 → 正常运动
        motion_level = "medium"
        speed_category = "normal"
        confidence = min(0.85, 0.5 + rate * 0.5)
    elif rate > 0.08:
        # 低频 → 慢速 (固定机位/演讲/访谈)
        motion_level = "slow"
        speed_category = "normal"
        confidence = min(0.9, 0.5 + rate * 2)
    elif rate > 0.005:
        # 极低频 → 静态 (静态镜头/缓慢摇镜)
        motion_level = "static"
        speed_category = "normal"
        confidence = min(0.95, 0.5 + rate * 20)
    else:
        # 几乎无变化 → 可能是慢动作 (视频很长但没镜头切换)
        if duration > 30:
            speed_category = "slow_motion"
            motion_level = "static"
        else:
            motion_level = "static"
            speed_category = "normal"
        confidence = 0.7 if duration > 30 else 0.5

    return MotionResult(
        motion_level=motion_level,
        speed_category=speed_category,
        scene_change_rate=round(rate, 4),
        scene_change_count=change_count,
        confidence=round(confidence, 2),
    )


def analyze_motion(video_path: str, duration: float) -> dict:
    """分析视频运动强度 (主入口).

    Args:
        video_path: 视频文件路径.
        duration: 视频时长 (秒).

    Returns:
        {
            "motion_level": "static"|"slow"|"medium"|"fast",
            "speed_category": "normal"|"timelapse"|"slow_motion",
            "scene_change_rate": 0.0,        # 次/秒
            "scene_change_count": N,
            "confidence": 0.0-1.0,
        }
    """
    mp4 = Path(video_path)
    if not mp4.exists():
        logger.warning("Video file not found for motion analysis: %s", video_path)
        return MotionResult().__dict__

    change_count = _count_scene_changes(mp4, threshold=2.0)
    result = _classify(change_count, duration)

    logger.debug(
        "Motion analysis for %s: %d changes / %.1fs → rate=%.3f → %s/%s (conf=%.2f)",
        mp4.name, change_count, duration,
        result.scene_change_rate, result.motion_level,
        result.speed_category, result.confidence,
    )
    return {
        "motion_level": result.motion_level,
        "speed_category": result.speed_category,
        "scene_change_rate": result.scene_change_rate,
        "scene_change_count": result.scene_change_count,
        "confidence": result.confidence,
    }
