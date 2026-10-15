"""预抽帧 + OCR 预扫 — 一次性脚本, 为后续 LLM 打标准备缓存.

设计:
- 纯 CLI 独立运行, 不经过 FastAPI/uvicorn, 不占用后端与 llama-server.
- 阶段①: 对素材 scdet 一遍解码 → 同时算 motion (原通道2, 省第二遍解码)
- 阶段②: 抽帧, 宽 ≤ 640 (原尺寸已 < 640 不缩放) → data/frames_cache/{asset_id}/
- 阶段③: easyocr 扫缓存帧 → 文字语言/国旗/location hint
- 写 meta.json: motion + ocr + 帧清单 + file_size/mtime (缓存键, 素材被替换则失效重抽)
- 并发: 抽帧 2 线程 + OCR 串行 — 笔记本不抢 CPU/GPU

用法:
    python scripts/preprocess_frames.py [--ids FILE|id1,id2] [--max-workers 2]
    --ids 缺省 → 全库 1467 素材; --ids _street_ids.json → 只跑街景

后续 LLM 打标: 复用 POST /api/library/tagging/run, pipeline 通道1/2/3 查缓存命中跳过.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db  # noqa: E402
from app.services.motion_analysis import analyze_motion  # noqa: E402

logger = logging.getLogger("preprocess_frames")

MAX_WIDTH = 640              # 帧最大宽度 (等比缩放, 只降不升)
CACHE_ROOT = None            # 延迟设置: data/frames_cache
LOG_FILE = PROJECT_ROOT / "logs" / "preprocess_frames.log"

# easyocr Reader 为进程级单例且非线程安全 → OCR 全局串行锁
_OCR_LOCK = threading.Lock()


def _setup_logging() -> None:
    """控制台 + 文件双路日志."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)


def _cache_dir(aid: str) -> Path:
    return CACHE_ROOT / aid


def _asset_cache_key(file_path: str, duration: float) -> dict:
    """缓存键: 文件大小 + mtime + 时长. 任一变化 → 缓存失效重抽."""
    p = Path(file_path)
    st = p.stat()
    return {
        "file_size": st.st_size,
        "mtime": int(st.st_mtime),
        "duration": duration,
    }


def _get_duration(video_path: str) -> float:
    """ffprobe 获取时长 (秒)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _is_ready(aid: str, video_path: str, duration: float) -> bool:
    """缓存是否就绪: 目录存在 + meta.json 可解析 + 缓存键一致."""
    meta_path = _cache_dir(aid) / "meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return meta.get("cache_key") == _asset_cache_key(video_path, duration) and bool(meta.get("frames"))


def _run_ocr(frames: list[Path]) -> dict:
    """OCR 预扫 (easyocr 全局单例, 只扫前 3 帧). 失败降级空结果.

    Reader 非线程安全 → 全程持锁串行.
    """
    global _reader
    try:
        from app.services.ocr_scan import ocr_scan_frames
    except Exception as exc:
        logger.warning("ocr_scan_frames import failed: %s", exc)
        return {"text_language": "unknown", "has_flag": False, "location_hint": "uncertain", "available": False}
    try:
        with _OCR_LOCK:
            return ocr_scan_frames(frames[:3])
    except Exception as exc:
        logger.warning("OCR scan failed: %s", exc)
        return {"text_language": "unknown", "has_flag": False, "location_hint": "uncertain", "available": False}


def preprocess_one(asset: dict) -> dict:
    """处理单个素材: 抽帧(640) + motion + OCR → 写缓存. 返回状态."""
    aid = asset["id"]
    video_path = asset["file_path"]
    duration = asset["duration_sec"] or _get_duration(video_path)
    d = _cache_dir(aid)

    if _is_ready(aid, video_path, duration):
        return {"aid": aid, "status": "cached", "asset_no": asset["asset_no"]}

    try:
        # 缓存帧目录: 幂等重建
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("frame_*.png"):
            old.unlink(missing_ok=True)

        # 通道1: 抽帧 (640 宽, 复用现有 smart_extract_frames)
        from app.services.frame_extraction import smart_extract_frames
        frames = smart_extract_frames(
            video_path, duration, d,
            frame_prefix="frame_", max_width=MAX_WIDTH,
        )
        if not frames:
            logger.warning("[%s] 无帧抽出, 跳过", asset["asset_no"])
            return {"aid": aid, "status": "no_frames", "asset_no": asset["asset_no"]}

        # PNG → JPEG (q85): 截图 JPEG 体积约为 PNG 的 1/4-1/5.
        # 全库 ~1467×10 帧 PNG 约 8GB → JPEG 约 2GB; 且 LLM 阶段 base64
        # 请求体同幅缩小, prefill 更小更快. OCR 对 JPEG 无感知差异.
        jpg_frames = _png_to_jpg(frames)

        # 通道2: 运动分析 (scdet 复用 — 抽帧 scdet 已算过, 此调用是第二遍; 保持独立)
        motion = analyze_motion(video_path, duration)

        # 通道3: OCR (只扫前 3 帧)
        ocr = _run_ocr(jpg_frames)

        meta = {
            "cache_key": _asset_cache_key(video_path, duration),
            "asset_no": asset["asset_no"],
            "duration": duration,
            "width": int(frames[0] and _png_size(frames[0])[0] or 0),
            "height": int(frames[0] and _png_size(frames[0])[1] or 0),
            "frame_files": [f.name for f in sorted(jpg_frames)],
            "frame_count": len(jpg_frames),
            "motion": motion,
            "ocr": ocr,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path = d / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"aid": aid, "status": "ok", "asset_no": asset["asset_no"], "frames": len(jpg_frames)}
    except Exception as exc:
        logger.error("[%s] 预抽帧失败: %s", asset["asset_no"], exc)
        return {"aid": aid, "status": "error", "asset_no": asset["asset_no"], "error": str(exc)}


def _png_to_jpg(png_frames: list[Path], quality: int = 85) -> list[Path]:
    """将缓存帧 PNG 转 JPEG (q85), 保留原始帧一并删除, 返回 JPEG 路径列表."""
    out: list[Path] = []
    for p in png_frames:
        jp = p.with_suffix(".jpg")
        try:
            from PIL import Image
            with Image.open(p) as im:
                im.convert("RGB").save(jp, "JPEG", quality=quality)
            p.unlink(missing_ok=True)   # 原 PNG 删除 (缓存产物, 属 build/cache 豁免)
            out.append(jp)
        except Exception as exc:
            logger.warning("PNG→JPG 转换失败 %s: %s (保留 PNG)", p.name, exc)
            out.append(p)   # 失败保留原 PNG, LLM/OCR 仍可用
    return out


def _png_size(p: Path) -> tuple[int, int]:
    """读 PNG 宽高 (仅文件头, 不加载全图)."""
    try:
        with open(p, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            import struct
            w, h = struct.unpack(">II", head[16:24])
            return int(w), int(h)
    except Exception:
        pass
    return (0, 0)


def main() -> None:
    global CACHE_ROOT, _reader
    _setup_logging()
    parser = argparse.ArgumentParser(description="预抽帧 + OCR 预扫, 为 LLM 打标备缓存")
    parser.add_argument("--ids", help="素材 id 列表文件(JSON数组) 或逗号分隔 id 串")
    parser.add_argument("--max-workers", type=int, default=2, help="抽帧并发线程 (笔记本建议 2)")
    args = parser.parse_args()

    cfg = load_config()
    set_config(cfg)
    init_db(cfg.app.database_url)
    CACHE_ROOT = PROJECT_ROOT / "data" / "frames_cache"
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    # 加载素材
    from app.database import db_session
    from app.models import VideoAsset

    with db_session() as s:
        q = s.query(VideoAsset.id, VideoAsset.asset_no, VideoAsset.file_path, VideoAsset.duration_sec)
        if args.ids:
            if Path(args.ids).exists():
                ids = json.loads(Path(args.ids).read_text(encoding="utf-8"))
            else:
                ids = [x.strip() for x in args.ids.split(",") if x.strip()]
            assets = [{"id": r[0], "asset_no": r[1], "file_path": r[2], "duration_sec": r[3]}
                      for r in q.filter(VideoAsset.id.in_(ids)).all()]
            logger.info("指定 %d 个素材", len(assets))
        else:
            assets = [{"id": r[0], "asset_no": r[1], "file_path": r[2], "duration_sec": r[3]} for r in q.all()]
            logger.info("全库 %d 个素材", len(assets))

    # 过滤缺失文件
    missing = [a for a in assets if not Path(a["file_path"]).exists()]
    if missing:
        logger.warning("跳过 %d 个文件缺失素材 (首个: %s)", len(missing), missing[0]["file_path"])
    assets = [a for a in assets if Path(a["file_path"]).exists()]

    cached = sum(1 for a in assets if _is_ready(a["id"], a["file_path"], a["duration_sec"] or 0))
    logger.info("已缓存 %d / 待处理 %d", cached, len(assets) - cached)

    t0 = time.time()
    ok = cached_ok = no_frame = err = 0
    with ThreadPoolExecutor(max_workers=args.max_workers, thread_name_prefix="preprocess") as ex:
        futs = {ex.submit(preprocess_one, a): a for a in assets}
        for i, fut in enumerate(as_completed(futs), 1):
            a = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"status": "error", "error": str(exc)}
                err += 1
            st = r.get("status")
            if st == "ok":
                ok += 1
            elif st == "cached":
                cached_ok += 1
            elif st == "no_frames":
                no_frame += 1
            elif st == "error":
                err += 1
            if i % 50 == 0 or i == len(assets):
                el = time.time() - t0
                rate = i / el
                eta = (len(assets) - i) / rate if rate > 0 else 0
                logger.info("[进度] %d/%d 处理中 → 新抽 %d / 缓存命中 %d / 无帧 %d / 失败 %d | 速率 %.1f 条/min | 预计剩余 %d min",
                            i, len(assets), ok, cached_ok, no_frame, err, rate * 60, int(eta / 60))

    el = time.time() - t0
    logger.info("预抽帧完成: 新抽 %d / 缓存命中 %d / 无帧 %d / 失败 %d, 总耗时 %.1f min",
                ok, cached_ok, no_frame, err, el / 60)
    logger.info("缓存目录: %s", CACHE_ROOT)


if __name__ == "__main__":
    main()
