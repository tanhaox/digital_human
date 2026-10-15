# -*- coding: utf-8 -*-
"""J 线草稿 diff — 手修版 vs 自动基线 = 结构化学习数据.

用法:
    python scripts/jy_draft_diff.py <草稿目录A:基线> <草稿目录B:手修版>

读取两份草稿的明文时间轴 (Timelines/*/template.json 优先, 顶层 template.json,
draft_content.json 若明文), 提取事件流后输出差异:
  + B 新增的 (效果/动画/转场/音效/字幕/贴纸/时长调整)
  - B 删除的
  ~ 属性变化 (音量/字号/样式 range)

学习循环: 导演台导出(基线) → 人工剪映精修(保存) → 本工具 diff → 差异入 R 配方.
"""
import glob
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_US = 1_000_000


def load_timeline(draft_dir: str) -> dict | None:
    for pats in (
        ["Timelines", "*", "template.json"],
        ["template.json"],
        ["draft_content.json"],
    ):
        for p in glob.glob(str(Path(draft_dir, *pats))):
            try:
                raw = open(p, "rb").read()
                if raw[:1] not in (b"{", b"["):
                    continue
                return json.loads(raw)
            except Exception:
                continue
    return None


def extract_events(data: dict) -> list[dict]:
    """把时间轴压平成事件列表 (可 diff 的粒度)."""
    m = data.get("materials", {}) or {}
    idx = {}
    for grp in ("videos", "audios", "texts", "stickers"):
        idx[grp] = {x.get("id"): x for x in (m.get(grp) or [])}
    anim_by_id = {}
    for an in m.get("material_animations", []) or []:
        for a in an.get("animations", []) or []:
            anim_by_id.setdefault(an.get("id"), []).append(
                f"{a.get('name')}({a.get('type')},{round((a.get('duration') or 0)/1000)}ms)")
    eff_by_id = {}
    for grp, label in (("effects", "特效"), ("video_effects", "特效"), ("transitions", "转场")):
        for e in m.get(grp, []) or []:
            nm = e.get("name") or e.get("resource_id")
            eff_by_id.setdefault(e.get("id"), []).append(f"{label}:{nm}")
    seg_effects = {}  # segment_id -> 效果描述列表
    for tr in data.get("tracks", []) or []:
        for s in tr.get("segments", []) or []:
            descs = []
            for ref in s.get("extra_material_refs", []) or []:
                descs += anim_by_id.get(ref, []) + eff_by_id.get(ref, [])
            if descs:
                seg_effects[s.get("id")] = descs

    events = []
    for tr in data.get("tracks", []) or []:
        ttype = tr.get("type")
        for s in tr.get("segments", []) or []:
            t = s.get("target_timerange") or {}
            mat_id = s.get("material_id")
            if ttype == "text":
                mat = idx["texts"].get(mat_id, {})
                try:
                    label = json.loads(mat.get("content") or "{}").get("text", "?")
                    styles = len(json.loads(mat.get("content") or "{}").get("styles", []))
                    label = f"字幕[{label[:14]}] 样式x{styles}"
                except Exception:
                    label = "字幕[?]"
            elif ttype == "audio":
                mat = idx["audios"].get(mat_id, {})
                kind = "音效" if mat.get("type") == "sound" else "音频"
                label = f"{kind}[{mat.get('name') or Path(mat.get('path') or '?').stem}]"
            elif ttype == "sticker":
                mat = idx["stickers"].get(mat_id, {})
                label = f"贴纸[{mat.get('name') or mat.get('resource_id') or '?'}]"
            elif ttype in ("video", "effect", "filter"):
                mat = idx["videos"].get(mat_id, {})
                label = f"{ttype}[{mat.get('material_name') or Path(mat.get('path') or '?').stem}]"
            else:
                label = f"{ttype}[?]"
            events.append({
                "key": f"{ttype}@{round((t.get('start') or 0)/_US, 1)}s",
                "label": label,
                "dur": round((t.get("duration") or 0) / _US, 2),
                "volume": s.get("volume"),
                "effects": seg_effects.get(s.get("id"), []),
            })
    return events


def diff(a_dir: str, b_dir: str) -> None:
    a, b = load_timeline(a_dir), load_timeline(b_dir)
    if not a or not b:
        print("!! 有草稿读不到明文时间轴")
        return
    ea, eb = extract_events(a), extract_events(b)
    ka = {e["key"]: e for e in ea}
    kb = {e["key"]: e for e in eb}

    print(f"基线 {len(ea)} 事件 / 手修 {len(eb)} 事件\n")
    print("== ➕ 手修新增 ==")
    for k in sorted(kb.keys() - ka.keys()):
        e = kb[k]
        print(f"  + {k} {e['label']} {e['dur']}s" + (f" | {e['effects']}" if e["effects"] else ""))
    print("\n== ➖ 手修删除 ==")
    for k in sorted(ka.keys() - kb.keys()):
        e = ka[k]
        print(f"  - {k} {e['label']} {e['dur']}s")
    print("\n== ~ 属性/效果变化 (同位置) ==")
    for k in sorted(ka.keys() & kb.keys()):
        x, y = ka[k], kb[k]
        changes = []
        if x["effects"] != y["effects"]:
            added = set(y["effects"]) - set(x["effects"])
            removed = set(x["effects"]) - set(y["effects"])
            if added:
                changes.append(f"+效果{sorted(added)}")
            if removed:
                changes.append(f"-效果{sorted(removed)}")
        if x["dur"] != y["dur"]:
            changes.append(f"时长 {x['dur']}→{y['dur']}")
        if x["label"] != y["label"]:
            changes.append(f"内容 {x['label']}→{y['label']}")
        if changes:
            print(f"  ~ {k} {y['label']}: {'; '.join(changes)}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    diff(sys.argv[1], sys.argv[2])
