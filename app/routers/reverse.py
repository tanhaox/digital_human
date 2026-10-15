# -*- coding: utf-8 -*-
"""图片反推 router (2026-09-06) — 引擎提取 + 提取库 CRUD.

链路: 前端选中图 (Pexels 在线 / 本机 / 粘贴) → POST /extract
→ 图字节落盘 data/reverse_library/ → GLM-4V-Flash 结构化提取
→ 字段回填前端 + 记录入库 (图/类型/重点/项目标签/字段 JSON)。
库查询按 target/project 过滤; 删除走回收站红线 (file_utils.recycle_file)。
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ImageExtraction
from app.services.file_utils import recycle_file
from app.services.reverse_prompt_service import (
    FOCUS_FIELDS,
    MODES,
    TARGET_FIELDS,
    ReverseExtractError,
    adapt_prompt,
    extract_fields,
    extract_flat,
    generate_image,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reverse", tags=["reverse"])

# 图落盘根: <project>/data/reverse_library/ (与既有 data/ 目录同居)
_LIBRARY_DIR = Path(__file__).absolute().parents[2] / "data" / "reverse_library"


class ExtractRequest(BaseModel):
    """一次提取: 图 (data_url 或远程 url 二选一) + 目标 + 重点 + 项目标签."""
    data_url: str | None = Field(default=None, max_length=30_000_000)   # 本机/粘贴图
    url: str | None = Field(default=None, max_length=2048)              # Pexels CDN 图
    filename: str | None = Field(default=None, max_length=256)
    source_type: str = Field(default="local", pattern=r"^(pexels|local|paste)$")
    source_url: str | None = Field(default=None, max_length=2048)       # Pexels 页面 URL
    target: str = Field(..., pattern=r"^(person|prop|scene)$")          # 入库分类
    mode: str = Field(default="anchors", pattern=r"^(anchors|pixel|tags)$")
    focus: list[str] = Field(default_factory=list)                      # 空 = 全部提取 (仅 anchors)
    project: str = Field(default="", max_length=128)


class GenerateRequest(BaseModel):
    """工坊闭环生图 (2026-09-07): 提示词 → CogView 直出 → 入库 (提示词↔图绑定)."""
    prompt: str = Field(..., min_length=1, max_length=4000)
    size: str = Field(default="1024x1024",
                      pattern=r"^(1024x1024|768x1344|864x1152|1344x768|1152x864|1440x720|720x1440)$")
    target: str = Field(default="person", pattern=r"^(person|prop|scene)$")  # 入库分类
    project: str = Field(default="", max_length=128)


class AdaptRequest(BaseModel):
    """提示词按目标模型方言改写 (0907: 词是方言 — CogView 词直进可灵 K2 必变样)."""
    prompt: str = Field(..., min_length=1, max_length=4000)
    target: str = Field(..., pattern=r"^(kling_video|kling_image|jimeng)$")


class ExtractPatch(BaseModel):
    """库记录修正: 项目标签 / 校对后的字段."""
    project: str | None = Field(default=None, max_length=128)
    values: dict[str, str] | None = None


def _sniff_ext(data: bytes, url: str) -> str:
    """magic bytes 优先, 其次 URL 后缀, 兜底 .jpg."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"GIF8":
        return ".gif"
    return Path(url.split("?")[0]).suffix.lower() or ".jpg"


def _fetch_bytes(body: ExtractRequest) -> tuple[bytes, str]:
    """取图字节: data_url 解码 / 远程 url 下载."""
    if body.data_url:
        try:
            head, b64 = body.data_url.split(",", 1)
            data = base64.b64decode(b64)
            mime = head.split(":", 1)[1].split(";", 1)[0] if ":" in head else "image/jpeg"
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(400, f"data_url 解析失败: {exc}") from exc
        if not mime.startswith("image/"):
            raise HTTPException(400, f"data_url 不是图片: {mime}")
        return data, mime
    if body.url:
        try:
            resp = requests.get(body.url, timeout=(10, 60))
        except requests.exceptions.RequestException as exc:
            raise HTTPException(502, f"参考图下载失败: {exc}") from exc
        if resp.status_code != 200:
            raise HTTPException(502, f"参考图下载失败 HTTP {resp.status_code}")
        mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
        if not mime.startswith("image/"):
            raise HTTPException(502, f"参考图不是图片: {mime}")
        return resp.content, mime
    raise HTTPException(400, "data_url 与 url 至少提供一项")


@router.post("/extract")
def extract(body: ExtractRequest, db: Session = Depends(get_db)):
    """反推提取 — 图落盘 + 引擎提取 + 入库, 返回字段供前端回填."""
    # focus key 校验 (非法 key 直接 400, 不静默) — 仅 anchors 模式生效
    valid_focus = set(FOCUS_FIELDS[body.target])
    bad = [k for k in body.focus if k not in valid_focus]
    if bad:
        raise HTTPException(400, f"非法 focus key: {bad} (可选: {sorted(valid_focus)})")

    data, mime = _fetch_bytes(body)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "图片超过 20MB, 请压缩后再提取")

    ext = _sniff_ext(data, body.url or "")
    _LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = f"{stamp}_{len(data)}{ext}"
    fpath = _LIBRARY_DIR / fname
    fpath.write_bytes(data)

    data_url = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    try:
        if body.mode == "anchors":
            values = extract_fields(data_url, body.target, body.focus)
        else:
            values = {"prompt": extract_flat(data_url, body.mode)}
    except ReverseExtractError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reverse] extract failed: %s", exc)
        raise HTTPException(500, f"反推引擎失败: {exc}")

    rec = ImageExtraction(
        image_path=str(fpath.resolve()),
        source_type=body.source_type,
        source_url=body.source_url,
        filename=body.filename,
        target_type=body.target,
        focus=",".join(body.focus) if body.focus else ("all" if body.mode == "anchors" else body.mode),
        project=body.project.strip(),
        values_json=json.dumps(values, ensure_ascii=False),
    )
    db.add(rec)
    db.commit()
    logger.info(
        "[reverse] extracted %s focus=%s project=%r -> %s (%d fields filled)",
        body.target, rec.focus, rec.project, rec.id,
        sum(1 for v in values.values() if v),
    )
    return {"record_id": rec.id, "values": values, "created_at": rec.created_at.isoformat()}


def _record_out(rec: ImageExtraction) -> dict:
    try:
        values = json.loads(rec.values_json)
    except ValueError:
        values = {}
    return {
        "id": rec.id,
        "target": rec.target_type,
        "focus": rec.focus,
        "project": rec.project or "",
        "source_type": rec.source_type,
        "source_url": rec.source_url,
        "filename": rec.filename,
        "image_url": f"/api/reverse/records/{rec.id}/image",
        "values": values,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


@router.post("/adapt")
def adapt(body: AdaptRequest):
    """提示词移植 — 按目标模型方言改写 (可灵图生视频版会剥掉外貌描述, 交给首帧图)."""
    prompt = body.prompt.strip()
    # 去掉工坊草稿头 ([图片反推草稿 · xx] 之类), 只留内容
    lines = [ln for ln in prompt.splitlines() if not ln.strip().startswith("[")]
    prompt = "\n".join(lines).strip() or prompt
    try:
        text = adapt_prompt(prompt, body.target)
    except ReverseExtractError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reverse] adapt failed: %s", exc)
        raise HTTPException(500, f"改写失败: {exc}")
    return {"text": text}


@router.post("/generate")
def generate(body: GenerateRequest, db: Session = Depends(get_db)):
    """提示词直接生图 — CogView (cogview-3-flash 免费) 落盘入库.

    生成记录与提取记录同库 (source_type='generated'), values_json 存生成用的
    完整提示词 — 图↔词双向可查, 前端按 source_type 打「生成」标。
    """
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(400, "prompt 不能为空")
    try:
        data = generate_image(prompt, body.size)
    except ReverseExtractError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reverse] generate failed: %s", exc)
        raise HTTPException(500, f"生图失败: {exc}")

    ext = _sniff_ext(data, "")
    _LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fpath = _LIBRARY_DIR / f"gen_{stamp}{ext}"
    fpath.write_bytes(data)

    rec = ImageExtraction(
        image_path=str(fpath.resolve()),
        source_type="generated",
        source_url=None,
        filename="AI生成",
        target_type=body.target,
        focus="gen",
        project=body.project.strip(),
        values_json=json.dumps({"prompt": prompt}, ensure_ascii=False),
    )
    db.add(rec)
    db.commit()
    logger.info("[reverse] generated image %s (%s, %d bytes, project=%r)",
                rec.id, body.size, len(data), rec.project)
    return _record_out(rec)


@router.get("/records")
def list_records(
    target: str | None = None,
    project: str | None = None,   # 空/未传 = 全部; "__none__" = 未分项目
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """提取库列表 — 按类型/项目过滤, 新的在前."""
    q = db.query(ImageExtraction)
    if target:
        if target not in TARGET_FIELDS:
            raise HTTPException(400, f"非法 target: {target}")
        q = q.filter(ImageExtraction.target_type == target)
    if project == "__none__":
        q = q.filter(ImageExtraction.project == "")
    elif project:
        q = q.filter(ImageExtraction.project == project)
    rows = q.order_by(ImageExtraction.created_at.desc()).limit(min(limit, 500)).all()
    projects = sorted({r.project for r in db.query(ImageExtraction.project).distinct().all() if r[0]})
    return {"items": [_record_out(r) for r in rows], "projects": projects}


@router.get("/records/{record_id}/image")
def record_image(record_id: str, db: Session = Depends(get_db)):
    """库记录原图 — 前端缩略图/预览直连."""
    rec = db.get(ImageExtraction, record_id)
    if not rec:
        raise HTTPException(404, "record not found")
    p = Path(rec.image_path)
    if not p.exists():
        raise HTTPException(404, "image file missing")
    media_type = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    return FileResponse(str(p), media_type=media_type)


@router.patch("/records/{record_id}")
def patch_record(record_id: str, body: ExtractPatch, db: Session = Depends(get_db)):
    """库记录修正 — 项目标签重打 / 校对后字段回写."""
    rec = db.get(ImageExtraction, record_id)
    if not rec:
        raise HTTPException(404, "record not found")
    if body.project is not None:
        rec.project = body.project.strip()
    if body.values is not None:
        allowed = set(TARGET_FIELDS[rec.target_type]) | {"prompt"}  # prompt = 整图反推平铺模式
        clean = {k: str(v) for k, v in body.values.items() if k in allowed}
        rec.values_json = json.dumps(clean, ensure_ascii=False)
    db.commit()
    return _record_out(rec)


@router.delete("/records/{record_id}")
def delete_record(record_id: str, db: Session = Depends(get_db)):
    """删除记录 — 图文件走回收站 (红线), 行删除."""
    rec = db.get(ImageExtraction, record_id)
    if not rec:
        raise HTTPException(404, "record not found")
    if not recycle_file(rec.image_path):
        logger.warning("[reverse] 图文件回收站失败, 保留原地: %s", rec.image_path)
    db.delete(rec)
    db.commit()
    return {"status": "ok", "deleted": record_id}
