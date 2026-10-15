"""Director 2.0 router 包: 任务生命周期端点按阶段分组.

原单文件 `app/routers/director.py`(860 行)拆分而来。`router` 聚合 8 个
分组 APIRouter, `main.py` 经 ``from .routers.director_routes import router``
接入 (属性名与旧 `routers.director.router` 保持一致)。

分组:
  list.py      列任务/详情/slot 列表
  planning.py  创建任务 + 后台规划线程
  execution.py 执行 + 取消
  retry.py     单 slot 重试 / 批量重试 / purge replaced
  compose.py   合成 + SSE 进度透传
  download.py  下载 / 打开目录
  stream.py    SSE 进度流
  cleanup.py   删除 / 批量清理
"""
from __future__ import annotations

from fastapi import APIRouter

from app.routers.director_routes.cleanup import cleanup_router
from app.routers.director_routes.compose import compose_router
from app.routers.director_routes.download import download_router
from app.routers.director_routes.execution import execution_router
from app.routers.director_routes.jy_export import jy_router
from app.routers.director_routes.list import list_router
from app.routers.director_routes.planning import planning_router
from app.routers.director_routes.retry import retry_router
from app.routers.director_routes.stream import stream_router

# 兼容 main.py: `from .routers.director_routes import router`
router = APIRouter(prefix="/api/director", tags=["director"])
for _sub in (
    list_router,
    planning_router,
    execution_router,
    retry_router,
    compose_router,
    download_router,
    jy_router,
    stream_router,
    cleanup_router,
):
    router.include_router(_sub)

__all__ = ["router"]
