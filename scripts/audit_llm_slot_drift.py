# -*- coding: utf-8 -*-
"""ID-024A 审计 — 量化 LLM 单次长响应回填字段相对脚本原文/真实 TTS 时间轴的漂移.

对比对象:
  - DB 里已落库的 DirectorSlot (LLM 回填的 text_context / segment_id / start/end)
  - 真值:
      text    = script.segments (selected_for_host, 按 line_index) 的原文
      time    = 最新 completed AudioJob 的 audio_files duration 累加出的逐段起止
指标:
  - text 漂移: 归一化后与段原文是否完全一致; 不一时打印前 8 个样本供人肉判断
  - time 漂移: |slot.start - 真值start| + |slot.end - 真值end| < 0.3s 记为命中
  - unbound  : slot.segment_id 不在真值表 (None / 指向无效段)
"""
import difflib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import get_session_maker, init_db
from app.models import AudioJob, DirectorJob

DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
init_db(f"sqlite:///{DB_PATH}")
db = get_session_maker()()

# 控制台 UTF-8,避免 Windows GBK 中文崩
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def norm(t):
    return "".join((t or "").split())


jobs = (
    db.query(DirectorJob)
    .filter(DirectorJob.status.in_(["reviewing", "completed", "failed"]))
    .order_by(DirectorJob.created_at.desc())
    .all()
)
print("director jobs (reviewing/completed/failed):", len(jobs))

total_slots = 0
text_exact = 0
text_near = 0          # ratio >= 0.9
text_mismatch = 0      # ratio < 0.7
time_hit = 0
time_miss = 0
unbound = 0
ref_cards = 0
empty_text = 0
jobs_with_slots = 0
examples: list[tuple] = []

for job in jobs:
    script = job.script
    if script is None:
        continue
    segs = sorted(
        (s for s in script.segments if s.selected_for_host),
        key=lambda s: s.line_index,
    )
    ajob = (
        db.query(AudioJob)
        .filter(AudioJob.script_id == script.id, AudioJob.status == "completed")
        .order_by(AudioJob.completed_at.desc())
        .first()
    )
    dur_by_seg = {}
    if ajob is not None:
        for af in ajob.audio_files:
            if af.segment_id is not None and af.duration is not None:
                dur_by_seg[af.segment_id] = float(af.duration)

    truth = {}
    cur = 0.0
    for s in segs:
        d = dur_by_seg.get(s.id)
        truth[s.id] = {"text": norm(s.text), "start": cur, "end": cur + d if d is not None else None}
        if d is not None:
            cur += d
    has_dur = bool(dur_by_seg)

    slots = list(job.slots)
    if not slots:
        continue
    jobs_with_slots += 1

    for slot in slots:
        pj = slot.params_json or {}
        if pj.get("no_voiceover"):
            ref_cards += 1
            continue
        total_slots += 1
        tc = norm(slot.text_context)
        if not tc:
            empty_text += 1
        sid = slot.segment_id
        if sid not in truth:
            unbound += 1
            continue
        t = truth[sid]
        if tc and t["text"]:
            if tc == t["text"]:
                text_exact += 1
            else:
                r = difflib.SequenceMatcher(None, tc, t["text"]).ratio()
                if r >= 0.9:
                    text_near += 1
                elif r < 0.7:
                    text_mismatch += 1
                    if len(examples) < 8:
                        examples.append((slot.slot_index, tc[:60], t["text"][:60], round(r, 3)))
        else:
            text_mismatch += 1
        if t["end"] is not None:
            dt = abs(slot.start_sec - t["start"]) + abs(slot.end_sec - t["end"])
            if dt < 0.3:
                time_hit += 1
            else:
                time_miss += 1

print("jobs with slots:", jobs_with_slots)
print("has TTS durations for truth:", has_dur)
print("total slots (excl ref cards):", total_slots)
print("  ref cards skipped:", ref_cards)
print("  slots w/ empty text_context:", empty_text)
print("  unbound segment_id (None/invalid):", unbound)
print("--- text drift (of bound slots with text) ---")
print("  exact match:", text_exact, "| near(>=0.9):", text_near, "| mismatch(<0.7):", text_mismatch)
print("--- time drift (|start/end diff| < 0.3s) ---")
print("  time hit:", time_hit, "| time miss:", time_miss)

if examples:
    print("--- text mismatch samples (slot_idx | llm_text | script_text | ratio) ---")
    for si, lt, st, r in examples:
        print(f"  #{si} | {lt!r} | {st!r} | {r}")

db.close()
