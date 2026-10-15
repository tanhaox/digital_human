"""Video library scan service — materials_dir 扫描与 VideoAsset 入库逻辑.

从 `app/routers/library.py::scan_materials` 下沉. DB session 由调用方传入.
SQLAlchemy/模型 延迟导入, 避免顶层循环依赖.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}


def extract_video_meta(meta: dict[str, Any]) -> tuple[int, int, float]:
    """从 ffprobe_metadata 提取 width/height/duration (0/0/0.0 兜底)."""
    width = 0
    height = 0
    duration = 0.0
    if meta.get("available") and meta.get("streams"):
        for s in meta["streams"]:
            if s.get("codec_type") == "video":
                width = s.get("width", 0) or 0
                height = s.get("height", 0) or 0
                dur_str = s.get("duration")
                if dur_str is not None:
                    try:
                        duration = float(dur_str)
                    except (ValueError, TypeError):
                        pass
                break
    if not duration and meta.get("format", {}).get("duration"):
        try:
            duration = float(meta["format"]["duration"])
        except (ValueError, TypeError):
            pass
    return width, height, duration


def _existing_paths(db: Any, materials_dir: Path) -> set[str]:
    """收集已入库 file_path, 统一归一化为正斜杠 (Windows 反斜杠 vs / 去重失效)."""
    from app.models import VideoAsset

    prefix = materials_dir.resolve().as_posix()
    found: set[str] = set()
    for (fp,) in db.query(VideoAsset.file_path).all():
        normalized = fp.replace("\\", "/")
        if normalized.startswith(prefix):
            found.add(normalized)
    return found


def _import_file(db: Any, entry: Path, abs_path: str) -> dict[str, Any] | None:
    """为单个新文件创建 VideoAsset. 失败返回 None (仅日志, 不中断扫描)."""
    from app.models import VideoAsset
    from app.services.asset_tagging import generate_asset_no
    from app.services.video_validator import ffprobe_metadata

    try:
        asset_no = generate_asset_no(db, "V")
        meta = ffprobe_metadata(entry)
        width, height, duration = extract_video_meta(meta)
        orientation = "portrait" if (height > width) else "landscape"
        asset = VideoAsset(
            id=uuid.uuid4().hex,
            asset_no=asset_no,
            source="local",
            file_path=abs_path,
            orientation=orientation,
            width=width,
            height=height,
            duration_sec=duration,
            source_type="footage",
            location="foreign",
            people="none",
        )
        db.add(asset)
        db.flush()  # 让 generate_asset_no 的下次调用能看到最新记录
        return {
            "id": asset.id,
            "asset_no": asset_no,
            "file_path": abs_path,
            "file_name": entry.name,
            "orientation": orientation,
            "width": width,
            "height": height,
            "duration_sec": round(duration, 2),
        }
    except Exception as exc:
        logger.warning("scan: failed to import %s: %s", entry.name, exc)
        return None


def scan_materials_dir(db: Any, materials_dir: Path) -> dict[str, Any]:
    """扫描 materials_dir 下未入库的视频文件, 创建 VideoAsset 记录.

    被 `app/routers/library.py::scan_materials` 调用.
    """
    existing = _existing_paths(db, materials_dir)
    scanned = 0
    imported = 0
    skipped = 0
    items: list[dict[str, Any]] = []

    for entry in sorted(materials_dir.rglob("*")):
        if not entry.is_file() or entry.suffix.lower() not in VIDEO_EXTS:
            continue
        scanned += 1
        abs_path = entry.resolve().as_posix()
        if abs_path in existing:
            skipped += 1
            continue
        item = _import_file(db, entry, abs_path)
        if item is not None:
            imported += 1
            items.append(item)

    return {"scanned": scanned, "imported": imported, "skipped": skipped, "items": items}


def _merge_tags(existing: list[str], added: list[str]) -> list[str]:
    """合并去重, 保持既有顺序在前、新增在后."""
    if not added:
        return existing or []
    merged = list(existing or [])
    for t in added:
        t = t.strip()
        if t and t not in merged:
            merged.append(t)
    return merged


def scan_materials_subdir(
    db: Any,
    materials_dir: Path,
    folder: str,
    scenes: list[str] | None = None,
    shot_types: list[str] | None = None,
) -> dict[str, Any]:
    """扫描 materials_dir 下**指定子文件夹**, 导入未入库视频并批量附加标签.

    与 scan_materials_dir 的关系:
    - 目标 = folder 解析出的路径 (相对 materials_dir, 或绝对路径).
    - 仅对本次**新导入**的素材写 scenes / shot_types (合并去重, 不写旧 tags 字段).
    - 已入库素材被跳过 (skipped), 不追加标签 — 与「唯一要求: 给该文件夹下所有视频
      批量加标签」的语义一致: 素材已入库说明此前已有人工处理, 不重复污染.

    返回 {"folder", "scanned", "imported", "skipped", "labeled", "items"}.
    """
    base = materials_dir.resolve()
    rel = Path(folder)
    if rel.is_absolute():
        target = rel
    else:
        target = base / rel
    target = target.resolve()
    # 防越界: 绝对路径必须落在 materials_dir 之内 (含等于根目录)
    if not (target == base or target.is_relative_to(base)):
        raise ValueError(f"folder 超出 materials_dir 范围: {folder}")

    if not target.is_dir():
        raise ValueError(f"文件夹不存在: {target}")

    existing = _existing_paths(db, base)
    scanned = 0
    imported = 0
    skipped = 0
    items: list[dict[str, Any]] = []

    for entry in sorted(target.rglob("*")):
        if not entry.is_file() or entry.suffix.lower() not in VIDEO_EXTS:
            continue
        scanned += 1
        abs_path = entry.resolve().as_posix()
        if abs_path in existing:
            skipped += 1
            continue
        item = _import_file(db, entry, abs_path)
        if item is None:
            continue
        # 仅新导入素材打标
        if scenes or shot_types:
            from app.models import VideoAsset

            asset = db.get(VideoAsset, item["id"])
            if asset is not None:
                asset.scenes = _merge_tags(asset.scenes, list(scenes or []))
                asset.shot_types = _merge_tags(asset.shot_types, list(shot_types or []))
                db.flush()
        imported += 1
        items.append(item)

    return {
        "folder": folder,
        "scanned": scanned,
        "imported": imported,
        "skipped": skipped,
        "labeled": imported if (scenes or shot_types) else 0,
        "items": items,
    }
