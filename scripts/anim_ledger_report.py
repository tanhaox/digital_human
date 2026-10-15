# -*- coding: utf-8 -*-
"""生产台账报表 (1005, 用户口径: 重做/补做=改进点, 首过批量=基线).

用法: python scripts/anim_ledger_report.py <书名> [ep]
数据源: outputs/动画/{书}/epN/build_ledger.jsonl (k2/h3.run_shot 逐笔, trigger=相位旗标)
        + shots.json.bak_* (重规划痕迹, 全历史)
口径: 每 (kind,sid) 首次=基线 (报表只列数不计改进); 后续=重做;
      首次但 trigger 含 fill/retry/redo/reroll/chain = 补充。
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "outputs" / "动画"
_IMPROVE = re.compile(r"fill|retry|redo|reroll|chain|replan")


def report(book: str, only_ep: int = 0) -> None:
    bdir = ROOT / book
    if not bdir.is_dir():
        print(f"无此书目录: {bdir}")
        return
    print(f"《{book}》 生产台账 (改进点口径: 重做=同镜再生成 / 补充=非首过相位的新镜)")
    print(f"{'ep':>3} {'图重做':>5} {'图补充':>5} {'视频重做':>6} {'视频补充':>6} {'改进耗时':>8} | 重规划痕迹 (bak)")
    for epd in sorted(bdir.glob("ep*"), key=lambda p: int(p.name[2:])):
        ep = int(epd.name[2:])
        if only_ep and ep != only_ep:
            continue
        rows = []
        lg = epd / "build_ledger.jsonl"
        if lg.exists():
            for line in lg.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        seen: Counter = Counter()
        redos, fills, ms = Counter(), Counter(), 0
        for d in rows:
            kind, sid = d.get("kind"), d.get("sid")
            if kind not in ("k2", "h3"):
                continue
            key = (kind, sid)
            seen[key] += 1
            if seen[key] == 1:
                if _IMPROVE.search(str(d.get("trigger") or "")):
                    fills[kind] += 1
                    ms += int(d.get("ms") or 0)
                continue  # 首过基线不计
            redos[kind] += 1
            ms += int(d.get("ms") or 0)
        baks = list(epd.glob("shots.json.bak_*"))
        kinds = Counter(re.sub(r"2026.*", "", b.name.replace("shots.json.bak_", "")) for b in baks)
        trail = " ".join(f"{k}×{v}" for k, v in sorted(kinds.items())) or "—"
        print(f"{ep:>3} {redos['k2']:>5} {fills['k2']:>5} {redos['h3']:>6} {fills['h3']:>6} "
              f"{ms // 1000:>7}s | {trail}")


if __name__ == "__main__":
    report(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0)
