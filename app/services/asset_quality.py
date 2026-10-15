# -*- coding: utf-8 -*-
"""素材内容质量打分服务 — 烂素材治理③ (2026-08-16).

共享的 VLM 质检入口: 全库扫描 (scripts/asset_quality_scan.py) 与
Pexels 下载即质检 (pexels_service/_candidates.py) 共用同一 rubric,
保证"库里筛掉的烂"和"下载挡掉的烂"标准一致。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["score_video_file"]

QUALITY_SYSTEM_PROMPT = """你是给知识类短视频供片的资深剪辑师, 正在验收 B-roll 素材库。
逐帧看图后, 按"这条素材敢不敢放进成片"打分。判断标准从严:

【一票否决 (≤3 分)】
- 画面涂抹/ Upscale 泥糊感、大面积噪点、严重压缩伪影
- 手持乱抖、失焦、过曝死白/欠曝死黑
- 内容空洞的 generic stock 货色: 千篇一律的城市空镜/路人不看镜头走路/
  摆拍的"商务人士握手微笑"/假装打字的键盘特写/地球网络科技动画
  (这类素材观众一眼识破"图库感", 是烂片感的最大来源)

【中等 (4-6 分)】
- 技术合格但平庸: 构图平常、光线平、信息量低
- 有明显瑕疵但不致命 (轻微偏色、地平线微斜)

【优良 (7-8 分)】
- 构图有主体有层次、光线有情绪、画面干净锐利
- 内容具体有信息量 (真实的机器运转/实验室/数据机房/人物在做具体的事)

【顶级 (9-10 分)】
- 电影感: 光影出色、色彩和谐、有叙事张力, 单帧就能留住观众

只输出一行 JSON:
{"quality_score": <1-10 整数>, "generic_stock": <true|false>, "verdict": "<不超过20字的中文裁决, 说清为什么>"}
不要输出任何其他文字。"""

_client: Any = None
_client_ready = False


def _get_client() -> Any | None:
    """懒加载本地视觉模型客户端 (llama-server 自拉起). 失败返回 None (fail-open)."""
    global _client, _client_ready
    if _client_ready:
        return _client
    _client_ready = True
    try:
        from app.config import get_config
        from app.services.local_llm_client import LocalLLMClient
        from app.services.video_tagging_service.llama import _ensure_llama_server

        if not _ensure_llama_server():
            logger.warning("[quality] llama-server 不可用, 质检跳过 (fail-open)")
            return None
        _client = LocalLLMClient(get_config().local_llm)
    except Exception as exc:
        logger.warning("[quality] 视觉客户端初始化失败: %s", exc)
        return None
    return _client


def _extract_two_frames(video: Path, tmpdir: Path) -> list[Path]:
    """首 1/3 与 2/3 处各抽一帧 (质量判断足够, 速度快)."""
    try:
        dur_probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=20,
        )
        dur = float(dur_probe.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        dur = 6.0
    frames = []
    for i, ratio in enumerate((0.33, 0.72)):
        out = tmpdir / f"q{i}.jpg"
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-ss", f"{max(dur * ratio - 0.1, 0):.2f}",
                 "-i", str(video), "-vframes", "1", "-q:v", "3", str(out)],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
                frames.append(out)
        except subprocess.TimeoutExpired:
            continue
    return frames


def _parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        s = int(d.get("quality_score", 0))
        if 1 <= s <= 10:
            return {"score": s, "generic": bool(d.get("generic_stock")),
                    "verdict": str(d.get("verdict", ""))[:60]}
    except Exception:
        return None
    return None


def score_video_file(path: str | Path) -> dict | None:
    """给单个视频文件打质量分. Returns {score, generic, verdict} | None(不可用/失败)."""
    client = _get_client()
    if client is None:
        return None
    video = Path(path)
    if not video.exists():
        return None
    with tempfile.TemporaryDirectory() as td:
        frames = _extract_two_frames(video, Path(td))
        if not frames:
            return None
        for _ in range(2):  # 空响应重试一次
            try:
                raw = client.chat_with_images(
                    image_paths=frames,
                    system_prompt=QUALITY_SYSTEM_PROMPT,
                    user_prompt="给这条 B-roll 素材打质量分。",
                )
            except Exception as exc:
                logger.warning("[quality] VLM 调用失败: %s", exc)
                return None
            parsed = _parse(raw)
            if parsed:
                return parsed
    return None


# ── 异步质检队列 (2026-08-16): 下载不等待, 后台单线程补打分 ──
# 生产考量: 同步打分 ~5s×45 slot ≈ 每片多 4~6 分钟 (用户指出); 异步模式下
# 下载即返回, 分数秒级落库 → 同 job 后续 slot 与未来全片受保护, 仅当前
# slot 已下载的素材可能带病上岗 (最坏情况: 一条片混入个别烂镜头)。
import queue
import threading

_score_queue: "queue.Queue[str]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def _score_worker() -> None:
    from app.database import get_session_maker
    from app.models import VideoAsset

    while True:
        path = _score_queue.get()
        try:
            result = score_video_file(path)
            if result is None:
                continue
            db = get_session_maker()()
            try:
                asset = db.query(VideoAsset).filter(VideoAsset.file_path == path).first()
                if asset is not None:
                    asset.quality_score = float(result["score"])
                    asset.quality_reason = ("generic;" if result["generic"] else "") + result["verdict"]
                    if result["score"] <= 3:
                        asset.preference = "dislike"
                    db.commit()
                    logger.info("[quality-async] %s → %d 分 %s", path.rsplit("/", 1)[-1], result["score"], result["verdict"])
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[quality-async] %s: %s", path, exc)
        finally:
            _score_queue.task_done()


def enqueue_quality_check(file_path: str) -> None:
    """异步打分入口: 立即返回, 后台落库 (≤3 分自动标 dislike 出局)."""
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_score_worker, daemon=True, name="asset-quality").start()
            _worker_started = True
    _score_queue.put(file_path)
