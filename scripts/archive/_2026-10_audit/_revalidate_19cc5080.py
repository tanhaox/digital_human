# -*- coding: utf-8 -*-
"""一次性重导出验证 (2026-09-03): job 19cc5080 复用已有 layers 截图,
以新时序/尾页书卡/logo 台标重建元素级草稿. 不重跑 TTS/截图/产线.

用法: python scripts/_revalidate_19cc5080.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config import load_config, set_config

set_config(load_config())

import json as _json

from app.database import db_session
from app.services.ppt_service import parse_pptx
from app.services.jy_draft_service import compute_page_timing, export_element_draft
from app.services.jy_draft_service.watermark import prepare_watermark
from app.services.ppt_pipeline import _render_book_card

JOB = "19cc5080-be09-4f2b-818d-f8fcc0a4088a"
BOOK = "7470b3db-2479-47a0-89e0-0a705069a67a"  # 蛤蟆先生 (audio 缓存来源 job 同书)
WORK = Path("E:/数字人计划/ppt") / JOB
AUDIO_MF = json.loads((Path("E:/数字人计划/ppt") / "cb3373ba-c8fd-4f7b-bd38-7cc77f16952e" / "audio" / "manifest.json").read_text(encoding="utf-8"))

slides = parse_pptx(WORK / "source.pptx")
durs = [float(s.get("duration") or 0) for s in AUDIO_MF["segments"]]
wavs = [str(Path("E:/数字人计划/ppt") / "cb3373ba-c8fd-4f7b-bd38-7cc77f16952e" / "audio" / s["file"])
        for s in AUDIO_MF["segments"]]
assert len(slides) == len(durs), (len(slides), len(durs))

# 尾页书卡 (真实渲染验证; 已渲染过则直接复用)
book_card = None
from app.database import init_db
_cfg = load_config()
set_config(_cfg)
init_db(_cfg.app.database_url)
if (WORK / "book_card.png").exists():
    book_card = WORK / "book_card.png"
    print("book_card: (cached)", book_card)
else:
    with db_session() as db:
        book_card = _render_book_card(db, BOOK, WORK, durs[-1])
    print("book_card:", book_card)

pages = []
cum = 0.0
for i, s in enumerate(slides):
    pd = WORK / "layers" / f"p{s.index:02d}"
    start, dur = cum, durs[i]
    cum += dur
    if i == len(slides) - 1 and book_card:
        pages.append({"start_sec": round(start, 3), "duration_sec": round(dur, 3),
                      "audio_file": wavs[i], "narration": s.notes or "",
                      "layers": [{"kind": "base", "file": str(book_card)}]})
        continue
    layers = [{"kind": "base", "file": str(pd / "base.png")}]
    for ti, tb in enumerate(s.text_blocks):
        f = pd / f"t{ti}.png"
        if f.exists():
            layers.append({"kind": "text", "file": str(f), "text": tb.text,
                           "order": tb.shape_id, "left": tb.left, "top": tb.top,
                           "pt": tb.font_size_pt, "bold": tb.bold})
    for ii, ib in enumerate(s.image_blocks):
        f = pd / f"im{ii}.png"
        if f.exists():
            layers.append({"kind": "image", "file": str(f), "order": 100 + ib.shape_id,
                           "left": ib.left, "top": ib.top})
    timed = compute_page_timing(layers, s.notes or "", start, dur)
    base_l = [l for l in layers if l["kind"] == "base"]
    pages.append({"start_sec": round(start, 3), "duration_sec": round(dur, 3),
                  "audio_file": wavs[i], "narration": s.notes or "",
                  "layers": base_l + timed})

# 目录页 (p02) 新时序快照
p2 = [l for l in pages[1]["layers"] if l["kind"] != "base"]
mx = max(l["start_sec"] for l in p2) - pages[1]["start_sec"]
print(f"目录页最晚入场 (页内相对): {mx:.2f}s  (旧 ~58s)")

logo = prepare_watermark()
try:
    from app.services.book_service.distiller import load_book_rules
    disclaimer = load_book_rules().get("opening_disclaimer") or ""
except Exception:
    disclaimer = ""

draft = export_element_draft(
    "PPT_revalidate_0903", pages, canvas=(1920, 1080),
    disclaimer=disclaimer, book_title="蛤蟆先生去看心理医生", ep_index=1,
    watermark=logo)
print(json.dumps(draft, ensure_ascii=False, indent=1))
