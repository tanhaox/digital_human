"""角色管理路由 — ComfyUI 多视图定型 + 数字人主流水线入参.

7 端点:
- GET    /api/roles
- POST   /api/roles
- GET    /api/roles/{id}
- PUT    /api/roles/{id}
- DELETE /api/roles/{id}
- POST   /api/roles/{id}/generate-views
- POST   /api/roles/{id}/apply
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import get_config
from ..database import get_db
from ..models import Role
from ..schemas import (
    ApplyRoleResponse,
    GenerateViewsRequest,
    GenerateViewsResponse,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)
from app.services import comfyui_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/roles", tags=["roles"])


@router.get("", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db)):
    return db.query(Role).order_by(Role.created_at.desc()).all()


@router.get("/{role_id}", response_model=RoleOut)
def get_role(role_id: str, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.post("", response_model=RoleOut, status_code=201)
async def create_role(
    name: str = Form(...),
    description: str = Form(""),
    workflow_used: str = Form("character_three_view"),
    seed: int | None = Form(None),
    reference: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传参考图 + 创建角色记录.

    reference → E:/数字人计划/roles/<id>/ref.<ext>
    """
    cfg = get_config()
    role_id = str(uuid.uuid4())
    role_dir = Path(cfg.defaults.roles_output_root) / role_id
    role_dir.mkdir(parents=True, exist_ok=True)

    # 保存参考图
    suffix = Path(reference.filename or "ref.png").suffix or ".png"
    ref_path = role_dir / f"ref{suffix}"
    blob = await reference.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Empty reference image")
    if len(blob) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Reference image > 10MB")
    ref_path.write_bytes(blob)

    role = Role(
        id=role_id,
        name=name,
        reference_image=str(ref_path),
        description=description,
        workflow_used=workflow_used,
        seed=seed,
        views={},
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.put("/{role_id}", response_model=RoleOut)
def update_role(role_id: str, body: RoleUpdate, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(role, k, v)
    db.commit()
    db.refresh(role)
    return role


@router.delete("/{role_id}", status_code=204)
def delete_role(role_id: str, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    # 不删磁盘,只删 DB 行 — 用户手动清理角色目录
    db.delete(role)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# 静态文件代理:前端不能用 file:/// 访问本地图片,必须走后端 FileResponse
# ---------------------------------------------------------------------------
@router.get("/{role_id}/reference-image")
def get_reference_image(role_id: str, db: Session = Depends(get_db)):
    """返回角色参考图(仅该角色目录下的文件)."""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    p = Path(role.reference_image)
    if not p.exists():
        raise HTTPException(status_code=410, detail="reference image missing")
    if not _is_under_role_dir(p, role_id):
        raise HTTPException(status_code=403, detail="path outside role directory")
    return FileResponse(str(p), media_type=_guess_media_type(p))


@router.get("/{role_id}/views/{view_name}")
def get_view_image(role_id: str, view_name: str, db: Session = Depends(get_db)):
    """返回角色指定视图图片(view_name 为 front / side / full)."""
    if view_name not in {"front", "side", "full"}:
        raise HTTPException(
            status_code=422, detail="view_name must be front, side or full"
        )
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    views = role.views or {}
    path_str = views.get(view_name)
    if not path_str:
        raise HTTPException(status_code=404, detail=f"view '{view_name}' not generated")
    p = Path(path_str)
    if not p.exists():
        raise HTTPException(status_code=410, detail="view image missing")
    if not _is_under_role_dir(p, role_id):
        raise HTTPException(status_code=403, detail="path outside role directory")
    return FileResponse(str(p), media_type=_guess_media_type(p))


def _is_under_role_dir(p: Path, role_id: str) -> bool:
    """安全校验:文件必须位于该 role 的专属目录下."""
    cfg = get_config()
    role_dir = Path(cfg.defaults.roles_output_root) / role_id
    try:
        p.resolve().relative_to(role_dir.resolve())
        return True
    except ValueError:
        return False


def _guess_media_type(p: Path) -> str:
    ext = p.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")


@router.post("/{role_id}/generate-views", response_model=GenerateViewsResponse)
async def generate_views(
    role_id: str,
    body: GenerateViewsRequest | None = None,
    db: Session = Depends(get_db),
):
    """调 ComfyUI 出 3 张视图 (正脸/侧脸/全身).

    - body=None → 使用角色当前 description / reference_image / seed
    - body.seed / body.description_override / body.reference_image 可覆盖

    阻塞调用 (客户端拿 200 + views 字典);SSE 留给后续 backlog (ID-008).
    """
    cfg = get_config()
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    body = body or GenerateViewsRequest()
    description = body.description_override or role.description
    reference_image = body.reference_image or role.reference_image
    seed = body.seed if body.seed is not None else role.seed

    # 锁 seed: 若用户传 seed → 覆盖角色; 否则保持角色 seed
    if body.seed is not None and role.seed != body.seed:
        role.seed = body.seed
        db.commit()
        db.refresh(role)

    wf_meta = comfyui_service.find_workflow(role.workflow_used)
    if not wf_meta:
        raise HTTPException(
            status_code=400,
            detail=f"workflow not in manifest: {role.workflow_used}",
        )

    try:
        wf_json = comfyui_service.load_workflow_json(wf_meta["source"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    payload = comfyui_service.inject_inputs(
        wf_json,
        {
            "description": description,
            "reference_image": reference_image,
            "seed": seed,
        },
    )

    result = await comfyui_service.submit_and_wait(
        workflow_payload=payload,
        role_id=role_id,
        base_url=cfg.defaults.base_url_comfyui,
        timeout_sec=cfg.defaults.comfyui_timeout_sec,
        output_view_order=wf_meta.get("output_view_order"),
    )

    status = result["status"]
    if status == "completed":
        role.views = result["views"]
        db.commit()
        db.refresh(role)
    elif status == "failed":
        raise HTTPException(status_code=502, detail=result.get("error") or "ComfyUI failed")
    elif status == "timeout":
        raise HTTPException(status_code=504, detail=result.get("error") or "ComfyUI timeout")

    return GenerateViewsResponse(
        role_id=role_id,
        prompt_id=result["prompt_id"],
        status=status,
        views=result["views"],
        error=result.get("error"),
    )


@router.post("/{role_id}/apply", response_model=ApplyRoleResponse)
def apply_role(role_id: str, db: Session = Depends(get_db)):
    """把角色应用到主流水线 — 当前仅返回入参结构 (留接口位).

    真正接 A 管线分镜待 ID-007 backlog.
    """
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if not role.views:
        raise HTTPException(
            status_code=400,
            detail="Role has no views yet — 请先生成多视图",
        )

    mainstream_input: dict[str, Any] = {
        "host_name": role.name,
        "host_reference": role.reference_image,
        "host_views": role.views,
        "host_description": role.description,
        "workflow_used": role.workflow_used,
        "seed": role.seed,
    }
    return ApplyRoleResponse(
        applied=True,
        role_id=role_id,
        mainstream_input=mainstream_input,
        note="主流水线分镜接入待 ID-007 backlog",
    )


# ---------------------------------------------------------------------------
# 形象组上传 (新)
# ---------------------------------------------------------------------------

CAMERA_LABELS = {"1": "正面半身", "2": "左侧45度", "3": "右侧45度", "4": "正面特写"}


@router.post("/{role_id}/groups")
async def upload_view_group(
    role_id: str,
    name: str = Form(...),
    cam1: UploadFile = File(...),
    cam2: UploadFile = File(...),
    cam3: UploadFile = File(...),
    cam4: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传一组 4 张机位图。返回更新后的 view_groups。"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    cfg = get_config().defaults
    group_dir = Path(role.reference_image).parent / "groups" / f"group_{len(role.view_groups or [])}"
    group_dir.mkdir(parents=True, exist_ok=True)

    cameras: dict[str, str] = {}
    for cam_key, file in [("1", cam1), ("2", cam2), ("3", cam3), ("4", cam4)]:
        ext = Path(file.filename or "img.png").suffix or ".png"
        dest = group_dir / f"cam{cam_key}{ext}"
        content = await file.read()
        dest.write_bytes(content)
        cameras[cam_key] = str(dest)

    groups = list(role.view_groups or [])
    groups.append({"name": name, "cameras": cameras})
    role.view_groups = groups
    db.commit()
    db.refresh(role)
    return {"role_id": role_id, "view_groups": role.view_groups}


@router.delete("/{role_id}/groups/{group_index}")
def delete_view_group(role_id: str, group_index: int, db: Session = Depends(get_db)):
    """删除指定形象组。"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    groups = list(role.view_groups or [])
    if group_index < 0 or group_index >= len(groups):
        raise HTTPException(status_code=404, detail="Group index out of range")
    groups.pop(group_index)
    role.view_groups = groups
    db.commit()
    return {"role_id": role_id, "view_groups": role.view_groups}


@router.get("/{role_id}/groups/{group_index}/cameras/{cam_key}")
def get_camera_image(role_id: str, group_index: int, cam_key: str, db: Session = Depends(get_db)):
    """返回指定组指定机位的图片。"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    groups = role.view_groups or []
    if group_index < 0 or group_index >= len(groups):
        raise HTTPException(status_code=404, detail="Group not found")
    cameras = groups[group_index].get("cameras") or {}
    img_path = cameras.get(cam_key)
    if not img_path or not Path(img_path).exists():
        raise HTTPException(status_code=404, detail="Camera image not found")
    return FileResponse(img_path)