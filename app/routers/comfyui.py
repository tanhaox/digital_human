"""ComfyUI HTTP client + workflow 同步接口.

约定:
- 不在 54321 Web 之外加新端口
- 不暴露 ComfyUI 内部参数(checkpoint/LoRA 路径)
- 所有 ComfyUI 调用必须走 54321 Web (本 router)

设计:
- 启动期通过 app/services/workflow_sync.py 把项目 workflows/ 同步到 ComfyUI user/default/workflows/
- 提交时按 manifest.yaml 装载 workflow JSON + 注入 inputs (app/services/comfyui_service.py)
- 轮询 /history/{prompt_id} 拿产物
- 产物落 E:/数字人计划/roles/<role_id>/<view>.png
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from app.config import get_config
from app.database import get_session_maker
from app.models import Role, WorkflowSync
from app.schemas import (
    ComfyUISubmitRequest,
    ComfyUISubmitResponse,
    ComfyUIWorkflowInfo,
    WorkflowSyncOut,
)
from app.services import comfyui_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comfyui", tags=["comfyui"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/health")
def comfyui_health():
    """探测 ComfyUI 服务是否在线 (GET /) 并返回本机 ffmpeg 状态."""
    cfg = get_config()
    base = cfg.defaults.base_url_comfyui
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(base.rstrip("/") + "/")
        online = True
        status_code = r.status_code
        error = None
    except httpx.RequestError as exc:
        online = False
        status_code = None
        error = str(exc)
    return {
        "online": online,
        "base_url": base,
        "status_code": status_code,
        "error": error,
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }


@router.get("/workflows", response_model=list[ComfyUIWorkflowInfo])
def list_workflows():
    """列 manifest.yaml 中的 workflow."""
    return [ComfyUIWorkflowInfo(**wf) for wf in comfyui_service.load_manifest()]


@router.post("/submit", response_model=ComfyUISubmitResponse)
async def comfyui_submit(body: ComfyUISubmitRequest):
    """通用 workflow 提交 (测试 / 调试用).

    body.workflow_name → 找 manifest 条目 → 装入 JSON → 注入 inputs → 轮询 → 落盘.
    body.role_id 决定产物落到哪个角色目录.
    """
    cfg = get_config()
    wf_meta = comfyui_service.find_workflow(body.workflow_name)
    if not wf_meta:
        raise HTTPException(status_code=404, detail=f"workflow not in manifest: {body.workflow_name}")

    try:
        wf_json = comfyui_service.load_workflow_json(wf_meta["source"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    role_id = body.role_id
    if role_id:
        # 验证 role 存在
        Session = get_session_maker()
        if Session is None:
            raise HTTPException(status_code=500, detail="DB not initialized")
        with Session() as db:
            role = db.query(Role).filter(Role.id == role_id).first()
            if not role:
                raise HTTPException(status_code=404, detail=f"role not found: {role_id}")
            # 若 inputs 没传 description, 用角色 description
            if "description" not in body.inputs and role.description:
                body.inputs["description"] = role.description
            if "reference_image" not in body.inputs and role.reference_image:
                body.inputs["reference_image"] = role.reference_image
            if "seed" not in body.inputs and role.seed is not None:
                body.inputs["seed"] = role.seed

    payload = comfyui_service.inject_inputs(wf_json, body.inputs)

    result = await comfyui_service.submit_and_wait(
        workflow_payload=payload,
        role_id=role_id,
        base_url=cfg.defaults.base_url_comfyui,
        timeout_sec=cfg.defaults.comfyui_timeout_sec,
        output_view_order=wf_meta.get("output_view_order"),
    )

    return ComfyUISubmitResponse(**result)


@router.post("/workflows/sync")
def workflows_sync():
    """手动触发同步 — 项目 → ComfyUI user/default/workflows/.

    由 app/services/workflow_sync.py 实现真正的 sha256 对比逻辑;
    本路由作为外部触发入口.
    """
    from app.services.workflow_sync import sync_workflows_on_startup

    results = sync_workflows_on_startup()
    return {"synced": results}


@router.put("/workflows/{name}/pull")
def workflow_pull(name: str):
    """运行时 → 项目回流: 复制 ComfyUI user/default/workflows/<name>.json
    到项目 workflows/<source>, 用于在 ComfyUI UI 调参后回项目.

    注: 本端点当前保留为手动入口, 自动化留给后续 backlog.
    """
    wf_meta = comfyui_service.find_workflow(name)
    if not wf_meta:
        raise HTTPException(status_code=404, detail=f"workflow not in manifest: {name}")
    runtime_path = Path(wf_meta["runtime_path"])
    project_path = comfyui_service.WORKFLOWS_DIR / wf_meta["source"]
    if not runtime_path.exists():
        raise HTTPException(status_code=400, detail=f"runtime workflow missing: {runtime_path}")
    project_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(runtime_path), str(project_path))

    # 记录 workflow_syncs
    Session = get_session_maker()
    if Session is None:
        return {"status": "ok", "note": "DB unavailable, but file copied"}
    with Session() as db:
        rec = WorkflowSync(
            workflow_name=name,
            source_sha256=comfyui_service.sha256(project_path),
            runtime_sha256=comfyui_service.sha256(runtime_path),
            synced=True,
            reason="pull",
        )
        db.add(rec)
        db.commit()
    return {"status": "ok", "project_path": str(project_path)}


@router.get("/syncs", response_model=list[WorkflowSyncOut])
def list_syncs(limit: int = 50):
    """最近 N 条同步审计记录."""
    Session = get_session_maker()
    if Session is None:
        return []
    with Session() as db:
        rows = db.query(WorkflowSync).order_by(WorkflowSync.id.desc()).limit(limit).all()
        return rows
