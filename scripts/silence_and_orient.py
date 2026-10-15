# -*- coding: utf-8 -*-
"""素材库统一消音 + 横竖屏实测重标 (2026-09-01 用户令).

① 所有视频去音轨: ffmpeg -c copy -an remux (临时文件→os.replace, 不重编码秒级)
② orientation/width/height 按 ffprobe 实测刷新 (修错标: 横屏打成竖屏)
跳过: 文件缺失 / 已无音轨 / webm 容器 (remux mkv 兼容; 后缀保真处理)

用法: python -u scripts/silence_and_orient.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

FFPROBE = r"C:\Programs\ffmpeg\bin\ffprobe.exe"
FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402

set_config(load_config())
init_db("sqlite:///data/pipeline.db")


def probe(path: str) -> dict | None:
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    try:
        d = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return None
    vids = [s for s in d.get("streams", []) if s.get("codec_type") == "video"]
    if not vids:
        return None
    v = vids[0]
    return {"w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
            "has_audio": any(s.get("codec_type") == "audio"
                             for s in d.get("streams", []))}


def strip_audio(path: Path) -> bool:
    """-c copy -an remux → 原子替换. 失败返回 False (原文件不动)."""
    fd, tmp = tempfile.mkstemp(suffix=path.suffix, dir=str(path.parent))
    os.close(fd)
    try:
        r = subprocess.run(
            [FF, "-y", "-v", "error", "-i", str(path),
             "-c", "copy", "-an", "-movflags", "+faststart", tmp],
            capture_output=True, timeout=120)
        if r.returncode != 0 or Path(tmp).stat().st_size == 0:
            return False
        os.replace(tmp, str(path))
        return True
    except Exception:
        return False
    finally:
        Path(tmp).unlink(missing_ok=True)


def main() -> None:
    dry = "--dry-run" in sys.argv
    db = get_session_maker()()
    rows = db.query(VideoAsset).all()
    print(f"总行 {len(rows)}{' (dry-run)' if dry else ''}", flush=True)
    n_muted = n_orient = n_miss = n_fail = 0
    t0 = time.time()
    for i, a in enumerate(rows, 1):
        p = Path(a.file_path) if a.file_path else None
        if not p or not p.exists():
            n_miss += 1
            continue
        info = probe(str(p))
        if not info or not info["w"] or not info["h"]:
            n_fail += 1
            continue
        orient = "landscape" if info["w"] >= info["h"] else "portrait"
        changed = False
        if a.orientation != orient or a.width != info["w"] or a.height != info["h"]:
            if not dry:
                a.orientation, a.width, a.height = orient, info["w"], info["h"]
            changed = True
            n_orient += 1
        if info["has_audio"]:
            if not dry:
                if strip_audio(p):
                    n_muted += 1
                else:
                    n_fail += 1
                    print(f"  消音失败: {a.asset_no} {p.name}", flush=True)
            else:
                n_muted += 1  # dry: 统计待消音量
        if changed and not dry:
            db.commit()
        if i % 200 == 0:
            print(f"  [{i}/{len(rows)}] 消音 {n_muted} / 重标 {n_orient} "
                  f"({time.time()-t0:.0f}s)", flush=True)
    if not dry:
        db.commit()
    db.close()
    print(f"完成: 消音 {n_muted} | 横竖屏/宽高重标 {n_orient} | 缺文件 {n_miss} | 失败 {n_fail}",
          flush=True)


if __name__ == "__main__":
    main()
