# -*- coding: utf-8 -*-
"""存量素材关键词出清 (2026-09-01 用户令): 已标注的黑白/国旗类按关键词先删一批.

背景: V20260901-4604 类 (kw 已标 black and white photo) 可直接词表匹配踢出,
不必等 VLM 复判; 4608 类 (llama 没认出国旗, 标成 solid background) 词表搜不到,
由 is_real_footage 重生/VLM 复判兜底。
词表宁错杀 (用户口径); 文件回收站, DB 行按精确 id 删。

用法: python scripts/purge_by_keywords.py [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

from send2trash import send2trash

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402

set_config(load_config())
init_db("sqlite:///data/pipeline.db")

# 黑白/历史档案 (en 词匹配 description_en/tags, zh 词匹配 description_zh)
BW_EN = ("black and white", "b&w", "b/w", "monochrome", "sepia", "archival",
         "vintage", "historical footage", "old photo", "grayscale")
BW_ZH = ("黑白", "老照片", "老胶片", "历史影像", "档案", "旧新闻", "默片")
# 国旗词表仅统计不删 (2026-09-01 dry-run 实证): "背景有国旗的政要演讲实拍"会被
# 整批误杀 (冯德莱恩/澳议会/美日会晤样本) — "大面积国旗为主体"与"背景带旗"
# 只有 VLM 能区分, 交给 is_real_footage 复判 (recheck_real_footage.py)。
FLAG_EN = ("flag", "national flag", "banner")
FLAG_ZH = ("国旗", "旗帜", "国徽")
# 地图 (2026-09-01 用户令全删, 存量 18%): 强词直接删; terrain/satellite 等弱词
# 不进 (滑雪地形/卫星锅/火箭发射实拍会误杀)。
MAP_EN = ("map", "atlas", "topographic", "geography")
MAP_ZH = ("地图", "地形图", "卫星图", "国界", "行政区划")


def _hit(a: VideoAsset) -> str | None:
    kw = ((a.description_en or "") + " " + " ".join(a.tags or [])).lower()
    desc = a.description_zh or ""
    for w in BW_EN:
        if w in kw:
            return f"bw:{w}"
    for w in BW_ZH:
        if w in desc:
            return f"bw:{w}"
    for w in FLAG_EN:
        if w in kw:
            return f"flag:{w}"
    for w in FLAG_ZH:
        if w in desc:
            return f"flag:{w}"
    for w in MAP_EN:
        if w in kw:
            return f"map:{w}"
    for w in MAP_ZH:
        if w in desc:
            return f"map:{w}"
    return None


def main() -> None:
    dry = "--dry-run" in sys.argv
    db = get_session_maker()()
    rows = db.query(VideoAsset).filter(VideoAsset.source == "youtube").all()
    bw_kicks, flag_only = [], []
    for a in rows:
        why = _hit(a)
        if why is None:
            continue
        # flag:* 只统计不删 (留给 VLM); bw:*/map:* 直接删
        (flag_only if why.startswith("flag:") else bw_kicks).append((a, why))
    n_map = sum(1 for _, w in bw_kicks if w.startswith("map:"))
    print(f"youtube rows: {len(rows)} | 删除批(bw+map): {len(bw_kicks)} (其中 map {n_map}) | "
          f"仅旗帜(不删, 留给VLM): {len(flag_only)}"
          f"{' (dry-run)' if dry else ''}", flush=True)
    for a, why in bw_kicks[:15]:
        print(f"  {a.asset_no} [{why}] {a.description_zh[:24]}", flush=True)
    if len(bw_kicks) > 15:
        print(f"  ... 共 {len(bw_kicks)} 条", flush=True)
    if not dry:
        for a, _ in bw_kicks:
            if a.file_path and Path(a.file_path).exists():
                try:
                    send2trash(a.file_path)
                except Exception:
                    pass
            db.delete(a)
        db.commit()
        print(f"已踢出 {len(bw_kicks)} 行 (文件回收站)", flush=True)
    db.close()


if __name__ == "__main__":
    main()
