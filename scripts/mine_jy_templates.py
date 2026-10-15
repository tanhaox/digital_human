# -*- coding: utf-8 -*-
"""模板挖掘器 — 118+ 口播工程明文时间轴 → 三层规律表 (2026-08-26).

数据源: G盘 140+套口播工程包 (只读, 剪映不打开防锁死) 的明文草稿:
  draft_content.json (明文版) / template.json(.bak) 明文镜像 / subdraft/**
产出 (data/jy_mining/):
  elements.jsonl  每文字元素一行 (字号/颜色/位置/字数/动画/字体/画布/密度)
  pairs.jsonl     同帧 (段入场动画 ↔ 音效) 配对
  summary.md      三张地基表:
    A 行宽约束表 — density=字号×字数×scale/画布宽 分布 (P50/P90/P99),
                  反推"字号→安全字数"对照 (横竖屏分桶) — 出片防裁切的查表依据
    B 动画↔音效共现矩阵 — 什么入场动画配什么音效, 音效时长分布
    C 颜色频率表 + D 字号分布 — 三色板/字号阶梯的全量统计验证
"""
from __future__ import annotations

import ast
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOTS = [
    r"G:\下载\140+套口播工程模板【淘宝：砚边资料铺】\40条一创",
    r"G:\下载\140+套口播工程模板【淘宝：砚边资料铺】\73口播工程",
    r"G:\下载\140+套口播工程模板【淘宝：砚边资料铺】\学习25期学习草稿-抖音是小鼎呀",
    r"F:\AI-Agent-Local\digital_human\data\jy_template_snapshot",
]
OUT = Path(__file__).resolve().parents[1] / "data" / "jy_mining"
US = 1_000_000


def _dig(v):
    """解字符串化嵌套: '{"a":1}' / "{'a':1}" → dict; 已是 dict 原样."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v[:1] in "{[":
        for fn in (json.loads, ast.literal_eval):
            try:
                return fn(v)
            except Exception:
                continue
    return v


def load_plain(folder: Path):
    """一个工程目录 → 明文时间轴 list[(源文件, dict)]. 空 canvas 的 tmp 跳过."""
    out = []
    for name in ("draft_content.json", "template.json", "template.json.bak"):
        p = folder / name
        if not p.exists():
            continue
        try:
            if p.read_bytes()[:1] != b"{":
                continue  # 加密
            d = json.loads(p.read_text(encoding="utf-8"))
            cc = d.get("canvas_config") or {}
            if not (cc.get("width") and cc.get("height")):
                continue  # template.tmp 空壳
            out.append((p.name, d))
            break  # 每目录取一个主源
        except Exception:
            continue
    # subdraft 明文子草稿 (复合片段内部时间轴)
    for p in folder.glob("subdraft/*/draft_content.json"):
        try:
            if p.read_bytes()[:1] != b"{":
                continue
            out.append((f"sub:{p.parent.name[:8]}", json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def iter_plaintexts():
    for root in ROOTS:
        rootp = Path(root)
        if not rootp.exists():
            print(f"[skip] 根目录不存在: {root}")
            continue
        for dirpath, _dirs, files in os_walk(rootp):
            if any(f in files for f in ("draft_content.json", "template.json", "template.json.bak")):
                yield Path(dirpath)


def os_walk(rootp: Path):
    import os
    for dp, ds, fs in os.walk(rootp):
        yield dp, ds, fs


def color_key(rgb):
    if not isinstance(rgb, (list, tuple)) or len(rgb) < 3:
        return None
    return tuple(round(float(x), 2) for x in rgb[:3])


def _anim_name_map():
    """动画/特效 resource_id → '枚举.名字' (pyJianYingDraft 枚举反查)."""
    import pyJianYingDraft as d
    rmap = {}
    for enum_name in ("TextIntro", "TextOutro", "TextLoopAnim", "IntroType",
                      "OutroType", "LoopType", "VideoSceneEffectType"):
        E = getattr(d, enum_name, None)
        if not E:
            continue
        for n in dir(E):
            if n.startswith("_"):
                continue
            rid = getattr(getattr(E, n), "value", None)
            rid = getattr(rid, "resource_id", None)
            if rid:
                rmap[rid] = f"{n}"
    return rmap


def mine() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sounds_dir = Path(__file__).resolve().parents[1] / "data" / "jy_sounds"
    sounds_dir.mkdir(parents=True, exist_ok=True)
    anim_names = _anim_name_map()
    n_projects = n_docs = 0
    n_sfx_harvest = 0
    elements = []   # 文字元素记录
    pairs = []      # (动画id, 音效名, 动画ms, 音效s, 画布)

    for folder in iter_plaintexts():
        docs = load_plain(folder)
        if not docs:
            continue
        n_projects += 1
        for src, d in docs:
            n_docs += 1
            m = d.get("materials", {})
            cc = d.get("canvas_config") or {}
            W, Hh = int(cc.get("width") or 0), int(cc.get("height") or 0)
            if not W:
                continue
            orientation = "portrait" if Hh > W else "landscape"

            # 索引: material_id → (材料, 轨道类型, 段)
            texts = {t.get("id"): t for t in m.get("texts", [])}
            anims = {a.get("id"): a for a in m.get("material_animations", [])}
            audios = {a.get("id"): a for a in m.get("audios", [])}

            # 文字段收集 (元素表) + 本 doc 音效事件
            text_segs = []
            doc_sfx = []
            for ti, tr in enumerate(d.get("tracks", [])):
                ttype = tr.get("type")
                for seg in tr.get("segments", []):
                    mat_id = seg.get("material_id")
                    clip = _dig(seg.get("clip")) or {}
                    tf = _dig(clip.get("transform")) or {}
                    scale = _dig(clip.get("scale")) or {}
                    trange = seg.get("target_timerange") or {}
                    refs = seg.get("extra_material_refs") or []
                    # 段入场动画 (从引用的 animation 材料取)
                    seg_anims = []
                    for r in refs:
                        am = anims.get(r)
                        if not am:
                            continue
                        for one in (am.get("animations") or []):
                            if isinstance(one, str):
                                continue
                            one = _dig(one)
                            aid = one.get("resource_id") or one.get("id") or one.get("animation_id")
                            if aid:
                                ms = one.get("duration") or 0
                                try:
                                    ms = int(ms) / 1000 if int(ms) > 100000 else int(ms)
                                except Exception:
                                    ms = 0
                                seg_anims.append((str(aid), ms, one.get("role") or one.get("type", "")))
                    if ttype == "text" and mat_id in texts:
                        t = texts[mat_id]
                        try:
                            content = json.loads(t["content"])
                        except Exception:
                            content = _dig(t["content"]) or {}
                        txt = content.get("text") or ""
                        for st in content.get("styles", []):
                            st = _dig(st) or {}
                            fill = _dig(st.get("fill")) or {}
                            solid = (_dig(fill.get("content")) or {}).get("solid") or {}
                            col = color_key(_dig(solid.get("color")))
                            fnt = _dig(st.get("font")) or {}
                            size = st.get("size")
                            try:
                                size = float(size)
                            except Exception:
                                size = 0
                            sx = scale.get("x") or 1
                            # 单行最大字符数 (估算行宽用)
                            lines = str(txt).split("\n")
                            max_line = max((len(l) for l in lines), default=0) or 1
                            aid0 = seg_anims[0][0] if seg_anims else None
                            elements.append({
                                "src": str(folder)[-60:], "doc": src, "orient": orientation,
                                "w": W, "text": str(txt)[:40], "chars": len(str(txt)),
                                "max_line": max_line, "size": size,
                                "density": round(size * max_line * float(sx) / W, 4),
                                "color": list(col) if col else None,
                                "bold": bool(st.get("bold")),
                                "x": tf.get("x"), "y": tf.get("y"), "sx": sx,
                                "font_id": (fnt.get("id") if isinstance(fnt, dict) else fnt) or t.get("font_resource_id"),
                                "anim_in": anim_names.get(aid0, aid0),
                                "anim_in_ms": seg_anims[0][1] if seg_anims else 0,
                                "start_s": round((trange.get("start") or 0) / US, 2),
                                "dur_s": round((trange.get("duration") or 0) / US, 2),
                                "track_idx": ti,
                            })
                            break  # 首样式 (主色主字号) — 多段样式另计
                        text_segs.append((trange.get("start") or 0, seg_anims, str(txt)[:20]))
                    # 音效段收集 (同帧配对用): 语义名音效 (type=sound), 顺手收割文件
                    elif ttype == "audio" and mat_id in audios:
                        am = audios[mat_id]
                        sname = (am.get("name") or "").strip()
                        spath = am.get("path") or ""
                        if sname and am.get("type") == "sound":
                            # 收割: G盘草稿内嵌音效 → 本地语义名库 (同名不覆盖)
                            src_file = folder / spath if spath and not spath.startswith(("C:", "D:")) else None
                            dst = sounds_dir / f"{sname}.mp3"
                            if src_file and src_file.exists() and not dst.exists():
                                try:
                                    import shutil
                                    shutil.copy2(src_file, dst)
                                    n_sfx_harvest += 1
                                except Exception:
                                    pass
                            dur_a = (trange.get("duration") or 0) / US
                            doc_sfx.append({
                                "audio_start": trange.get("start") or 0, "sfx": sname,
                                "sfx_sec": round(dur_a, 2), "orient": orientation,
                                "near_anim": None, "near_anim_ms": 0, "near_text": "",
                                "src": str(folder)[-50:],
                            })
            # 同帧配对 (本 doc 内): 音效 start ≈ 文字段 start (±60ms) → 挂上其动画
            for p in doc_sfx:
                for st_us, sa, txt in text_segs:
                    if abs(st_us - p["audio_start"]) <= 60_000:
                        p["near_anim"] = anim_names.get(sa[0][0], sa[0][0]) if sa else None
                        p["near_anim_ms"] = sa[0][1] if sa else 0
                        p["near_text"] = txt
                        break
            pairs.extend(doc_sfx)

    # ── 落盘 ──
    with open(OUT / "elements.jsonl", "w", encoding="utf-8") as f:
        for e in elements:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(OUT / "pairs.jsonl", "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # ── summary 三张表 ──
    lines = ["# 模板挖掘 summary (自动生成)", f"", f"工程目录: {n_projects} | 明文时间轴: {n_docs} | 文字元素: {len(elements)} | 音效段: {len(pairs)}", ""]

    # A 行宽约束表 (density = size × 单行字数 × scale / 画布宽)
    lines += ["## A. 行宽约束表 (density 分布; >1.0 ≈ 出屏)", ""]
    for orient in ("portrait", "landscape"):
        dens = sorted(e["density"] for e in elements if e["orient"] == orient and e["size"] > 0)
        if not dens:
            continue
        def pct(q):
            return dens[min(len(dens) - 1, int(len(dens) * q))]
        lines.append(f"- **{orient}** n={len(dens)} | P50={pct(0.5):.3f} P90={pct(0.9):.3f} "
                     f"P99={pct(0.99):.3f} max={dens[-1]:.3f}")
        # 字号→安全单行字数 (按 P90 density 反推)
        lines.append("  | 字号 | 安全单行字数 (P90) |")
        lines.append("  |---|---|")
        for sz in (8, 10, 12, 15, 18, 20, 24, 28, 34):
            if not dens:
                continue
            safe = int(W_DEFAULT[orient] * pct(0.9) / sz) if sz else 0
            lines.append(f"  | {sz} | ~{max(1, safe)} 字 |")
    lines.append("")

    # B 动画↔音效共现
    co = Counter()
    for p in pairs:
        if p.get("near_anim"):
            co[(p["near_anim"], p["sfx"])] += 1
    lines += ["## B. 动画↔音效同帧共现 Top40", "", "| 入场动画ID | 音效 | 次数 |", "|---|---|---|"]
    for (aid, sfx), n in co.most_common(40):
        lines.append(f"| `{aid}` | {sfx} | {n} |")
    # 音效时长分布 (配动画时)
    dur_pairs = [p["sfx_sec"] for p in pairs if p.get("near_anim") and p["sfx_sec"] > 0]
    if dur_pairs:
        dur_pairs.sort()
        lines += ["", f"配对音效时长: P50={dur_pairs[len(dur_pairs)//2]:.2f}s "
                  f"P90={dur_pairs[int(len(dur_pairs)*0.9)]:.2f}s max={dur_pairs[-1]:.2f}s"]
    lines.append("")

    # C 颜色频率
    cc = Counter(tuple(e["color"]) for e in elements if e["color"])
    lines += ["## C. 颜色频率 Top20", "", "| RGB | 次数 | 典型字号 |", "|---|---|---|"]
    size_by_color = defaultdict(list)
    for e in elements:
        if e["color"]:
            size_by_color[tuple(e["color"])].append(e["size"])
    for col, n in cc.most_common(20):
        sizes = sorted(size_by_color[col])
        med = sizes[len(sizes) // 2]
        lines.append(f"| ({col[0]:.2f},{col[1]:.2f},{col[2]:.2f}) | {n} | {med:.0f} |")
    lines.append("")

    # D 字号分布
    lines += ["## D. 字号分布", ""]
    for orient in ("portrait", "landscape"):
        sizes = [e["size"] for e in elements if e["orient"] == orient and e["size"] > 0]
        if not sizes:
            continue
        hist = Counter(int(s) for s in sizes)
        top = ", ".join(f"{s}号×{n}" for s, n in hist.most_common(15))
        lines.append(f"- **{orient}** n={len(sizes)}: {top}")
    lines.append("")

    # 字体 Top (名字反查)
    ff = Counter(e["font_id"] for e in elements if e["font_id"])
    fname = {}
    try:
        import pyJianYingDraft as d
        for n in dir(d.FontType):
            rid = getattr(getattr(d.FontType, n), "value", None)
            rid = getattr(rid, "resource_id", None)
            if rid:
                fname[rid] = n
    except Exception:
        pass
    lines += ["## E. 字体频率 Top15", "", "| 字体 | 次数 |", "|---|---|"]
    for fid, n in ff.most_common(15):
        lines.append(f"| {fname.get(fid, fid)} | {n} |")
    lines += ["", f"音效收割: 新增 {n_sfx_harvest} 个语义名文件 → data/jy_sounds/"]

    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 挖掘完成: {n_projects} 工程 / {n_docs} 时间轴 / {len(elements)} 文字元素 / {len(pairs)} 音效段")
    print(f"   → {OUT}\\summary.md")


W_DEFAULT = {"portrait": 1080, "landscape": 1920}


if __name__ == "__main__":
    mine()
