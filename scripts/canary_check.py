# -*- coding: utf-8 -*-
"""金丝雀退化监测 (0910 playbook 采纳④, "防煤气").

提示词依赖模型行为稳定, 但供应商静默升级 — 同一段提示词质量渐崩而无报错
(kimi thinking 烧 token 事件即显性案例, 隐性 = 分数慢掉无人察觉)。

三件套:
  ① canary 用例集: .tmp/canary_seed_*.txt 固定稿件, 温度 0, 每日同批同提示词跑
  ② CUSUM 累积和: 不设死阈值, 累积下探趋势报警 (评审噪声 ±4~8, 单日抖动不算)
  ③ 三变量归因: 提示词版本(文件mtime) + 模型名 + 分数 → 退化时对表定责

用法:
  python scripts/canary_check.py            # 跑一轮, 追加 canary_log.jsonl, 输出趋势
  python scripts/canary_check.py --baseline # 首跑建基线
日志: .tmp/canary_log.jsonl (每行一轮: ts/scores/model/prompt_ver/cusum)
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CANARY_DIR = Path(".tmp")
LOG = CANARY_DIR / "canary_log.jsonl"
SCORE_TPL = Path("config/review_book_score_v1.txt")

# CUSUM 参数: 偏差累积, 阈值 12 ≈ 评审噪声带上沿的 3 天连续下探
_CUSUM_DRIFT = 3.0      # 单轮允许的随机下行 (实测同温0离散±24, 中位数后收窄)
_CUSUM_ALARM = 12.0     # 累积和越线 = 告警
_BASELINE_ROUNDS = 3    # 建基线跑几轮取中位数 (实测 temp0 单轮离散达 48 分!)


def _score_once(script: str) -> tuple[dict, str]:
    """一轮金丝雀: 固定提示词 + 温度 0 → (scores_dict, model_name)."""
    from app.config import load_config, set_config
    set_config(load_config()) if not _cfg_loaded() else None
    from app.services.book_service.creation_common import _llm
    score_p = io.open(SCORE_TPL, encoding="utf-8").read()
    raw = _llm().chat(score_p, script, model="pro", temperature=0.0)
    i = raw.rfind('{"scores"')
    d = {}
    if i >= 0:
        for cut in range(raw.rfind("}"), i, -1):
            if raw[cut] == "}":
                try:
                    d = json.loads(raw[i:cut + 1]); break
                except Exception:
                    continue
    model = "unknown"
    m = re.search(r'"?model"?\s*[:=]\s*"([^"]+)"', raw[:0] or "")  # 响应头不可得, 用配置名
    try:
        from app.config import get_config
        model = (getattr(get_config(), "kimi", None) and get_config().kimi.model_pro) or "unknown"
    except Exception:
        pass
    return d, model


def _cfg_loaded() -> bool:
    try:
        from app.config import get_config
        get_config()
        return True
    except RuntimeError:
        return False


def _prompt_ver() -> str:
    """提示词指纹: 评分模板 mtime+size (改没改一目了然)."""
    st = SCORE_TPL.stat()
    return f"{int(st.st_mtime)}:{st.st_size}"


def _load_log() -> list[dict]:
    if not LOG.is_file():
        return []
    return [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()]


def _cusum(rows: list[dict]) -> float:
    """累积和: 基线均值以下的部分逐轮累加 (每轮豁免 _CUSUM_DRIFT 随机量)."""
    base = [r["total"] for r in rows if r.get("is_baseline")]
    if not base:
        return 0.0
    base_sorted = sorted(base)
    mu = base_sorted[len(base_sorted) // 2]  # 中位数 (抗离群)
    c = 0.0
    for r in rows:
        if r.get("is_baseline"):
            continue
        d = mu - r.get("total", mu)
        if d > _CUSUM_DRIFT:
            c += d - _CUSUM_DRIFT
        else:
            c = max(0.0, c - 0.5)  # 回升则缓慢释放, 防永久挂账
    return round(c, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="首跑建基线 (清空旧基线标记)")
    args = ap.parse_args()

    seeds = sorted(CANARY_DIR.glob("canary_seed_*.txt"))
    if not seeds:
        print("无种子用例 (.tmp/canary_seed_*.txt)")
        return 1
    script = seeds[0].read_text(encoding="utf-8")

    rows = _load_log()
    if args.baseline:
        for r in rows:
            r["is_baseline"] = False

    print(f"金丝雀跑分: {seeds[0].name} ({len(script)}字) × {_BASELINE_ROUNDS if args.baseline else 1} 轮 …")
    import statistics
    _rounds = _BASELINE_ROUNDS if args.baseline else 2
    _totals = []
    for _ in range(_rounds):
        d, model = _score_once(script)
        if d.get("scores"):
            _totals.append(d.get("total", 0))
        if not d.get("scores"):
            print("  ✗ 评分 JSON 解析失败 — 本身就是退化信号 (格式服从度崩了)")
            row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "total": 0, "parse_fail": True,
                   "model": model, "prompt_ver": _prompt_ver(),
                   "is_baseline": bool(args.baseline)}
        else:
            row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "total": d.get("total"),
                   "scores": d.get("scores"), "fatal": d.get("fatal"),
                   "model": model, "prompt_ver": _prompt_ver(),
                   "is_baseline": bool(args.baseline)}
            rows.append(row)
            print(f"  total={row.get('total')} model={model}")
    # 中位数轮汇总 (防单轮离群触发误报)
    if _totals:
        rows.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "total": statistics.median(_totals), "rounds": _totals,
                     "model": model, "prompt_ver": _prompt_ver(),
                     "is_median": True, "is_baseline": bool(args.baseline)})
        print(f"  → 中位数: {statistics.median(_totals)} (轮值 {_totals})")

    with LOG.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    c = _cusum(rows)
    hist = [(r["ts"][:10], r.get("total")) for r in rows[-8:]]
    vers = {(r.get("model"), r.get("prompt_ver")) for r in rows if r.get("total")}
    print(f"\n历史 (近8轮): {hist}")
    print(f"模型×提示词版本组合: {len(vers)} 个")
    if c >= _CUSUM_ALARM:
        recent_v = sorted(vers)[-1]
        print(f"🔴 CUSUM={c} ≥ {_CUSUM_ALARM} — 评审链退化趋势确认!")
        print(f"   归因线索: 当前 (model={recent_v[0]}, prompt={recent_v[1]}); "
             f"对照日志首行版本 — 若 prompt_ver 未变而分数下探 → 供应商静默变更, 启动适配层")
    elif c > 0:
        print(f"🟡 CUSUM={c} 累积中 (阈值 {_CUSUM_ALARM}) — 观察即可")
    else:
        print(f"🟢 CUSUM={c} — 评审链健康")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
