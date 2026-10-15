# -*- coding: utf-8 -*-
"""人检关口 — qc_sheet.html 接触表 (缩略图 + QC要点 + 状态 + 审批命令速查).

用法: k2 批跑完 → qc 生成 → 浏览器逐镜检查(文字/穿模/构图) → approve 命令回填.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any

from . import shots as shots_mod

logger = logging.getLogger(__name__)

_BADGE = {
    "planned": ("⏳ 待生图", "#888"),
    "img_done": ("🔍 待人检", "#c80"),
    "approved": ("✅ 已批", "#2a0"),
    "anim_done": ("🎬 动画成", "#08c"),
    "anim_fail": ("⚠️ 动画败", "#c33"),
}


def _card(shot: dict[str, Any]) -> str:
    badge, color = _BADGE.get(shot["status"], (shot["status"], "#888"))
    media = ""
    if shot.get("video_file"):
        # 有视频: 视频为主, 图缩略折叠
        media = (
            f'<video src="{shot["video_file"]}" controls muted preload="metadata"></video>'
            f'<a class="thumb" href="{shot["image_file"]}" target="_blank" title="点开首帧原图">'
            f'<img src="{shot["image_file"]}" loading="lazy" alt="首帧"><span>首帧</span></a>'
        )
    elif shot.get("image_file"):
        media = f'<a href="{shot["image_file"]}" target="_blank" title="点击新标签页看原图"><img src="{shot["image_file"]}" loading="lazy" alt="{shot["shot_id"]}"></a>'
    else:
        media = '<div class="noimg">未生图</div>'
    beats = "".join(
        f'<li><b>{b["t_start"]}-{b["t_end"]}s</b> {html.escape(str(b["motion"])[:160])}</li>'
        for b in shot["anim"].get("beats") or []
    )
    texts = "".join(
        f'<li>{html.escape(str(t.get("kind", "")))} [{t.get("t_start", "")}-{t.get("t_end", "")}s] {html.escape(str(t.get("text", "")))}</li>'
        for t in shot.get("text_layer") or []
    )
    note = html.escape(shot.get("reject_note") or "")
    note_html = f'<div class="reject">打回理由: {note}</div>' if note else ""
    err = html.escape(shot.get("error") or "")
    err_html = f'<div class="err">错误: {err}</div>' if err else ""
    # 0927 P1-B 视频机检标记 (观察模式): 乱码/崩坏/首帧漂移交人检
    vsus = shot.get("video_suspect") or {}
    vsus_html = (f'<div class="reject">🎥 视频机检: '
                 f'{html.escape(str(vsus.get("kinds")))} '
                 f'{html.escape(str(vsus.get("what", ""))[:120])}</div>'
                 if vsus else "")
    return f"""<div class="card pt{shot['page_type']}">
  <div class="head"><b>{shot['shot_id']}</b> <span class="pt">{shot['page_type']}</span>
    <span class="label">{html.escape(shot.get("label", ""))}</span>
    <span class="badge" style="background:{color}">{badge}</span></div>
  {media}
  <div class="narration">口播: {html.escape(shot.get("narration", ""))}</div>
  <div class="qc">QC: {html.escape(shot.get("qc_notes", ""))}</div>
  <ol class="beats">{beats}</ol>
  <ul class="texts">{texts}</ul>
  {note_html}{err_html}{vsus_html}
</div>"""


def render(doc: dict[str, Any]) -> Path:
    out = shots_mod.ep_dir(doc["book_title"], doc["ep"]) / "qc_sheet.html"
    cards = "\n".join(_card(s) for s in doc["shots"])
    book = html.escape(doc["book_title"])
    title = html.escape(doc.get("ep_title", ""))
    cheat = (
        f'python -m app.services.anim_pipeline.run approve --book "{book}" --ep {doc["ep"]} '
        '--ok s01,s02 --redo s03 --note "穿模"'
    )
    html_doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>QC {book} ep{doc['ep']}</title><style>
body{{margin:0;padding:16px;background:#f5f4f0;font:14px/1.5 system-ui,"Microsoft YaHei"}}
h1{{font-size:18px}} .sub{{color:#666;margin-bottom:12px}}
.cheat{{background:#fffbe6;border:1px solid #e0d8a8;padding:8px 12px;border-radius:6px;
font-family:Consolas,monospace;font-size:12px;margin-bottom:16px;word-break:break-all}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}
.card{{background:#fff;border-radius:8px;padding:12px;box-shadow:0 1px 3px rgba(0,0,0,.12)}}
.card img,.card video,.noimg{{width:100%;aspect-ratio:16/9;object-fit:contain;
background:#111;border-radius:4px;margin:6px 0;display:block}}
.thumb{{display:inline-flex;align-items:center;gap:6px;margin:0 0 6px;
text-decoration:none;color:#555;font-size:12px}}
.thumb img{{width:150px;aspect-ratio:16/9;margin:0;border:1px solid #ddd}}
.noimg{{display:flex;align-items:center;justify-content:center;color:#555}}
.head{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
.pt{{font-weight:700;color:#fff;background:#345;padding:0 6px;border-radius:4px}}
.card.ptC .pt{{background:#a33}} .label{{color:#666;font-size:12px}}
.badge{{color:#fff;font-size:12px;padding:1px 8px;border-radius:10px}}
.narration{{font-size:14px;color:#222;margin-top:6px}} .qc{{font-size:12px;color:#a33;margin-top:4px}}
.beats,.texts{{font-size:12px;color:#555;margin:6px 0 0 18px;padding:0}}
.reject{{font-size:12px;color:#c33;margin-top:4px}}
.err{{font-size:12px;color:#c33;margin-top:4px;word-break:break-all}}
</style></head><body>
<h1>{book} · 第{doc['ep']}集 · {title}</h1>
<div class="sub">{shots_mod.summary(doc)} · 策略: {html.escape(doc.get("ep_summary", ""))}</div>
<div class="cheat">审批命令: {html.escape(cheat)}</div>
<div class="grid">{cards}</div>
</body></html>"""
    out.write_text(html_doc, encoding="utf-8")
    logger.info("[qc] 接触表 → %s", out)
    return out
