"""视频打标 — 单素材 4 通道流水线 (抽帧 → 运动分析 → OCR 预扫 → LLM 综合打标).

预抽帧缓存集成: 若 data/frames_cache/{asset_id}/ 已有预抽帧 (preprocess_frames.py
产出的 JPEG 帧 + meta.json 含 motion/ocr), 则通道1/2/3 直接命中缓存跳过重复
抽帧/motion/OCR, 仅保留通道4 LLM. 缓存未命中或 meta 异常时回退老逻辑 (现抽现算).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from app.config import get_config
from app.services.video_tagging_service.constants import SYSTEM_PROMPT
from app.services.video_tagging_service.parse import frame_tagging_result

logger = logging.getLogger(__name__)

__all__ = ["_run_pipeline_for_asset", "load_preprocessed_frames"]

# 预抽帧缓存根目录 (与 scripts/preprocess_frames.py 保持一致)
CACHE_ROOT: Path | None = None


def _get_cache_root() -> Path:
    """延迟解析缓存根目录 (data/frames_cache), 避免 import 时依赖 app 配置."""
    global CACHE_ROOT
    if CACHE_ROOT is None:
        try:
            cfg = get_config()
            data_dir = Path(cfg.app.data_dir) if hasattr(cfg, "app") else None
        except Exception:
            data_dir = None
        if data_dir is None:
            data_dir = Path(__file__).resolve().parents[3] / "data"
        CACHE_ROOT = data_dir / "frames_cache"
    return CACHE_ROOT


def load_preprocessed_frames(asset_id: str | None) -> tuple[list[Path] | None, dict | None, dict | None]:
    """读取预抽帧缓存 (frame_*.jpg + meta.json).

    Returns:
        (frames, motion_result, ocr_result); 缓存缺失/损坏 → (None, None, None).
    """
    if not asset_id:
        return None, None, None
    d = _get_cache_root() / asset_id
    meta_path = d / "meta.json"
    if not meta_path.exists():
        return None, None, None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[pipeline] 预抽帧 meta.json 解析失败 %s: %s", asset_id, exc)
        return None, None, None
    frame_files = [f for f in meta.get("frame_files", []) if f.endswith(".jpg")]
    if not frame_files:
        return None, None, None
    frames = [d / f for f in frame_files]
    # 全部帧文件必须实际存在
    if not all(f.exists() for f in frames):
        logger.warning("[pipeline] 预抽帧缓存不完整 %s: 缺帧文件", asset_id)
        return None, None, None
    motion = meta.get("motion") or None
    ocr = meta.get("ocr") or None
    return frames, motion, ocr


def _build_enriched_user_prompt(
    motion_result: dict | None,
    ocr_result: dict | None,
) -> str:
    """将通道 2/3 的分析结果注入 user prompt, 供 LLM 参考."""
    parts = ["请分析这些视频关键帧，生成结构化标签 JSON。", ""]

    if motion_result and motion_result.get("confidence", 0) > 0.3:
        parts.append("## 运动分析 (预计算)")
        parts.append(f"- motion_level: **{motion_result['motion_level']}**")
        parts.append(f"- speed_category: **{motion_result['speed_category']}**")
        parts.append(f"- scene_change_rate: {motion_result.get('scene_change_rate', 0)}/s")
        parts.append(f"- 置信度: {motion_result.get('confidence', 0):.0%}")
        parts.append("")

    if ocr_result:
        if ocr_result.get("available"):
            parts.append("## OCR 扫描 (预计算)")
            parts.append(f"- 文字语言: **{ocr_result['text_language']}**")
            parts.append(f"- 检测到国旗: **{'是' if ocr_result.get('has_flag') else '否'}**")
            parts.append(f"- 文字覆盖率: {ocr_result.get('text_coverage', 0):.1%}")
            parts.append(f"- 国内外推断: {ocr_result['location_hint']}")
            parts.append("")
        elif ocr_result.get("has_flag"):
            # OCR 不可用但国旗色块检测到
            parts.append("## 国旗检测 (色块分析)")
            parts.append("- 画面中疑似检测到中国国旗色块 (红色+黄色)")
            parts.append("- 建议 location 设为 domestic")
            parts.append("")

    parts.append("请结合关键帧画面和上述预分析数据，生成标签 JSON。")

    return "\n".join(parts)


def _run_channel1_frames(
    video_path: str,
    duration: float,
    tmpdir: Path,
    asset_no: str,
    fallback: dict,
    pub_channel: Callable[[int, str, str], None],
    frame_prefix: str = "frame_",
) -> list[Path] | None:
    """通道1: 智能抽帧 (scdet 场景检测). 无帧返回 None → 规则回退."""
    from app.services.frame_extraction import smart_extract_frames

    frames = smart_extract_frames(video_path, duration, tmpdir, frame_prefix=frame_prefix)
    logger.debug("[pipeline:%s] 通道1: %d 帧", asset_no, len(frames))
    pub_channel(1, "智能抽帧", f"{asset_no}: 抽帧完成 ({len(frames)} 帧)")

    if not frames:
        logger.warning("No frames extracted for %s, using rule fallback", asset_no)
        return None
    return frames


def _run_channel2_motion(
    video_path: str,
    duration: float,
    asset_no: str,
) -> dict | None:
    """通道2: 运动分析 (并行, 不依赖帧). 失败仅 warning."""
    from app.services.motion_analysis import analyze_motion

    try:
        motion_result = analyze_motion(video_path, duration)
        logger.debug("[pipeline:%s] 通道2: motion=%s speed=%s",
                     asset_no, motion_result.get("motion_level"), motion_result.get("speed_category"))
        return motion_result
    except Exception as exc:
        logger.warning("[pipeline:%s] 通道2 失败: %s", asset_no, exc)
        return None


def _run_channel3_ocr(
    frames: list[Path],
    asset_no: str,
) -> dict | None:
    """通道3: OCR 预扫 (依赖帧输出, 可选). 失败仅 warning."""
    from app.services.ocr_scan import ocr_scan_frames

    try:
        ocr_result = ocr_scan_frames(frames)
        logger.debug("[pipeline:%s] 通道3: lang=%s flag=%s hint=%s",
                     asset_no, ocr_result.get("text_language"),
                     ocr_result.get("has_flag"), ocr_result.get("location_hint"))
        return ocr_result
    except Exception as exc:
        logger.warning("[pipeline:%s] 通道3 失败: %s", asset_no, exc)
        return None


def _run_channel4_llm(
    frames: list[Path],
    client: Any,
    fallback: dict,
    asset_no: str,
    motion_result: dict | None,
    ocr_result: dict | None,
) -> dict[str, Any]:
    """通道4: LLM 综合打标. LocalLLMError → 规则回退."""
    from app.services.local_llm_client import LocalLLMError

    enriched_prompt = _build_enriched_user_prompt(motion_result, ocr_result)
    try:
        raw = client.chat_with_images(
            image_paths=frames,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=enriched_prompt,
        )
        logger.debug("LLM raw response for %s: %s", asset_no, raw[:300])
        return frame_tagging_result(raw, fallback)
    except LocalLLMError as exc:
        logger.error("LLM call failed for %s: %s", asset_no, exc)
        return {**fallback, "_fallback": True, "_ai_raw": f"LocalLLMError: {exc}"}


def _inject_analysis_extra(
    tags: dict[str, Any],
    motion_result: dict | None,
    ocr_result: dict | None,
) -> None:
    """把通道 2/3 结果注入 ai_tags_extra 供后续查询."""
    extra = tags.get("_ai_extra") or {}
    if isinstance(extra, dict):
        if motion_result:
            extra["_motion_analysis"] = motion_result
        if ocr_result:
            extra["_ocr_scan"] = ocr_result
        tags["_ai_extra"] = extra if extra else None


def _run_pipeline_for_asset(
    video_path: str,
    duration: float,
    tmpdir: Path,
    client: Any,
    fallback: dict,
    asset_no: str,
    job_id: str | None = None,
    done: int = 0,
    failed: int = 0,
    total: int = 1,
    frame_prefix: str = "frame_",
    asset_id: str | None = None,
) -> dict[str, Any]:
    """对单个素材执行完整 4 通道流水线.

    通道1: 智能抽帧 (scdet 场景检测)
    通道2: 运动分析 (并行, 不依赖帧)
    通道3: OCR 预扫 (依赖帧输出, 可选)
    通道4: LLM 综合打标

    asset_id: 素材 UUID. 传入时优先读预抽帧缓存 (data/frames_cache/{id}/):
    命中则通道1/2/3 直接取缓存帧 + meta 的 motion/ocr, 仅跑通道4 LLM (640px JPEG);
    缓存缺失/损坏时回退老逻辑 (现抽现算), 不阻塞打标.

    job_id/done/failed/total: 批量任务上下文, 用于在每通道后发布
    通道级进度事件 (type=channel), 前端可显示"当前素材第 N/4 通道".
    job_id=None (同步单素材调用) 时 publish 为 no-op.

    frame_prefix: 帧 PNG 文件名前缀. 并发打标时每个素材独立前缀, 互不覆盖.
    默认 "frame_" 保持单素材调用 (目录内仅一个素材) 的既有行为.

    Returns:
        结构化标签字典.
    """
    from app.services.director_events import publish

    def pub_channel(ch: int, name: str, msg: str) -> None:
        publish(job_id, {
            "type": "channel",
            "channel": ch, "channel_total": 4, "channel_name": name,
            "done": done, "failed": failed, "total": total,
            "current_asset_no": asset_no,
            "msg": msg,
        })

    # ── 预抽帧缓存: 命中则通道1/2/3 直接复用 (仅保留通道4 LLM) ──
    cached_frames, cached_motion, cached_ocr = load_preprocessed_frames(asset_id)
    if cached_frames is not None:
        pub_channel(1, "智能抽帧", f"{asset_no}: 预抽帧缓存命中 ({len(cached_frames)} 帧)")
        pub_channel(2, "运动分析", f"{asset_no}: 运动分析 (缓存)")
        pub_channel(3, "OCR 预扫", f"{asset_no}: OCR 预扫 (缓存)")
        pub_channel(4, "LLM 打标", f"{asset_no}: LLM 综合打标中…")
        tags = _run_channel4_llm(
            cached_frames, client, fallback, asset_no,
            cached_motion, cached_ocr,
        )
        _inject_analysis_extra(tags, cached_motion, cached_ocr)
        return tags

    # ── 通道1: 智能抽帧 ──
    frames = _run_channel1_frames(video_path, duration, tmpdir, asset_no, fallback, pub_channel, frame_prefix)
    if frames is None:
        return {**fallback, "_fallback": True, "_ai_raw": "no frames extracted"}

    # ── 通道2: 运动分析 ──
    motion_result = _run_channel2_motion(video_path, duration, asset_no)
    pub_channel(2, "运动分析", f"{asset_no}: 运动分析完成")

    # ── 通道3: OCR 预扫 ──
    ocr_result = _run_channel3_ocr(frames, asset_no)
    pub_channel(3, "OCR 预扫", f"{asset_no}: OCR 预扫完成")

    # ── 通道4: LLM 综合打标 ──
    pub_channel(4, "LLM 打标", f"{asset_no}: LLM 综合打标中…")
    tags = _run_channel4_llm(frames, client, fallback, asset_no, motion_result, ocr_result)

    _inject_analysis_extra(tags, motion_result, ocr_result)

    return tags
