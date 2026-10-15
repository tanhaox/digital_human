"""智能抽帧服务 — 场景检测 + 代表性帧选取, 替代固定 25%/50%/75%.

两阶段策略:
1. ffmpeg scdet 检测场景变化点
2. 按镜头切换强度自适应帧数档位 (单镜头 6 / 中等 8 / 高切换 12):
   - 单镜头素材 (P 线下载, 长镜头直出): 6 帧足够, 减少 LLM token 与耗时
   - 中等切换: 8 帧
   - 高镜头切换 (抖音/人工添加的文件夹素材, 镜头狂摇): 12 帧并保证全片覆盖
3. 场景变化充分时: 取变化点帧 + 段中间帧
4. 场景变化不足时: 退回到均匀采样
5. 上限 12 帧, 控制 LLM token 消耗
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 帧数档位 (镜头切换强度 → 帧数) ──
MAX_FRAMES = 12          # 上限 (高切换档)
Tier: dict[str, int] = {
    "single": 6,         # 单镜头 / 低切换
    "medium": 8,         # 中等切换
    "high": 12,          # 高镜头切换
}
FALLBACK_FRAMES = 10     # scdet 不可用/场景过少时的均匀采样帧数


def _run_ffmpeg_scdet(video_path: Path, threshold: float = 10.0) -> list[float]:
    """运行 ffmpeg scdet 滤镜, 返回场景变化时间点列表."""
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not on PATH; scdet unavailable")
        return []

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
        logger.warning("ffmpeg scdet timed out for %s", video_path.name)
        return []

    # Parse stderr for "lavfi.scd.time=" lines
    times: list[float] = []
    for line in r.stderr.splitlines():
        m = re.search(r"lavfi\.scd\.time=(\d+\.?\d*)", line)
        if m:
            try:
                times.append(float(m.group(1)))
            except ValueError:
                pass
    return times


def _resolve_frame_tier(scene_count: int) -> tuple[str, int]:
    """按场景切换强度解析帧数档位.

    Args:
        scene_count: scdet 检测出的场景变化次数 (去重前原始计数).

    Returns:
        (tier_name, frames) — tier_name ∈ {"single","medium","high"}.
    """
    if scene_count < 3:
        return "single", Tier["single"]
    if scene_count < 10:
        return "medium", Tier["medium"]
    return "high", Tier["high"]


def _build_extraction_timestamps(
    scene_times: list[float],
    duration: float,
    max_frames: int,
) -> list[float]:
    """根据场景变化时间点构建抽帧时间戳列表.

    策略:
    - 场景变化 >= max_frames//2: 取场景变化点 (每个镜头起点), cap 到 max_frames
    - 场景变化 < 3: 退回到均匀采样 FALLBACK_FRAMES 帧
    - 适中: 场景变化点 + 段中间帧交错排列, 优先覆盖更多镜头
    """
    if not scene_times or len(scene_times) < 3:
        # 退回到均匀采样
        offset = min(0.5, duration * 0.03)
        step = (duration - offset * 2) / max(FALLBACK_FRAMES - 1, 1)
        return [round(offset + step * i, 2) for i in range(FALLBACK_FRAMES)]

    # 去重 + 排序
    unique_times = sorted(set(round(t, 2) for t in scene_times if 0 < t < duration))
    if not unique_times:
        offset = min(0.5, duration * 0.03)
        step = (duration - offset * 2) / max(FALLBACK_FRAMES - 1, 1)
        return [round(offset + step * i, 2) for i in range(FALLBACK_FRAMES)]

    # 每个场景变化点后 0.5s 取帧 (避开转场混合帧)
    extracted: list[float] = []
    skip_gap = min(0.5, duration * 0.02)

    for t in unique_times:
        ts = round(min(t + skip_gap, duration - 0.1), 2)
        # 避免重复时间点
        if not extracted or (ts - extracted[-1]) > 0.5:
            extracted.append(ts)
        if len(extracted) >= max_frames:
            break

    # 如果还太少, 在段间补充中间帧 (若 still < max_frames, 用更小的窗口再补一轮)
    if len(extracted) < max_frames:
        segments = [0.0] + extracted + [duration]
        for window in (0.8, 0.5):
            candidates: list[float] = []
            for i in range(len(segments) - 1):
                mid = round((segments[i] + segments[i + 1]) / 2, 2)
                # 离所有已有点都 >= window 才加入 (优先完整覆盖每个段)
                if all(abs(mid - e) >= window for e in extracted):
                    candidates.append(mid)
            # 交错合并 (优先场景变化点)
            for m in candidates:
                if len(extracted) >= max_frames:
                    break
                if all(abs(m - e) >= window for e in extracted):
                    extracted.append(m)
            if len(extracted) >= max_frames:
                break
            # 段间隔 < window 时上一轮可能无新增, 用 window 内仍差 → 接受稍近的点
            segments = [0.0] + extracted + [duration]
        extracted = sorted(extracted)[:max_frames]

    # 确保至少 3 帧
    if len(extracted) < 3:
        offset = min(0.5, duration * 0.03)
        step = (duration - offset * 2) / max(FALLBACK_FRAMES - 1, 1)
        extracted = [round(offset + step * i, 2) for i in range(FALLBACK_FRAMES)]

    return extracted[:max_frames]


def _extract_frame_at(mp4_path: Path, png_path: Path, at_seconds: float, label: str = "", max_width: int | None = None) -> bool:
    """复用 ffmpeg 子进程提取单帧.

    Args:
        max_width: 输出帧最大宽度. 视频宽度超过时按比例缩到该宽度
                   (高度等比), 否则原尺寸直出. None → 保持原尺寸.
    """
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{at_seconds:.3f}",
        "-i", str(mp4_path),
        "-vframes", "1",
    ]
    if max_width:
        # 等比缩放到 max_width (ffmpeg scale 保持宽高比, 只降不升)
        cmd += ["-vf", f"scale=w='min(iw,{max_width})':h=-2"]
    cmd.append(str(png_path))
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=20, check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg frame extraction timed out at %.1fs (%s)", at_seconds, label)
        return False
    ok = r.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0
    if not ok:
        logger.warning("ffmpeg frame extraction failed at %.1fs (%s): %s",
                       at_seconds, label, r.stderr[:200])
    return ok


def smart_extract_frames(
    video_path: str,
    duration: float,
    tmpdir: Path,
    max_frames: int | None = None,
    frame_prefix: str = "frame_",
    max_width: int | None = None,
) -> list[Path]:
    """智能抽帧主入口 (自适应档位).

    1. 先用 scdet 检测场景变化
    2. 按场景切换强度自适应选择帧数档位 (单镜头 6 / 中等 8 / 高切换 12)
    3. 构建抽帧时间戳列表
    4. 逐个提取 PNG

    Args:
        video_path: 视频文件路径.
        duration: 视频时长 (秒).
        tmpdir: 临时目录 (存放帧 PNG).
        max_frames: 帧数覆盖值. 默认 None → 按场景切换强度自适应
                    (单镜头 6 / 中等 8 / 高切换 12). 显式传入则固定档位.
        frame_prefix: 帧 PNG 文件名前缀. 并发打标时每个素材独立前缀, 避免覆盖.
        max_width: 输出帧最大宽度 (等比缩放, 只降不升). None → 原尺寸.

    Returns:
        成功抽取的 PNG 路径列表.
    """
    mp4 = Path(video_path)
    if not mp4.exists():
        logger.warning("Video file not found: %s", video_path)
        return []

    # Pass 1: 场景检测 (同时用于档位判定, 不额外开销)
    scene_times = _run_ffmpeg_scdet(mp4, threshold=10.0)
    logger.debug("scdet found %d scene changes for %s", len(scene_times), mp4.name)

    # 去重规范化: scdet 对同一切点可能在 4 帧窗口内重复上报 (去重后才是真实切点数,
    # 也避免 _build_extraction_timestamps 内因重复点提前凑满 max_frames 而漏尾部)
    scene_times = sorted(
        round(t, 2) for t in scene_times
        if 0 < t < duration and not any(abs(t - s) < 0.5 for s in scene_times if s < t)
    )

    # Pass 2: 自适应帧数档位
    if max_frames is None:
        tier, max_frames = _resolve_frame_tier(len(scene_times))
        logger.info("[抽帧:%s] 场景 %d 次 → %s 档 %d 帧", mp4.name, len(scene_times), tier, max_frames)
    else:
        logger.debug("[抽帧:%s] 固定档位 %d 帧", mp4.name, max_frames)

    # Pass 3: 构建时间戳
    timestamps = _build_extraction_timestamps(scene_times, duration, max_frames)
    logger.debug("Extracting %d frames for %s at: %s", len(timestamps), mp4.name, timestamps)

    # Pass 4: 逐帧提取
    frames: list[Path] = []
    for i, ts in enumerate(timestamps):
        png_path = tmpdir / f"{frame_prefix}{i:03d}.png"
        ok = _extract_frame_at(mp4, png_path, ts, label=f"#{i}@{ts:.1f}s", max_width=max_width)
        if ok:
            frames.append(png_path)

    logger.info("Smart extract: %d/%d frames extracted for %s",
                len(frames), len(timestamps), mp4.name)
    return frames
