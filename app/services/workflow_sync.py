"""Workflow SSOT 同步服务 — 项目 → ComfyUI 运行时.

54321 Web 启动期跑一次:
- 读 workflows/manifest.yaml
- 对每个 workflow 比对 source SHA256 vs runtime SHA256
- 不一致 / 缺 → 覆盖, 记录 workflow_syncs
- 写 E:\\数字人计划\\logs\\workflow-sync-<timestamp>.log
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# paths
WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "workflows"
MANIFEST_PATH = WORKFLOWS_DIR / "manifest.yaml"
LOG_DIR = Path(r"E:/数字人计划/logs")


def _sha256(p: Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def sync_workflows_on_startup() -> list[dict[str, Any]]:
    """54321 Web 启动期跑一次;返回每条 workflow 的同步结果."""
    results: list[dict[str, Any]] = []
    if not MANIFEST_PATH.exists():
        logger.warning("[workflow_sync] manifest.yaml not found at %s", MANIFEST_PATH)
        return results

    raw = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
    wf_list = raw.get("workflows", [])
    if not isinstance(wf_list, list):
        return results

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"workflow-sync-{_now_ts()}.log"

    for wf in wf_list:
        name = wf.get("name", "<unknown>")
        source_rel = wf.get("source", "")
        runtime_rel = wf.get("runtime_path", "")
        auto_sync = wf.get("auto_sync", True)

        src = WORKFLOWS_DIR / source_rel
        dst = Path(runtime_rel) if runtime_rel else None

        src_sha = _sha256(src) if src.exists() else None
        dst_sha = _sha256(dst) if (dst and dst.exists()) else None

        synced = False
        reason = "skipped"

        if not auto_sync:
            reason = "auto_sync=false"
        elif src_sha is None:
            reason = "source_missing"
        elif dst_sha is None:
            # runtime 缺 → 复制
            if dst is not None:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                dst_sha = _sha256(dst)
                synced = True
                reason = "missing"
        elif dst_sha != src_sha:
            # 不一致 → 覆盖
            if dst is not None:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                dst_sha = _sha256(dst)
                synced = True
                reason = "diff"
        else:
            reason = "in_sync"

        results.append(
            {
                "name": name,
                "src_sha256": src_sha,
                "dst_sha256": dst_sha,
                "synced": synced,
                "reason": reason,
            }
        )
        logger.info(
            "[workflow_sync] %s → synced=%s reason=%s src=%s dst=%s",
            name,
            synced,
            reason,
            src_sha[:8] if src_sha else None,
            dst_sha[:8] if dst_sha else None,
        )

        # 写 workflow_syncs 表
        try:
            from ..database import get_session_maker
            from ..models import WorkflowSync

            Session = get_session_maker()
            if Session is not None:
                with Session() as db:
                    rec = WorkflowSync(
                        workflow_name=name,
                        source_sha256=src_sha,
                        runtime_sha256=dst_sha,
                        synced=synced,
                        reason=reason,
                    )
                    db.add(rec)
                    db.commit()
        except Exception as exc:
            logger.warning("[workflow_sync] DB write failed for %s: %s", name, exc)

    # 写日志文件
    try:
        log_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("[workflow_sync] log file write failed: %s", exc)

    logger.info("[workflow_sync] %d workflows synced", len(results))
    return results