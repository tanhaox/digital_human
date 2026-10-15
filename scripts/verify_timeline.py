# -*- coding: utf-8 -*-
"""ID-024 快路径时间轴对照验证 — 累计 start/end vs TTS wav 真实时长."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import init_db, get_session_maker
from app.models import AudioJob, Script
from app.services.director_service import _collect_tts_segment_durations

DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
init_db(f"sqlite:///{DB_PATH}")
db = get_session_maker()()

SID = "216e3073-4783-4ed8-8fc3-90a4da7c1588"
script = db.get(Script, SID)
segs = [
    {"id": s.id, "text": s.text}
    for s in sorted(script.segments, key=lambda x: x.line_index)
    if s.selected_for_host
]

res = _collect_tts_segment_durations(db, SID, segs)
job = (
    db.query(AudioJob)
    .filter(AudioJob.script_id == SID, AudioJob.status == "completed")
    .order_by(AudioJob.completed_at.desc())
    .first()
)
dur_by_seg = {}
for af in job.audio_files:
    if af.segment_id is not None and af.duration is not None:
        dur_by_seg[af.segment_id] = round(af.duration, 3)

print("timeline vs TTS actual (first 6):")
current = 0.0
mismatch = 0
for i, seg in enumerate(res):
    end = round(current + seg["duration"], 3)
    actual = dur_by_seg.get(seg["id"])
    ok = actual is not None and abs(end - round(current + actual, 3)) < 0.001
    if not ok:
        mismatch += 1
    if i < 6:
        print(f"  #{i}: start={current:.3f} end={end:.3f} dur={seg['duration']:.3f} tts={actual} {'OK' if ok else 'MISMATCH'}")
    current = end

total = round(sum(s["duration"] for s in res), 3)
print("total:", total, "| mismatch:", mismatch)
print("RESULT:", "PASS" if mismatch == 0 and abs(total - 458.31) < 0.01 else "FAIL")
db.close()
