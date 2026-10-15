# -*- coding: utf-8 -*-
"""打标质量抽检 v2: RLR 已入库素材的标签完整度/合理度/字幕判定分布。"""
import json
import random
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
random.seed(42)
con = sqlite3.connect("file:data/pipeline.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def parse_tags(raw):
    """register 的 tags 列可能是 dict/list/字段名列表, 统一成 dict。"""
    try:
        t = json.loads(raw) if raw else {}
    except Exception:
        return {}
    if isinstance(t, list):
        if t and isinstance(t[0], dict):
            base = {}
            for d in t:
                base.update(d)
            return base
        return {"_list": t}
    return t


rows = con.execute(
    "select * from video_assets where file_path like '%youtube%' "
    "and file_path like '%clips%' order by created_at desc").fetchall()
print(f"RLR 已入库: {len(rows)} 条")
if rows:
    print("tags 形态示例:", json.dumps(parse_tags(rows[0]["tags"]),
                                       ensure_ascii=False)[:180])

n_desc = n_kw = n_scene = n_true = n_false = n_untag = 0
desc_lens = []
for r in rows:
    t = parse_tags(r["tags"])
    if t.get("desc_zh"):
        n_desc += 1
        desc_lens.append(len(t["desc_zh"]))
    if t.get("keywords_en"):
        n_kw += 1
    if t.get("has_burned_text") is True:
        n_true += 1
    elif t.get("has_burned_text") is False:
        n_false += 1
    else:
        n_untag += 1
    if r["scenes"]:
        n_scene += 1

if rows:
    print(f"desc_zh 有值: {n_desc}/{len(rows)} ({n_desc/len(rows)*100:.0f}%)")
    if desc_lens:
        print(f"desc_zh 长度: 均{sum(desc_lens)/len(desc_lens):.0f}字 "
              f"最短{min(desc_lens)} 最长{max(desc_lens)}")
    print(f"keywords_en 有值: {n_kw}/{len(rows)}")
    judged = n_true + n_false
    print(f"字幕判定: 干净={n_false} 有字={n_true} 未判={n_untag} "
          f"→ 有字占比 {n_true/max(1,judged)*100:.0f}% (OCR干净窗出身,VLM兜底复查)")
    print(f"scenes 维度有值: {n_scene}/{len(rows)}")

    print("\n===== 随机抽 12 条 =====")
    for r in random.sample(rows, min(12, len(rows))):
        t = parse_tags(r["tags"])
        kw = ",".join((t.get("keywords_en") or [])[:4])
        print(f"· {r['asset_no']} [{r['duration_sec']:.0f}s] "
              f"{t.get('desc_zh', '∅')} | 字幕:{t.get('has_burned_text')} | {kw}")
