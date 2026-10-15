"""Video Library router — 素材库 + 成品库 CRUD / 搜索 / 标签 / 偏好."""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_config
from app.database import get_db
from app.models import VideoAsset, VideoOutput
from app.schemas import (
    ImportFolderRequest,
    PexelsImageSearchRequest,
    PexelsImportRequest,
    PexelsOnlineSearchRequest,
    VideoAssetOut,
    VideoAssetPreferenceRequest,
    VideoAssetUpdate,
    VIDEO_LOCATION_CHOICES,
    VIDEO_ORIENTATION_CHOICES,
    VIDEO_PEOPLE_CHOICES,
    VIDEO_PREFERENCE_CHOICES,
    VIDEO_SCENE_CHOICES,
    VIDEO_SHOT_TYPE_CHOICES,
    VIDEO_SOURCE_TYPE_CHOICES,
)
from app.services import library_scan_service, library_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/assets")
def list_assets(
    q: str | None = Query(None, description="搜索编号/描述/query/摄影师/标签"),
    orientation: str | None = Query(None),
    source_type: str | None = Query(None),
    location: str | None = Query(None),
    people: str | None = Query(None),
    preference: str | None = Query(None),
    scenes: str | None = Query(None, description="逗号分隔, OR 匹配"),
    shot_types: str | None = Query(None, description="逗号分隔, OR 匹配"),
    tags: str | None = Query(None, description="逗号分隔标签过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """搜索/筛选素材库. 喜欢的素材排序置顶."""
    result = library_service.search_assets(
        db,
        q=q, orientation=orientation, source_type=source_type, location=location,
        people=people, preference=preference, scenes=scenes,
        shot_types=shot_types, tags=tags, limit=limit, offset=offset,
    )
    return {
        "total": result["total"],
        "items": [VideoAssetOut.model_validate(a).model_dump() for a in result["items"]],
    }


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, db: Session = Depends(get_db)):
    a = db.get(VideoAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    return VideoAssetOut.model_validate(a).model_dump()


@router.api_route("/assets/{asset_id}/file", methods=["GET", "HEAD"])
def get_asset_file(asset_id: str, request: Request, db: Session = Depends(get_db)):
    """返回素材视频文件，支持 HTTP Range 请求 (GET/HEAD).

    HEAD 由 uvicorn 在协议层抑制 body, 仅返回头 (Content-Type/Length) —
    浏览器 <video> 探测文件存在性与元数据依赖 HEAD, 缺它则 405.
    """
    a = db.get(VideoAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    p = Path(a.file_path)
    media_type = mimetypes.guess_type(str(p))[0] or "video/mp4"
    return library_service.video_file_response(request, p, media_type)


@router.put("/assets/{asset_id}")
def update_asset(asset_id: str, body: VideoAssetUpdate, db: Session = Depends(get_db)):
    """更新素材元数据(asset_no 不可改)."""
    a = db.get(VideoAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(a, key, value)
    db.commit()
    db.refresh(a)
    return VideoAssetOut.model_validate(a).model_dump()


@router.post("/assets/{asset_id}/preference")
def set_preference(asset_id: str, body: VideoAssetPreferenceRequest, db: Session = Depends(get_db)):
    """设置素材偏好 like / neutral / dislike. dislike 会进入黑名单, 后续 resolve 不再下载/返回."""
    a = db.get(VideoAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    a.preference = body.preference
    db.commit()
    return {"id": a.id, "preference": a.preference}


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, remove_file: bool = True, db: Session = Depends(get_db)):
    """删除素材记录，可选同时移物理文件到回收站."""
    a = db.get(VideoAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    if remove_file:
        p = Path(a.file_path)
        if p.exists():
            library_service.recycle_file(str(p))
    db.delete(a)
    db.commit()
    return {"status": "ok", "deleted": asset_id}


@router.get("/dimensions")
def list_dimensions():
    """返回所有筛选维度枚举值(前端下拉框用)."""
    return {
        "orientation": list(VIDEO_ORIENTATION_CHOICES),
        "source_type": list(VIDEO_SOURCE_TYPE_CHOICES),
        "location": list(VIDEO_LOCATION_CHOICES),
        "people": list(VIDEO_PEOPLE_CHOICES),
        "preference": list(VIDEO_PREFERENCE_CHOICES),
        "scenes": list(VIDEO_SCENE_CHOICES),
        "shot_types": list(VIDEO_SHOT_TYPE_CHOICES),
    }


@router.get("/tags")
def list_all_tags(db: Session = Depends(get_db)):
    """获取所有已使用的老 tags 去重(兼容旧素材)."""
    tag_set: set[str] = set()
    for (tags_list,) in db.query(VideoAsset.tags).filter(VideoAsset.tags.isnot(None)).all():
        if isinstance(tags_list, list):
            tag_set.update(tags_list)
    return sorted(tag_set)


@router.get("/folders")
def list_material_folders(db: Session = Depends(get_db)):
    """列出 materials_dir 下的一级子文件夹 (供导入弹窗选择)."""
    cfg = get_config()
    base = Path(cfg.defaults.materials_dir).resolve()
    if not base.is_dir():
        return {"materials_dir": str(base), "folders": []}
    folders = sorted(
        p.name for p in base.iterdir() if p.is_dir()
    )
    return {"materials_dir": str(base), "folders": folders}


@router.post("/import-folder")
def import_folder(body: ImportFolderRequest, db: Session = Depends(get_db)):
    """导入 materials_dir 下指定子文件夹并批量附加标签.

    为该文件夹下未入库的视频建立 VideoAsset 记录, 并统一附加 scenes /
    shot_types (合并去重). 标签可用库内既有枚举 (航拍/空镜/城市…), 也可新建.
    已入库素材跳过, 不追加标签.
    """
    cfg = get_config()
    materials_dir = Path(cfg.defaults.materials_dir)
    if not materials_dir.is_dir():
        raise HTTPException(400, f"materials_dir 不存在: {materials_dir}")
    try:
        result = library_scan_service.scan_materials_subdir(
            db,
            materials_dir,
            body.folder,
            scenes=body.scenes,
            shot_types=body.shot_types,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return result


@router.post("/scan")
def scan_materials(db: Session = Depends(get_db)):
    """扫描 materials_dir 下未入库的视频文件，自动创建 VideoAsset 记录.

    对每个新文件自动: 生成 asset_no (V{日期}-{序号}) / ffprobe 提取尺寸时长 /
    推断 orientation / source 设为 "local".
    """
    cfg = get_config()
    materials_dir = Path(cfg.defaults.materials_dir)
    if not materials_dir.is_dir():
        raise HTTPException(400, f"materials_dir 不存在: {materials_dir}")
    result = library_scan_service.scan_materials_dir(db, materials_dir)
    db.commit()
    return result


@router.get("/outputs")
def list_outputs(
    q: str | None = Query(None),
    orientation: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(VideoOutput)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(VideoOutput.title.ilike(like), VideoOutput.description.ilike(like)))
    if orientation:
        query = query.filter(VideoOutput.orientation == orientation)
    total = query.count()
    items = query.order_by(VideoOutput.created_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [library_service.output_to_dict(o) for o in items]}


@router.api_route("/outputs/{output_id}/file", methods=["GET", "HEAD"])
def get_output_file(output_id: str, request: Request, db: Session = Depends(get_db)):
    """返回成品视频文件，支持 HTTP Range 请求 (GET/HEAD)."""
    o = db.get(VideoOutput, output_id)
    if not o:
        raise HTTPException(404, "output not found")
    p = Path(o.file_path)
    media_type = mimetypes.guess_type(str(p))[0] or "video/mp4"
    return library_service.video_file_response(request, p, media_type)


@router.delete("/outputs/{output_id}")
def delete_output(output_id: str, remove_file: bool = True, db: Session = Depends(get_db)):
    o = db.get(VideoOutput, output_id)
    if not o:
        raise HTTPException(404, "output not found")
    if remove_file:
        p = Path(o.file_path)
        if p.exists():
            library_service.recycle_file(str(p))
    db.delete(o)
    db.commit()
    return {"status": "ok", "deleted": output_id}


# ── P线在线搜索 (2026-08-25): 素材库页独立于本地搜索的在线入口 ──
# 与本地搜索的分工: 本地搜索只查 VideoAsset 表; 在线搜索直连 Pexels API
# 返回元数据预览 (不下载), 用户勾选后调 /pexels/import 走 P线既有
# 下载/打标/编号/去重/配额/质检链路入库.


@router.post("/pexels/search")
def pexels_online_search(body: PexelsOnlineSearchRequest, db: Session = Depends(get_db)):
    """在线搜索 Pexels — 只返回预览元数据, 不消耗下载配额."""
    from app.services.pexels_service import PexelsAuthError, pexels_service
    from app.services.pexels_service._db import get_dislike_pexels_ids
    from app.services.pexels_service._http import search_pexels

    query = body.query.strip()
    if not query:
        raise HTTPException(400, "query 不能为空")
    try:
        pexels_service._ensure_config()
        videos = search_pexels(
            pexels_service, query,
            per_page=body.per_page, page=body.page, orientation=body.orientation or "any",
        )
    except PexelsAuthError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[library/pexels] online search failed: %s", exc)
        raise HTTPException(502, f"Pexels 搜索失败: {exc}")

    # 已入库/已拉黑标记 — 前端置灰防重复下载
    existing_ids = {
        pid for (pid,) in db.query(VideoAsset.pexels_id).filter(
            VideoAsset.pexels_id.isnot(None)
        ).all()
    }
    dislike_ids = get_dislike_pexels_ids(db)
    items = []
    for v in videos:
        pid = v.get("id")
        if not pid:
            continue
        items.append({
            "pexels_id": pid,
            "duration": v.get("duration"),
            "width": v.get("width"), "height": v.get("height"),
            "image": v.get("image"),
            "url": v.get("url"),
            "photographer": (v.get("user") or {}).get("name") or "",
            "in_library": pid in existing_ids,
            "disliked": pid in dislike_ids,
            "video": v,  # 完整 dict 回传, import 时免二次拉取
        })
    return {"items": items, "query": query}


@router.post("/pexels/image-search")
def pexels_image_search(body: PexelsImageSearchRequest):
    """在线搜索 Pexels 图片 (2026-09-06) — 图片反推页参考图.

    只返回预览元数据 (缩略图/大图 URL), 不下载不入库, 不消耗下载配额.
    图片与视频同 key 同 base, 复用 P线 http_session 与鉴权链路.
    """
    from app.services.pexels_service import PexelsAuthError, pexels_service
    from app.services.pexels_service._http import search_pexels_photos

    query = body.query.strip()
    if not query:
        raise HTTPException(400, "query 不能为空")
    try:
        pexels_service._ensure_config()
        photos = search_pexels_photos(
            pexels_service, query,
            per_page=body.per_page, page=body.page, orientation=body.orientation or "any",
        )
    except PexelsAuthError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[library/pexels] image search failed: %s", exc)
        raise HTTPException(502, f"Pexels 图片搜索失败: {exc}")

    items = []
    for p in photos:
        pid = p.get("id")
        if not pid:
            continue
        src = p.get("src") or {}
        items.append({
            "photo_id": pid,
            "photographer": p.get("photographer") or "",
            "alt": p.get("alt") or "",
            "width": p.get("width"), "height": p.get("height"),
            "thumb": src.get("medium"),
            "preview": src.get("large2x") or src.get("large") or src.get("original"),
            "pexels_url": p.get("url"),
        })
    return {"items": items, "query": query}


@router.post("/pexels/import")
def pexels_import(body: PexelsImportRequest, db: Session = Depends(get_db)):
    """勾选入库 — 复用 P线下载链路: 配额闸门 + 厌恶拉黑 + pexels_id 去重 + 异步质检."""
    from app.services.pexels_service import PexelsAuthError, pexels_service
    from app.services.pexels_service._candidates import _widths_sorted
    from app.services.pexels_service._db import (
        get_dislike_pexels_ids,
        get_quota_used_today,
        increment_quota,
        register_video_asset,
        upsert_asset,
    )
    from app.services.pexels_service._http import download
    from app.services.pexels_service.types import PexelsResolveError
    from app.services.pexels_utils import pick_video_file

    try:
        pexels_service._ensure_config()
    except PexelsAuthError as exc:
        raise HTTPException(503, str(exc))

    cfg = get_config().defaults
    materials_dir = cfg.materials_dir
    quota = cfg.pexels_daily_download_quota
    remaining_quota = max(0, quota - get_quota_used_today(db))
    dislike_ids = get_dislike_pexels_ids(db)
    tags_str = ",".join(t.strip().lower() for t in body.query.split() if t.strip())

    imported: list[dict] = []
    skipped: list[dict] = []
    for req_item in body.items:
        video = req_item.video or {}
        pid = video.get("id")
        if not pid:
            continue
        if pid in dislike_ids:
            skipped.append({"pexels_id": pid, "reason": "dislike 拉黑"})
            continue
        existing_va = db.query(VideoAsset).filter(VideoAsset.pexels_id == pid).first()
        if existing_va is not None:
            skipped.append({"pexels_id": pid, "reason": "已在素材库", "asset_no": existing_va.asset_no})
            continue
        if remaining_quota <= 0:
            skipped.append({"pexels_id": pid, "reason": "今日下载配额已用尽"})
            continue
        chosen = pick_video_file(video.get("video_files", []), _widths_sorted(body.prefer_resolution or cfg.pexels_preferred_resolution))
        if chosen is None or not chosen.get("link"):
            skipped.append({"pexels_id": pid, "reason": "无可用视频流"})
            continue
        try:
            local_path = download(pexels_service, chosen["link"], pid, materials_dir)
        except PexelsResolveError as exc:
            logger.warning("[library/pexels] download failed pexels_id=%s: %s", pid, exc)
            skipped.append({"pexels_id": pid, "reason": f"下载失败: {exc}"})
            continue
        if local_path is None:
            skipped.append({"pexels_id": pid, "reason": "下载失败"})
            continue

        from app.models import MaterialAsset
        file_size = Path(local_path).stat().st_size
        increment_quota(db, pid, file_size)
        existing_ma = db.query(MaterialAsset).filter(MaterialAsset.pexels_id == pid).first()
        upsert_asset(db, existing_ma, video, chosen, chosen["link"], local_path, tags_str)
        register_video_asset(db, video, chosen, local_path, tags_str, raw_query=body.query)
        db.commit()
        remaining_quota -= 1
        # 下载即质检 async 模式 (与 P线一致): 后台补打分, 不阻塞页面
        try:
            if getattr(cfg, "pexels_download_quality_gate", True):
                from app.services.asset_quality import enqueue_quality_check
                enqueue_quality_check(local_path)
        except Exception:  # noqa: BLE001
            logger.warning("[library/pexels] quality enqueue failed: %s", local_path)
        va = db.query(VideoAsset).filter(VideoAsset.pexels_id == pid).first()
        imported.append({"pexels_id": pid, "asset_no": va.asset_no if va else None})

    logger.info(
        "[library/pexels] import done: %d imported, %d skipped, quota_remaining=%d",
        len(imported), len(skipped), remaining_quota,
    )
    return {"imported": imported, "skipped": skipped, "quota_remaining": remaining_quota}
