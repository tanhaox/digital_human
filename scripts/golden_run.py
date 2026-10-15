# -*- coding: utf-8 -*-
"""黄金集跑分器 (0910): 固定用例 × 评分模板 → 建基线 / 回归对比.

用法:
  python scripts/golden_run.py --baseline   # 改模板后重建基线 (逐用例记预期)
  python scripts/golden_run.py              # 回归跑: 与基线对比, 红牌判定
断言 (边界用例 = 缺陷抓出):
  b01 钩子书名   → 维度1 ≤5          b06 平空断言  → 维度4 ≤6
  b02 端水       → 维度12 ≤5         b07 模糊刻度  → 维度5 ≤6
  b03 合规违规   → 维度14 ≤2 或 fatal含 → b08 纯史料  → 维度6 ≤6
  b04 层级断裂   → 维度2 ≤6          b09 金句埋段  → 维度8 ≤6
  b05 裸数据     → 维度9 ≤6          b10 CTA重复   → 维度15 ≤6
  c01 真实稿     → total 落基线 ±20 (温度0噪声带内)
"""
from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / ".tmp" / "golden"
BASE = GOLDEN / "baseline.json"
SCORE_TPL = Path("config/review_book_score_v1.txt")

# 用例 → 断言 (维度编号: 期望上限); c01 为区间断言
ASSERTS = {
    "b01_hook_bookname": {1: 5},
    "b02_rewater": {12: 5},
    "b03_compliance": {14: 2},
    "b04_tier_broken": {2: 6},
    "b05_naked_data": {9: 6},
    "b06_hollow_persona": {4: 6},
    "b07_vague_scale": {5: 6},
    "b08_no_mirror": {6: 6},
    "b09_quote_buried": {8: 6},
    "b10_cta_repeat": {15: 6},
}
TOL_TOTAL = 20  # c01 真实稿: 温度0中位噪声带


def score_once(script: str) -> dict:
    from app.config import load_config, set_config
    try:
        from app.config import get_config
        get_config()
    except RuntimeError:
        set_config(load_config())
    from app.services.book_service.creation_common import _llm
    tpl = io.open(SCORE_TPL, encoding="utf-8").read()
    raw = _llm().chat(tpl, script, model="pro", temperature=0.0)
    i = raw.rfind('{"scores"')
    if i < 0:
        return {"parse_fail": True, "total": 0, "scores": {}}
    for cut in range(raw.rfind("}"), i, -1):
        if raw[cut] == "}":
            try:
                d = json.loads(raw[i:cut + 1])
                return {"total": d.get("total", 0), "scores": d.get("scores", {}),
                        "fatal": d.get("fatal", [])}
            except Exception:
                continue
    return {"parse_fail": True, "total": 0, "scores": {}}


def run_all() -> dict:
    results = {}
    cases = sorted(GOLDEN.glob("*.txt"))
    for p in cases:
        name = p.stem
        script = p.read_text(encoding="utf-8")
        # 2 轮取中位 (温度0仍有采样波动, 实测±17)
        totals, best = [], None
        for _ in range(2):
            d = score_once(script)
            if not d.get("parse_fail"):
                totals.append(d["total"])
                best = d if d["total"] >= (max(totals) if len(totals) > 1 else d["total"]) else best or d
        med = statistics.median(totals) if totals else 0
        results[name] = {"total_med": med, "rounds": totals,
                         "scores": (best or {}).get("scores", {}),
                         "fatal": (best or {}).get("fatal", []),
                         "parse_fail": not totals}
        print(f"  {name}: 中位 {med} (轮值 {totals})")
    return results


def judge(results: dict, baseline: dict | None) -> int:
    fails = []
    for name, assert_map in ASSERTS.items():
        r = results.get(name)
        if not r or r.get("parse_fail"):
            fails.append(f"{name}: 解析失败")
            continue
        for dim, cap in assert_map.items():
            v = r["scores"].get(str(dim))
            if v is None:
                fails.append(f"{name}: 维度{dim}缺分")
            elif int(v) > cap:
                fails.append(f"{name}: 维度{dim}={v} 超上限{cap} (缺陷未抓出=回归)")
    c = results.get("c01_ep1_real")
    if baseline and c:
        b = baseline.get("c01_ep1_real", {}).get("total_med")
        if b and abs(c["total_med"] - b) > TOL_TOTAL:
            fails.append(f"c01: 中位 {c['total_med']} 偏离基线 {b} 超±{TOL_TOTAL}")
    if fails:
        print(f"\n🔴 红牌 {len(fails)} 条 — 改动阻塞, 逐条:")
        for x in fails:
            print("   ✗", x)
        return 1
    print("\n🟢 全绿 — 缺陷抓出率 100%, 真实稿在噪声带内")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()
    print(f"== 黄金集 {'建基线' if args.baseline else '回归'} {time.strftime('%Y-%m-%d %H:%M')} ==")
    results = run_all()
    if args.baseline:
        BASE.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"基线已存 {BASE}")
        return judge(results, None)
    baseline = json.loads(BASE.read_text(encoding="utf-8")) if BASE.is_file() else None
    if not baseline:
        print("无基线 — 先跑 --baseline")
        return 1
    return judge(results, baseline)


if __name__ == "__main__":
    raise SystemExit(main())
