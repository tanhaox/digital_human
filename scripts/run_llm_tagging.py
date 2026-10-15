#!/usr/bin/env python
"""全量 LLM 打标驱动 — 预抽帧完成后运行.

直接驱动 jobs.start_tagging_job, 不经过 FastAPI/uvicorn, 不启动后端额外服务.
只跑已预抽帧缓存的素材 (data/frames_cache/{id}/meta.json 存在) → 通道1/2/3
全部命中缓存, 仅通道4 LLM 打标 (640px JPEG 帧 → llama-server). 本地模型,
不消耗云端 token.

用法:
    python scripts/run_llm_tagging.py [--ids FILE|id1,id2] [--poll 30]

--ids 缺省 → 全量 = data/frames_cache 下所有有 meta.json 的素材 (已预抽帧).
带 lockfile (data/llm_tagging.lock) 防重入: 另一个实例在跑时直接退出.

日志: logs/llm_tagging.log
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db  # noqa: E402
from app.services.video_tagging_service.llama import _ensure_llama_server  # noqa: E402

LOG_FILE = PROJECT_ROOT / "logs" / "llm_tagging.log"
LOCK_FILE = PROJECT_ROOT / "data" / "llm_tagging.lock"
TERMINAL_STATES = ("completed", "failed", "cancelled")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _resolve_ids(args_ids: str | None) -> list[str] | None:
    """解析素材 id 列表; None → 全量 = 预抽帧缓存目录中已有 meta.json 的素材."""
    if args_ids:
        if Path(args_ids).exists():
            return json.loads(Path(args_ids).read_text(encoding="utf-8"))
        return [x.strip() for x in args_ids.split(",") if x.strip()]
    cache_dir = PROJECT_ROOT / "data" / "frames_cache"
    if not cache_dir.is_dir():
        logging.getLogger("llm_tagging").warning("预抽帧缓存目录不存在: %s", cache_dir)
        return None
    return [d.name for d in cache_dir.iterdir()
            if d.is_dir() and (d / "meta.json").exists()]


def main() -> int:
    _setup_logging()
    logger = logging.getLogger("llm_tagging")
    parser = argparse.ArgumentParser(description="预抽帧后全量 LLM 打标 (缓存命中)")
    parser.add_argument("--ids", help="素材 id 列表文件(JSON数组) 或逗号分隔串; 缺省=已预抽帧素材")
    parser.add_argument("--poll", type=int, default=30, help="状态轮询间隔秒")
    args = parser.parse_args()

    # 防重入: 另一个实例在跑 → 直接退出 (避免定时唤醒与串联任务双触发)
    if LOCK_FILE.exists():
        logger.warning("另一个 LLM 打标已在运行 (lock: %s), 退出", LOCK_FILE)
        return 0
    LOCK_FILE.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    try:
        cfg = load_config()
        set_config(cfg)
        init_db(cfg.app.database_url)

        from app.services.video_tagging_service.jobs import (
            get_job_status,
            start_tagging_job,
        )

        if not _ensure_llama_server():
            logger.error("llama-server 无法启动, 任务中止")
            return 1

        asset_ids = _resolve_ids(args.ids)
        if not asset_ids:
            logger.warning("没有可打标的素材 (预抽帧缓存为空), 退出")
            return 0
        logger.info("启动 LLM 全量打标: %d 个素材 (缓存命中, 仅通道4)", len(asset_ids))

        job_id = start_tagging_job(asset_ids)
        logger.info("job_id=%s 开始轮询状态 (间隔 %ds)", job_id, args.poll)

        last_done, last_failed = -1, -1
        while True:
            st = get_job_status(job_id)
            if st is None:
                logger.error("job 状态丢失 (进程重启?), 退出")
                return 1
            done, failed, total = st["done"], st["failed"], st["total"]
            if done != last_done or failed != last_failed:
                logger.info("[进度] %d 完成 / %d 失败 / 共 %d | 当前: %s | 状态: %s",
                            done, failed, total, st.get("current_asset_no"), st["status"])
                last_done, last_failed = done, failed
            if st["status"] in TERMINAL_STATES:
                break
            time.sleep(args.poll)

        st = get_job_status(job_id)
        logger.info("LLM 打标结束: %s | %d 完成 / %d 失败 / 共 %d",
                    st["status"], st["done"], st["failed"], st["total"])
        return 0 if st["status"] == "completed" else 1
    finally:
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
