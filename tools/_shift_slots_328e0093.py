# -*- coding: utf-8 -*-
"""328e0093 slot 时间轴平移 — 音频 bleed 修复后行时长变化, 导出轨 (消费
slot.start_sec) 需按 旧/新行累计曲线 线性映射重排。画面规划 (visual_type/
workflow/params) 不动, 只动时间。执行前备份 director_slots 到 data/。
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")

JOB_ID = "203c44ce-e961-4d4c-8e39-163a5424ffdd"
SCRIPT_ID = "328e0093-d28b-4946-99f1-6a7329135834"
PROJ = Path(r"F:/AI-Agent-Local/digital_human/projects/20260907_012434_135834/audio")

con = sqlite3.connect(r"F:/AI-Agent-Local/digital_human/data/pipeline.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

# ── 备份 ──
rows = [dict(r) for r in cur.execute(
    "SELECT * FROM director_slots WHERE director_job_id=?", (JOB_ID,)).fetchall()]
Path(r"F:/AI-Agent-Local/digital_human/data/_slots_backup_328e0093.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"备份 {len(rows)} slots -> data/_slots_backup_328e0093.json")

# ── 旧行时长 (Segment.estimated_duration, 产线生成时写入, 离线修复未触碰) ──
segs = [dict(r) for r in cur.execute(
    "SELECT line_index, estimated_duration, text FROM segments "
    "WHERE script_id=? AND selected_for_host=1 ORDER BY line_index", (SCRIPT_ID,)).fetchall()]
# ── 新行时长 (manifest, 修复后回写) ──
manifest = json.loads((PROJ / "manifest.json").read_text(encoding="utf-8"))
m_segs = sorted(manifest["segments"], key=lambda s: int(s["index"]))
assert len(segs) == len(m_segs), f"行数不齐: segs={len(segs)} manifest={len(m_segs)}"

old_d = [s["estimated_duration"] or m["duration"] or 0.0
         for s, m in zip(segs, m_segs)]
new_d = [m["duration"] or 0.0 for m in m_segs]
old_total, new_total = sum(old_d), sum(new_d)
print(f"旧总长 {old_total:.2f}s -> 新总长 {new_total:.2f}s (Δ {new_total-old_total:+.2f}s)")

# 累计曲线 (边界点: old_cum[i] = 前 i 行总长)
old_cum = [0.0]
for d in old_d:
    old_cum.append(old_cum[-1] + d)
new_cum = [0.0]
for d in new_d:
    new_cum.append(new_cum[-1] + d)


def map_t(t: float) -> float:
    """旧时刻 → 新时刻 (行内线性, 行边界精确对齐)。"""
    if t <= 0:
        return 0.0
    if t >= old_total:
        return new_total
    # 定位行 i: old_cum[i] <= t < old_cum[i+1]
    lo, hi = 0, len(old_cum) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if old_cum[mid] <= t:
            lo = mid
        else:
            hi = mid - 1
    i = lo
    span_old = old_cum[i + 1] - old_cum[i] or 1e-6
    frac = (t - old_cum[i]) / span_old
    return new_cum[i] + frac * (new_cum[i + 1] - new_cum[i])


# ── 平移 slots ──
n = 0
for r in rows:
    s0, e0 = r["start_sec"], r["end_sec"]
    s1, e1 = round(map_t(s0), 3), round(map_t(e0), 3)
    d1 = round(e1 - s1, 3)
    if d1 <= 0.05:  # 平移后过扁的 slot 保底 0.1s, 防导出除零
        d1, e1 = 0.1, round(s1 + 0.1, 3)
    cur.execute(
        "UPDATE director_slots SET start_sec=?, end_sec=?, duration_sec=? WHERE id=?",
        (s1, e1, d1, r["id"]))
    n += 1
con.commit()
print(f"平移 {n} slots 完成")

# 验证: 新 slot 时间轴尾 ≈ 新音频总长
mx = cur.execute(
    "SELECT MAX(end_sec) FROM director_slots WHERE director_job_id=?", (JOB_ID,)).fetchone()[0]
print(f"slot 轴尾 {mx:.2f}s vs 音频 {new_total:.2f}s")
