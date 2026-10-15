# -*- coding: utf-8 -*-
"""LLM 微 pass 引擎助手 (0930 重构: 自 episode_gen 各 pass 内联代码提取共享)。

统一骨架: 触发(病族扫描) → LLM 定向微编辑 → 复检 → 闸 → 落档 → 日志。
闸判据三大助手 (原各 pass 内联复制, 现单一实现):
- issue_family / family_new: 字数/车轱辘话/复读段同病不同引文归一族 —
  字符串精确比对把同病当新病, 好稿两轮误杀实锤
- word_band_ok: 字数同病取距带更近者 (加笑删冗的带内浮动 ≠ 新问题)
- fig_count / fig_better: 贯穿人物出场配额的次数提取与"程度减轻"判定
快照可组合: validate/flow/fig/brand 按 pass 需要取 — 幽默基线不含 fig、
CTA+去重共享 pre-CTA 基线 (压测实锤的语义差异, 禁统一)。
"""
from __future__ import annotations

import re

# 同病族 (同族不计数 — 新病才是拒稿理由)
FAMILIES = ("字数", "车轱辘话", "复读段")


def issue_family(issue: str) -> str:
    """问题归类到病族; 其余按原串 (具体行为=精确比对)。"""
    for k in FAMILIES:
        if k in issue:
            return k
    return issue


def family_new(after: list[str], base: list[str]) -> list[str]:
    """after 相对 base 的新病 (病族豁免: base 已有同族不算新)。"""
    base_fams = {issue_family(b) for b in base}
    return [i for i in after if issue_family(i) not in base_fams]


def _word_count(issue: str) -> int | None:
    m = re.search(r"字数 (\d+)", issue)
    return int(m.group(1)) if m else None


def word_band_ok(h_issues: list[str], b_issues: list[str], target_sec: float) -> bool:
    """字数同病豁免: 新问题全为字数族 且 距带更近 (1013欠24 vs 1109超5 误杀实锤)。"""
    cap = int(float(target_sec or 300) * 4.6)
    lo = int(cap * 0.94)

    def dist(n):
        return 0 if n is None or lo <= n <= cap else min(abs(n - lo), abs(n - cap))

    h_n = next((_word_count(i) for i in h_issues if "字数" in i), None)
    b_n = next((_word_count(i) for i in b_issues if "字数" in i), None)
    return dist(h_n) <= dist(b_n)


def fig_count(issue: str) -> int | None:
    """贯穿人物 issue「X出现 N 次>N」→ N; 非人物 issue → None。"""
    if not issue:
        return None
    m = re.search(r"贯穿人物「[^」]+」出现 (\d+) 次", issue)
    return int(m.group(1)) if m else None


def fig_better(after_fig: str | None, base_fig: str | None) -> bool:
    """人物出场次数下降 = 程度减轻 (闸只认条数时 5→4 改善被误拒实锤)。"""
    a, b = fig_count(after_fig or ""), fig_count(base_fig or "")
    return a is not None and b is not None and a < b


def fig_quota_issues(text: str, figure: str) -> list[str]:
    """贯穿人物出场配额 (全稿≤4处职能出场, 名字词频=通用代理指标)。"""
    if not figure:
        return []
    name = re.split(r"[：:（(]", figure)[0].strip()
    short = name.split("·")[-1] if "·" in name else name
    if len(short) < 2:
        return []
    n = text.count(short)
    if n > 4:
        return [f"贯穿人物「{name}」出现 {n} 次 >4 (全稿≤4处职能出场各一次: 钩子反差结论/"
                "中段完整故事仅一次/拆解三过表量化/总结身份重述 — 病在内容双讲不在名字, "
                "同一段事实禁出现两遍) — 合并去重"]
    return []


def snapshot(text: str, *, target: float, book_title: str, require_cta: bool = False,
             ep_index: int = 0, flow_check: bool = False, figure: str = "",
             include: tuple[str, ...] = ("validate", "flow", "brand")) -> dict[str, list[str]]:
    """一次校验快照 (可组合部分)。pass 闸比对都吃快照, 禁各 pass 零散重调。

    include: validate=validate_script(含CTA)/flow=section_flow_issues/
             brand=中国品牌闸/fig=人物配额 — 按 pass 语义取, 例: 幽默不含 fig。
    """
    from .creation_common import validate_script, section_flow_issues, _zh_brand_issues
    snap: dict[str, list[str]] = {}
    if "validate" in include:
        snap["validate"] = list(validate_script(text, target, book_title=book_title,
                                                require_cta=require_cta)[1])
    if "flow" in include:
        snap["flow"] = list(section_flow_issues(text, book_title=book_title,
                                                ep_index=ep_index, flow_check=flow_check))
    if "brand" in include:
        snap["brand"] = _zh_brand_issues(text, book_title)
    if "fig" in include:
        snap["fig"] = fig_quota_issues(text, figure)
    return snap


def snap_issues(snap: dict[str, list[str]], *parts: str) -> list[str]:
    """快照取扁平问题列表 (默认全并)。"""
    keys = parts or tuple(snap.keys())
    return [i for k in keys if k in snap for i in snap[k]]
