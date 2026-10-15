# -*- coding: utf-8 -*-
"""挖掘产物固化 — data/jy_mining/*.jsonl → 三个机器可读 config (2026-08-26).

闭环: mine_jy_templates.py 挖掘 → 本脚本固化 → jy_effect_library / 音效编排引擎
     / HF 模板 v2 查表消费。新模板包到手: 重挖 → 重固化, config 自动升级。

产出:
  config/jy_layout_constraints.json    行宽约束/字号分布 (防裁切查表, 横竖屏)
  config/jy_anim_sfx_cooccurrence.json 动画↔音效共现矩阵 + 时长规则
  config/jy_style_palette.json         颜色板(带语义)/字体表/字号阶梯
  data/jy_sounds/_index.json           重建 (含新收割 173 个音效的时长)
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
MINING = ROOT / "data" / "jy_mining"
CONFIG = ROOT / "config"
SOUNDS = ROOT / "data" / "jy_sounds"
CANVAS_W = {"portrait": 1080, "landscape": 1920}

# 颜色语义 (人工注入: 拆解文档积累的知识; 统计只给值, 语义给消费方)
COLOR_ROLES = {
    (1.0, 1.0, 1.0): "正文白 (绝对主导)",
    (1.0, 0.96, 0.54): "引文金/划重点金 (45期)",
    (1.0, 0.75, 0.09): "关键词黄/收尾黄 (53期)",
    (0.72, 0.11, 0.11): "冲击红/否定标记 (四模板)",
    (1.0, 0.87, 0.0): "建议黄/指令黄 (17期)",
    (0.0, 0.0, 0.0): "压纸黑标题 (49期新闻卡)",
    (1.0, 0.0, 0.0): "纯红巨字强调 (36号冲击档)",
    (1.0, 0.09, 0.22): "玫红金句 (53期核心词)",
}

# 打字类动画: 音效须钳到动画时长 (人审规则, 统计互证 P50=0.93s)
TYPEWRITER_ANIMS = ["打字光标", "新年打字机", "居中打字", "复古打字机", "预览打字",
                    "打字机_I", "打字机_II", "打字机_III", "打字机IV", "随机打字机",
                    "故障打字机", "变色输入"]


def pct(sorted_vals, q):
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * q))]


def main() -> None:
    now = datetime.now().isoformat(timespec="seconds")
    elements = [json.loads(l) for l in (MINING / "elements.jsonl").read_text(encoding="utf-8").splitlines()]
    pairs = [json.loads(l) for l in (MINING / "pairs.jsonl").read_text(encoding="utf-8").splitlines()]

    # ── 1. 行宽约束 ──
    lay = {"_meta": {"generated": now, "source": "data/jy_mining/elements.jsonl",
                     "rule": "density = 字号×单行字数×scale/画布宽; >1.0 出屏; "
                             "safe_p90=常规安全, safe_p99=极限出血",
                     "note": "landscape 样本为标题/贴字类, 字幕类按产线30字断句另论"}}
    for orient, w in CANVAS_W.items():
        dens = sorted(e["density"] for e in elements if e["orient"] == orient and e["size"] > 0)
        if not dens:
            continue
        p50, p90, p99 = pct(dens, 0.5), pct(dens, 0.9), pct(dens, 0.99)
        safe90, safe99 = {}, {}
        for sz in (8, 10, 12, 14, 15, 18, 20, 24, 28, 34, 36):
            safe90[sz] = max(1, int(w * p90 / sz))
            safe99[sz] = max(1, int(w * p99 / sz))
        sizes = Counter(int(e["size"]) for e in elements if e["orient"] == orient and e["size"] > 0)
        lay[orient] = {
            "canvas_w": w, "n": len(dens),
            "density": {"p50": round(p50, 3), "p90": round(p90, 3), "p99": round(p99, 3),
                        "max": round(dens[-1], 3)},
            "safe_chars_p90": safe90, "safe_chars_p99": safe99,
            "font_size_top": [{"size": s, "n": n} for s, n in sizes.most_common(12)],
        }
    (CONFIG / "jy_layout_constraints.json").write_text(
        json.dumps(lay, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 2. 动画↔音效共现 ──
    co = defaultdict(Counter)
    for p in pairs:
        if p.get("near_anim"):
            co[p["near_anim"]][p["sfx"]] += 1
    dur = sorted(p["sfx_sec"] for p in pairs if p.get("near_anim") and p["sfx_sec"] > 0)
    occ = {
        "_meta": {"generated": now, "source": "data/jy_mining/pairs.jsonl",
                  "usage": "jy_effect_library 应用入场动画后查 anims[动画名] 取音效族轮换; "
                           "打字类动画音效钳 anim_ms+150ms"},
        "anims": {anim: [{"sfx": s, "n": n} for s, n in c.most_common()]
                  for anim, c in sorted(co.items(), key=lambda kv: -sum(kv[1].values()))},
        "sfx_duration_sec": {"p50": round(pct(dur, 0.5), 2), "p90": round(pct(dur, 0.9), 2),
                             "max": round(dur[-1], 2)},
        "typewriter_anims": [a for a in TYPEWRITER_ANIMS if a in co],
    }
    (CONFIG / "jy_anim_sfx_cooccurrence.json").write_text(
        json.dumps(occ, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 3. 样式板 (颜色/字体/阶梯) ──
    col_n = Counter()
    col_sizes = defaultdict(list)
    for e in elements:
        if e["color"]:
            key = tuple(round(v, 2) for v in e["color"])
            col_n[key] += 1
            col_sizes[key].append(e["size"])
    font_n = Counter(e["font_id"] for e in elements if e["font_id"])
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
    pal = {
        "_meta": {"generated": now,
                  "usage": "HF 模板 v2 / J 线样式默认值; role 为人工语义标注"},
        "colors": [
            {"rgb": list(k), "n": n,
             "typical_size": sorted(col_sizes[k])[len(col_sizes[k]) // 2],
             "role": COLOR_ROLES.get(k)}
            for k, n in col_n.most_common(15)
        ],
        "fonts": [{"name": fname.get(f, f), "id": f, "n": n} for f, n in font_n.most_common(15)],
        "size_ladder_portrait": [12, 15, 18, 24, 36],
        "size_ladder_landscape": [12, 15, 17, 22],
        "subtitle": {"portrait": 12, "landscape": 15},
    }
    (CONFIG / "jy_style_palette.json").write_text(
        json.dumps(pal, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 4. 音效索引重建 (含新收割) ──
    idx = {}
    try:
        import pymediainfo
        for p in sorted(SOUNDS.glob("*.mp3")):
            try:
                mi = pymediainfo.MediaInfo.parse(str(p))
                t = mi.tracks[0]
                if t.duration:
                    idx[p.stem] = int(float(t.duration))
                    continue
            except Exception:
                pass
            idx[p.stem] = 1000  # 探测失败兜底
    except ImportError:
        print("[warn] pymediainfo 缺失, 索引时长用兜底值")
        idx = {p.stem: 1000 for p in SOUNDS.glob("*.mp3")}
    (SOUNDS / "_index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=0),
                                        encoding="utf-8")

    # ── 报告 ──
    print(f"✅ 固化完成 ({now})")
    print(f"  行宽约束:   {len(lay) - 1} 画布桶 | 竖屏 p90={lay.get('portrait', {}).get('density', {}).get('p90')}"
          f" / 横屏 p90={lay.get('landscape', {}).get('density', {}).get('p90')}")
    print(f"  共现矩阵:   {len(occ['anims'])} 动画 × 音效族 | 打字类 {len(occ['typewriter_anims'])} 个")
    print(f"  样式板:     颜色 {len(pal['colors'])} + 字体 {len(pal['fonts'])}")
    print(f"  音效索引:   {len(idx)} 个 (重建)")


if __name__ == "__main__":
    main()
