# -*- coding: utf-8 -*-
"""螺旋周检 (0910 SOP): 盘点累积数据 → 开闸判定 → (够量则) 元评审提案.

节奏: 每周账号数据分析后跑一次。文本层最优先 (用户令)。
数据仓: .tmp/spiral/ — 每集评审环产物按 epNN_ 前缀归档:
    epNN_report.txt   审片报告 (A-H 全文)
    epNN_script.txt   首稿
    epNN_rewrite.txt  终稿 (改稿官产出, 含 coverage JSON)
复盘仓: docs/content-playbook/复盘-*.md
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
SPIRAL = ROOT / ".tmp" / "spiral"
FUPAN_GLOB = "复盘-*.md"
GATE_EPISODES = 5     # 环② 开闸: ≥5 集
GATE_REVIEWS = 5      # 环③ 开闸: ≥5 条复盘


def inventory() -> tuple[list[str], list[str]]:
    eps = sorted({m.group(1) for p in SPIRAL.glob("ep*") if (m := re.match(r"(ep\d+)_", p.name))})
# TODO: 目录已改名 content-playbook，此 glob 现状 0 命中，留待 backlog 处理
    fus = sorted((ROOT / "docs" / "references").glob(FUPAN_GLOB.replace(" ", "")))
    return eps, [f.name for f in fus]


def meta_review(eps: list[str]) -> str:
    """元评审: 跨集找反复低分 + 反复手修 → 模板补丁提案 (人工审批后才可落)."""
    from app.config import load_config, set_config
    set_config(load_config()) if not _cfg_ok() else None
    from app.services.book_service.creation_common import _llm

    corpus = []
    for ep in eps:
        rep = SPIRAL / f"{ep}_report.txt"
        rw = SPIRAL / f"{ep}_rewrite.txt"
        if rep.is_file():
            corpus.append(f"===== {ep} 审片报告 =====\n{rep.read_text(encoding='utf-8')[:6000]}")
        if rw.is_file():
            t = rw.read_text(encoding='utf-8')
            cov = t[t.find('{"coverage"'):][:2000]
            corpus.append(f"===== {ep} 改稿 coverage =====\n{cov}")

    sys_p = (
        "你是「老谭读书」的元评审官。输入是近几集的审片报告与改稿 coverage。"
        "任务: 找出【反复出现的扣分模式】—— 哪些评分维度连续低分 (≤6)? 改稿官是否在每集反复手工修同类问题?"
        "若模式成立, 提 1~3 条对【生成模板】的补丁提案 (治本: 让首稿不再犯), 每条必须引用 ≥2 个具体案例 (集数+维度+原句摘录)。"
        "若某低分维度在生成模板中已有规则但仍反复低分, 归因为'规则在但未被执行'→ 提案应为结构调整 (前置/合并/删冗余) 而非加新规则。"
        "输出: ## 开闸发现\\n(模式清单, 每条带案例引用)\\n## 模板补丁提案\\n(每条: 规则文本/WHY/来源案例/retire_when)\\n## 不建议动的\\n(理由)。"
        "没有稳定模式就明说'本周不开闸' — 不凑数。"
    )
    return _llm().chat(sys_p, "\n\n".join(corpus)[:60000], model="pro", temperature=0.3)


def _cfg_ok() -> bool:
    try:
        from app.config import get_config
        get_config()
        return True
    except RuntimeError:
        return False


def main() -> int:
    SPIRAL.mkdir(parents=True, exist_ok=True)
    eps, fus = inventory()
    print(f"== 螺旋周检 {__import__('time').strftime('%Y-%m-%d')} ==")
    print(f"数据仓: 评审环产物 {len(eps)} 集 {eps} | 复盘账本 {len(fus)} 条")
    print(f"闸① 文本元评审: {'🟢 开 ({}/{} 集)'.format(len(eps), GATE_EPISODES) if len(eps) >= GATE_EPISODES else '🔴 未到 (差 {} 集)'.format(GATE_EPISODES - len(eps))}")
    print(f"闸② 真实数据校准: {'🟢 开 ({}/{} 条)'.format(len(fus), GATE_REVIEWS) if len(fus) >= GATE_REVIEWS else '🔴 未到 (差 {} 条)'.format(GATE_REVIEWS - len(fus))}")

    if len(eps) >= GATE_EPISODES:
        print("\n== 元评审启动 (提案仅供人工审批, 落模板须同步记挎斗账) ==")
        out = meta_review(eps)
        dest = ROOT / ".tmp" / "spiral" / f"meta_review_{__import__('time').strftime('%Y%m%d')}.md"
        dest.write_text(out, encoding="utf-8")
        print(out[:3000])
        print(f"\n全文已存 {dest}")
    else:
        print("\n本周不开闸 — 继续攒数据 (评审环每集自动产 report/rewrite, 归档到 .tmp/spiral/epNN_*.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
