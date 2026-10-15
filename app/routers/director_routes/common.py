"""Director router 公共工具: 管线约束编解码 / 404 守卫 / 产物查找 / 文件清理.

与 slot_workflows 批次的 common.py 同理 — 只放无状态的纯工具函数与常量,
不承载端点逻辑, 避免多子模块重复定义。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import DirectorJob, DirectorSlot

# workflow → pipeline tag 映射 (与 slot_executor._EXECUTION_PHASES 保持一致)
_WORKFLOW_PIPELINE: dict[str, str | None] = {
    "host": "c",
    "mixed_host_broll": "c",
    "broll_pexels": "p",
    "evidence_image": "p",  # 证据图管线③ (2026-09-04): 与 broll_pexels 同 P 线
    "hf_chart": "h",
    "hf_title": "h",
    "broll_local": None,
    "black_placeholder": None,
}

__all__ = [
    "_WORKFLOW_PIPELINE",
    "_encode_pipelines",
    "_decode_pipelines",
    "_job_or_404",
    "_slot_or_404",
    "_find_composition_mp4",
    "_cleanup_job_files",
]


# ── 管线约束编解码 ──
# 三态语义 (2026-08-07 修复 "全开→None→无约束→host 回归" 根因):
#   None         = 全启用 (无约束, 允许 host) — 与 parser/executor 的 None 语义一致
#   set()        = 全关 (无任何管线可用, 只跑 broll_local/black_placeholder 兜底)
#   {"c","p"}    = 部分启用 (约束块 + _strip_host_mode 生效)
# 落库列 (job.pipelines): None = 全启用, "" = 全关, "c,p" = 部分启用。
# "" 必须与 None 区分: 若全关落库成 None, execute/retry 回退到 job.pipelines 时会
# 退化成全启用, fallback 链就可能引入 P/C 线 (replace_failed_slot 的 None 走 legacy 链)。
def _encode_pipelines(enabled: set[str] | None) -> str | None:
    if enabled is None:
        return None            # 全启用
    return ",".join(sorted(enabled)) if enabled else ""   # 部分启用 / 全关


def _decode_pipelines(raw: str | None) -> set[str] | None:
    """None → None(全启用); "" → set()(全关); 非空 → 管线集合 (非法值过滤)."""
    if raw is None:
        return None
    if not raw.strip():
        return set()
    return {p.strip().lower() for p in raw.split(",") if p.strip()} & {"c", "p", "h"}


def _job_or_404(db: Session, job_id: str) -> DirectorJob:
    job = db.get(DirectorJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"DirectorJob {job_id} not found")
    return job


def _slot_or_404(db: Session, job_id: str, slot_id: str) -> DirectorSlot:
    slot = db.get(DirectorSlot, slot_id)
    if slot is None or slot.director_job_id != job_id:
        raise HTTPException(
            status_code=404, detail=f"DirectorSlot {slot_id} not in job {job_id}"
        )
    return slot


def _find_composition_mp4(job_id: str) -> str | None:
    """回退:在 composition_output_root 下找 director_<job_id>.mp4."""
    from app.config import get_config

    cfg = get_config()
    root = Path(cfg.defaults.composition_output_root) / job_id
    if not root.exists():
        return None
    cand = root / f"director_{job_id}.mp4"
    return str(cand) if cand.exists() else None


def _cleanup_job_files(job_id: str) -> None:
    """Remove director output + composition directories for a job."""
    import shutil
    from app.config import get_config
    import logging

    logger = logging.getLogger(__name__)
    cfg = get_config()
    dirs_to_remove = [
        Path(cfg.defaults.director_output_root) / job_id,
        Path(cfg.defaults.composition_output_root) / job_id,
    ]
    for d in dirs_to_remove:
        if d.exists() and d.is_dir():
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception as exc:
                logger.warning("[cleanup] failed to remove %s: %s", d, exc)
