# -*- coding: utf-8 -*-
"""Gate 0.5 合规过滤层 (0908 设计令) — L0 料池 → Step 1 选料池之间的确定性阀门.

原则 (用户令): 只把合规的往下游传导, 阻断层层传递 — 过滤是代码不是提示词,
组装 Step1/quotes/骨架输入时做替换/剔除, 下游永远只见安全版。

映射来源 (两级):
1. 结构化 JSON (新书): 蒸馏时 audit_res 落 `{书名}.合规映射.json` — {orig, safe, action}
2. 文本备注解析 (旧书兜底): 蒸馏 txt「## 合规备注」节 `- 改写: X → ...改为"Y"...`
   — 抽引号内安全句为 safe; 无安全句的条目按剔除处理。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["load_compliance_map", "sanitize_material", "sanitize_quote_pool"]

_RE_MARK_LINE = re.compile(r"^- 改写[:：]\s*(.+?)\s*→\s*(.+)$", re.M)
# dir 文本里的成品安全句: 优先「改为"XXX"」后的引号段 (首个引号常是被删原词, 0908 实锤)
_RE_SAFE_REPL = re.compile(r"改为[：:]?[“\"]([^”\"]{8,120})[”\"]")
# dir 文本里的成品安全句: 「改为"XXX"」/「"XXX"」中文引号 ≥8 字
_RE_SAFE_IN_DIR = re.compile(r"[“\"]([^”\"]{8,120})[”\"]")
_RE_RULE_LINE = re.compile(r"^- 注意[:：]\s*(.+)$", re.M)


def _l0_dir(book_title: str) -> Path:
    from .l0 import _l0_dir as _d
    return _d(book_title)


def load_compliance_map(book_title: str, source_path: str = "") -> dict:
    """→ {rules: [{orig, safe, note}], hard_notes: [str]} — 映射缺失返回空 (fail-open+log).

    JSON 在 L0 目录; 旧书文本备注在蒸馏 txt (book.source_path, G盘等外部路径)。
    """
    root = _l0_dir(book_title)
    jf = root / "合规映射.json"
    if jf.exists():
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            return {"rules": list(data.get("rules") or []), "hard_notes": list(data.get("hard_notes") or [])}
        except Exception as exc:
            logger.warning("[gate0.5] 合规映射 JSON 损坏, 回退文本解析: %s", exc)
    # 旧书兜底: 解析蒸馏 txt 备注节 (source_path 优先, L0 目录其次)
    cands = list(root.glob("*.蒸馏.txt"))
    if source_path and Path(source_path).exists():
        cands.insert(0, Path(source_path))
    for tf in cands:
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = re.search(r"## 合规备注.*?\n(.*?)(?:\n## |\Z)", text, re.S)
        if not m:
            continue
        rules, hard = [], []
        for mm in _RE_MARK_LINE.finditer(m.group(1)):
            orig, direction = mm.group(1).strip(), mm.group(2).strip()
            sm = _RE_SAFE_REPL.search(direction) or _RE_SAFE_IN_DIR.search(direction)
            rules.append({"orig": orig, "safe": sm.group(1).strip() if sm else "",
                          "note": direction[:80]})
        for rr in _RE_RULE_LINE.finditer(m.group(1)):
            hard.append(rr.group(1).strip())
        if rules or hard:
            return {"rules": rules, "hard_notes": hard}
    return {"rules": [], "hard_notes": []}


def _norm_key(s: str) -> str:
    return re.sub(r"[\s，。、；：！？“”\"']", "", s or "")


def sanitize_material(text: str, book_title: str, source_path: str = "",
                      *, drop_unsafe: bool = True) -> tuple[str, list[str]]:
    """书料文本 → (安全文本, 命中说明列表).

    逐条规则: orig (归一化后 ≥10 字才可信, 过短误伤) 在文中命中 →
      - 有 safe: 整条改写说明对应的安全句替换 orig 所在句
      - 无 safe 且 drop_unsafe: 删除命中句 (高危出局)
    fail-open: 无映射原样返回。
    """
    cmap = load_compliance_map(book_title, source_path)
    hits: list[str] = []
    if not cmap["rules"]:
        return text, hits
    out = text
    for r in cmap["rules"]:
        orig = _norm_key(r.get("orig") or "")
        safe = (r.get("safe") or "").strip()
        if len(orig) < 10:
            continue
        # 在原文里找 orig 的可见形态 (取前 12 字做锚, 命中即处理所在句)
        anchor = r.get("orig") or ""
        probe = re.sub(r"\s", "", anchor)[:12]
        hay = re.sub(r"\s", "", out)
        if probe and probe in hay:
            # 定位含锚的整句并替换/删除
            sent_re = re.compile(r"[^。！？\n]*" + re.escape(probe[:6]) + r"[^。！？\n]*[。！？]?",
                                 re.S)
            def _repl(m: re.Match) -> str:
                hits.append(f"{'替换' if safe else '剔除'}: {m.group(0)[:30]}…")
                return safe + ("。" if safe and not safe.endswith(("。", "！", "？")) else "") if safe else ""
            out = sent_re.sub(_repl, out, count=1)
    return out, hits


def sanitize_quote_pool(quotes: list[str], book_title: str, source_path: str = "") -> list[str]:
    """B-Q1 选句池过滤 (0908 漏洞堵): 命中敏感映射的句子剔除 (字幕上屏零容忍, 不做替换)."""
    cmap = load_compliance_map(book_title, source_path)
    if not cmap["rules"]:
        return quotes
    keys = [_norm_key(r.get("orig") or "")[:12] for r in cmap["rules"] if len(_norm_key(r.get("orig") or "")) >= 10]
    out = []
    for q in quotes:
        nq = _norm_key(q)
        if any(k and k in nq for k in keys):
            continue
        out.append(q)
    if len(out) < len(quotes):
        logger.info("[gate0.5] 书摘池过滤 %d → %d 句", len(quotes), len(out))
    return out
