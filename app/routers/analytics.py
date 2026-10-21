"""数据看板 API — 抖音账号逐日指标 + 粉丝增长摘要 (/web/analytics.html 消费).

GET  /api/analytics/daily   全量载荷 (粉丝摘要 + 视频侧 + 挂钩 + 里程碑 + 预警)
POST /api/analytics/import  上传《数据表现》xlsx 直接入库 (免 CLI)
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..services.analytics_import import import_xlsx
from ..services.analytics_service import dashboard_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/daily")
def daily():
    """全量逐日行 (douyin_daily) + 摘要 (异常日/常态基线/周桶/相关性)."""
    return dashboard_payload()


@router.post("/import")
async def import_upload(file: UploadFile = File(...)):
    """上传数据表现 xlsx → 按日 upsert → 返回导入报告 (页面刷新即见新数据)."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        raise HTTPException(400, f"仅支持 xlsx, 收到: {file.filename!r}")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        report = import_xlsx(tmp_path, source_name=file.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # openpyxl 解析失败等
        logger.exception("[analytics] xlsx 导入失败")
        raise HTTPException(400, f"解析失败: {exc}") from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # Windows 句柄迟释放 — 残留临时文件好过 500
            logger.warning("[analytics] 临时文件清理失败 (句柄未释放): %s", tmp_path)
    logger.info("[analytics] 页面上传导入 %s: %s", file.filename, report)
    return report
