# -*- coding: utf-8 -*-
"""build_jy_effect_catalog.py — J2 效果目录扫描器 v2 (2026-08-27).

扫 data/jy_template_snapshot/ 全部草稿 (含嵌套 subdraft/组合), 聚合
特效/动画/贴纸/转场 → data/jy_effect_catalog.json。

对号链 (name 为空的条目依次回退):
  1. 草稿自带 name 字段 (美颜类: 轮廓光/小脸/白牙…, video_effect: 模糊/暗角…)
  2. config/jy_effect_aliases.json 人工别名 (基线预设等)
  3. pyJianYingDraft 注册表 (video_effect 短 effect_id 直接对;
     长资源ID对 resource_id — 0.3.0 覆盖不全, 对上就赚)
仍无名 → 以原始 ID 展示并计入 _meta.unresolved 供人工补别名。

用法: python scripts/build_jy_effect_catalog.py   (快照更新后可重跑刷新)
"""
from __future__ import annotations

import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "jy_template_snapshot"
OUT = ROOT / "data" / "jy_effect_catalog.json"
ALIASES = ROOT / "config" / "jy_effect_aliases.json"

# material 容器 → 目录类别
CONTAINERS = {
    "effects": "effect",              # 美颜/调色算法特效 (长资源ID, name 常空)
    "video_effects": "video_effect",  # 画面特效 (短 effect_id + name)
    "stickers": "sticker",
    "transitions": "transition",
    "material_animations": "animation",  # 包装器: animations[] 内逐条
}


def rid_from_path(path: str | None, min_len: int = 6) -> str | None:
    """缓存路径 .../effect/<id>/<md5>/... → 提取 id 段."""
    if not path:
        return None
    i = path.find("effect")
    if i == -1:
        return None
    rest = path[i + 6:].replace("\\", "/").lstrip("/: ")
    head = rest.split("/")[0]
    return head if head.isdigit() and len(head) >= min_len else None


def harvest(node, out: dict[str, list]) -> None:
    """递归收集 materials.<容器> — 快照草稿嵌套 subdraft/组合, 任何一层都算."""
    if isinstance(node, dict):
        mats = node.get("materials")
        if isinstance(mats, dict):
            for key, cat in CONTAINERS.items():
                arr = mats.get(key)
                if isinstance(arr, list) and arr:
                    out[cat].extend(arr)
        for v in node.values():
            harvest(v, out)
    elif isinstance(node, list):
        for v in node:
            harvest(v, out)


def entry_identity(cat: str, it: dict) -> tuple[str | None, str | None]:
    """(主键ID, 显示名) — 各容器字段形状不同, 见 scripts/_probe 侦查结论 (2026-08-27)."""
    if cat == "animation":
        return None, None  # 在 collect 展开处理
    name = (it.get("name") or "").strip() or None
    if cat == "effect":
        return rid_from_path(it.get("path"), min_len=15), name
    if cat == "video_effect":
        return (it.get("effect_id") or "").strip() or rid_from_path(it.get("path")), name
    if cat == "transition":
        return (it.get("effect_id") or "").strip() or None, name
    if cat == "sticker":
        return (it.get("resource_id") or "").strip() or None, name or (it.get("title") or "").strip() or None
    return None, name


def load_registry() -> dict[str, dict]:
    """pyJianYingDraft 注册表: id(短effect_id/长resource_id) -> {name, category}."""
    reg: dict[str, dict] = {}
    try:
        import pyJianYingDraft as m  # 坑: site-packages 双大小写目录, 只认混合大小写名
    except ImportError:
        return reg
    for attr, cat in [("VideoSceneEffectType", "video_effect"),
                      ("VideoCharacterEffectType", "effect"),
                      ("TransitionType", "transition")]:
        cls = getattr(m, attr, None)
        if cls is None:
            continue
        for x in cls:
            mt = x.value
            reg[mt.effect_id] = {"name": mt.name, "category": cat}
            if getattr(mt, "resource_id", None):
                reg[mt.resource_id] = {"name": mt.name, "category": cat}
    return reg


def main() -> None:
    aliases = json.loads(ALIASES.read_text(encoding="utf-8")) if ALIASES.exists() else {}
    registry = load_registry()
    files = sorted(glob.glob(str(SNAPSHOT / "**" / "draft_content.json"), recursive=True))
    print(f"草稿 draft_content.json: {len(files)} 个 | 注册表: {len(registry)} 条 | 别名: {len(aliases)} 条")

    counts: dict[str, Counter] = defaultdict(Counter)          # cat -> key -> count
    names: dict[str, dict[str, str]] = defaultdict(dict)        # cat -> key -> name
    per_template: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    anim_durations: Counter = Counter()

    # 计数口径 = 模板目录 (一个模板 5+ 个 subdraft, 按文件计数会虚增 ~6 倍;
    # 推荐池信号是"多少模板用过" — rel 级去重, 2026-08-27 v2 定稿)
    seen_by_rel: dict[str, set] = defaultdict(set)

    def bump(rel: str, cat: str, key: str, name: str | None) -> None:
        if (cat, key) in seen_by_rel[rel]:
            return
        seen_by_rel[rel].add((cat, key))
        counts[cat][key] += 1
        label = (
            (names[cat].get(key))
            or (aliases.get(key, {}).get("name"))
            or (registry.get(key, {}).get("name"))
            or name
        )
        if label:
            names[cat][key] = label
        if (cat, label or key) not in per_template[rel][cat]:
            per_template[rel][cat].append((cat, label or key))

    for f in files:
        rel = Path(f).relative_to(SNAPSHOT).parts[0]  # 模板名 = 快照顶层目录
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        raw: dict[str, list] = defaultdict(list)
        harvest(d, raw)

        for cat, items in raw.items():
            for it in items:
                if not isinstance(it, dict):
                    continue
                if cat == "animation":
                    for anim in it.get("animations") or []:
                        if not isinstance(anim, dict):
                            continue
                        # 侦查 (2026-08-27): 字段是 type/resource_id/name/duration(μs)
                        aid = ((anim.get("resource_id") or "").strip()
                               or (anim.get("id") or "").strip() or None)
                        aname = (anim.get("name") or "").strip() or None
                        if not (aid or aname):
                            continue
                        kind = {"in": "anim_in", "out": "anim_out", "loop": "anim_loop"}.get(
                            (anim.get("type") or "").strip().lower(), "anim_other")
                        bump(rel, kind, aid or aname, aname)
                        if anim.get("duration"):
                            anim_durations[str(int(anim["duration"]) // 1000)] += 1
                    continue
                key, name = entry_identity(cat, it)
                if not (key or name):
                    continue
                bump(rel, cat, key or name, name)

    # 聚合输出: 类别 -> [{key,name,count}] 按 count 降序
    aggregated: dict[str, list[dict]] = {}
    unresolved: list[str] = []
    for cat, cnt in counts.items():
        rows = []
        for key, n in cnt.most_common():
            label = names[cat].get(key) or (aliases.get(key, {}).get("name")) \
                or (registry.get(key, {}).get("name"))
            if not label:
                label = key if key.isdigit() else (key or "?")
                if key.isdigit() and n >= 3:
                    unresolved.append(f"{cat}:{key}×{n}")
            row = {"key": key, "name": label, "count": n}
            if key in aliases and aliases[key].get("baseline"):
                row["baseline"] = True
            rows.append(row)
        aggregated[cat] = rows

    catalog = {
        "_meta": {
            "source": "jy_template_snapshot",
            "date": "2026-08-27",
            "version": 2,
            "tool": "scripts/build_jy_effect_catalog.py",
            "draft_files": len(files),
            "aliases": str(ALIASES.relative_to(ROOT)),
            "unresolved": unresolved,
        },
        "aggregated": aggregated,
        "anim_dur_ms": dict(anim_durations.most_common(12)),
        "per_template": {
            tpl: {cat: [lbl for _, lbl in labels] for cat, labels in cats.items()}
            for tpl, cats in per_template.items()
        },
    }
    OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(v) for v in aggregated.values())
    print(f"→ {OUT.relative_to(ROOT)}  类别×条目: {total} | 未对号(≥3次): {len(unresolved)}")
    for cat, rows in aggregated.items():
        top = ", ".join(f"{r['name']}×{r['count']}" for r in rows[:5])
        print(f"  {cat}: {len(rows)} 种 | {top}")


if __name__ == "__main__":
    main()
