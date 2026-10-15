# -*- coding: utf-8 -*-
"""终修: 恢复尾部资料页引用卡 + 查 slot0/1 + 导出."""
import json
import sqlite3
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

con = sqlite3.connect("data/pipeline.db")
# slot 0/1 现状
for idx in (0, 1, 27):
    r = con.execute(
        "select workflow, status, params_json from director_slots "
        "where director_job_id like '5f4a5c7d%' and slot_index=? and status != 'replaced'",
        (idx,)).fetchone()
    if r:
        p = json.loads(r[2]) if r[2] else {}
        print(f"slot {idx}: [{r[0]}] {r[1]} | ent={p.get('entities')} | {str(p.get('local_file'))[-40:]}")

# 恢复 slot 27 为资料页引用卡 (workflow 被之前的人物提升永久改写, 重建 render_config)
from app.database import init_db, get_session_maker
from app.models import DirectorJob, Script
init_db("sqlite:///data/pipeline.db")
with get_session_maker()() as db:
    job = db.query(DirectorJob).filter(DirectorJob.id.like("5f4a5c7d%")).first()
    script = job.script
    from app.services.director_service._postprocess import _collect_news_sources
    sources = _collect_news_sources(db, script) or [{"media": "公开报道", "title": "内容综合自公开报道"}]
    subtitle = "  ·  ".join(
        f"{s['media']}·{s['title']}" if s["title"] else s["media"] for s in sources)[:200]
    for s in job.slots:
        if s.slot_index == 27 and s.status != "replaced":
            s.workflow = "hf_title"
            s.visual_type = "hf_title"
            s.status = "queued"
            s.output_path = None
            params = dict(s.params_json or {})
            params["render_config"] = {"title": "内容来源声明", "subtitle": subtitle,
                                        "style": "references", "sources": sources}
            params.pop("entities", None)
            s.params_json = params
    db.commit()
print("slot 27 → 资料页引用卡已恢复")
