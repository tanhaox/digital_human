# -*- coding: utf -*-
"""一次性审计: 扫全库稿件的多音字/生僻字 → 稿件读音风险报告。
只读 DB, 不写任何东西。产物: docs/audit-polyphone-20260903.md
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sqlite3

from pypinyin import pinyin

from app.services.pinyin_fix import _rules  # 复用加载逻辑 (含缓存)

DB = ROOT / "data" / "pipeline.db"
OUT = ROOT / "docs" / "audit-polyphone-20260903.md"


def load_texts():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    texts, seen = [], set()
    for (t,) in conn.execute("SELECT script_text FROM scripts WHERE script_text IS NOT NULL"):
        if t and t not in seen:
            seen.add(t)
            texts.append(t)
    for (t,) in conn.execute(
        "SELECT script_text FROM book_episodes WHERE script_text IS NOT NULL"
    ):
        if t and t not in seen:
            seen.add(t)
            texts.append(t)
    conn.close()
    return texts


def main():
    import re
    from pypinyin import pinyin as _pinyin

    texts = load_texts()
    rules = _rules()
    # 词表已覆盖的字符 (命中词内部的字不算风险)
    covered = set("".join(rules.keys()))

    han = re.compile(r"[一-鿿]")
    poly_counter = Counter()      # 字 -> 出现次数
    poly_grams = defaultdict(Counter)  # 字 -> ngram -> 次数
    rare_fail = Counter()         # GB2312 编不出的超生僻字
    rare_l2 = Counter()           # GB2312 二级字 (次常用)

    readings_cache = {}

    def readings(ch):
        if ch not in readings_cache:
            try:
                h = pinyin(ch, heteronym=True, errors=lambda x: ["?"])[0]
            except Exception:
                h = ["?"]
            readings_cache[ch] = h
        return readings_cache[ch]

    for text in texts:
        # 剥离已有 <字|PINYIN> 标注再统计
        text = re.sub(r"<(\S{1,4})\|[A-Z]+[1-5]>", r"\1", text)
        # 词表命中区间整体跳过 (已纠音)
        skip_spans = []
        for word in rules:
            start = 0
            while True:
                i = text.find(word, start)
                if i < 0:
                    break
                skip_spans.append((i, i + len(word)))
                start = i + 1
        chars = list(text)
        for i, ch in enumerate(chars):
            if not han.match(ch) or ch in covered:
                continue
            if any(a <= i < b for a, b in skip_spans):
                continue
            # 生僻度
            try:
                b = ch.encode("gb2312")
                level2 = b[0] >= 0xD8
            except UnicodeEncodeError:
                rare_fail[ch] += 1
                level2 = False
            if level2:
                rare_l2[ch] += 1
            # 多音字
            if len(readings(ch)) > 1:
                poly_counter[ch] += 1
                for n in (2, 3, 4):
                    lo, hi = max(0, i - n + 1), min(len(chars), i + n)
                    for j in range(lo, hi - n + 1):
                        gram = "".join(chars[j : j + n])
                        if ch in gram and han.match(gram):
                            poly_grams[ch][gram] += 1

    lines = []
    lines.append("# 稿件读音风险审计 (2026-09-03)")
    lines.append("")
    lines.append(f"- 稿件源: data/pipeline.db → scripts + book_episodes (去重后 {len(texts)} 篇)")
    lines.append(f"- 已排除: 纠音词表命中词 (银行/行长/头发/长发/重复/重逢/磷化铟/昇腾/蛤蟆)")
    lines.append("- 分级: P0 = GB2312 编不出的超生僻字 (TTS 大概率读错/哑火); "
                 "P1 = GB2312 二级次常用字; P2 = 多音字 (按字频排序, 词表只应收词级恒定读法)")
    lines.append("")

    lines.append("## P0 超生僻字 (TTS 高危)")
    lines.append("")
    if rare_fail:
        for ch, c in rare_fail.most_common():
            lines.append(f"- **{ch}** ×{c}  候选读音: {'/'.join(readings(ch))}")
    else:
        lines.append("(无)")
    lines.append("")

    lines.append("## P1 二级次常用字 (含部分多音)")
    lines.append("")
    if rare_l2:
        for ch, c in rare_l2.most_common(80):
            r = readings(ch)
            tag = "多音" if len(r) > 1 else "单音"
            lines.append(f"- {ch} ×{c}  [{tag}] {'/'.join(r)}")
    else:
        lines.append("(无)")
    lines.append("")

    lines.append("## P2 多音字 (字频降序, 每字附最高频词形)")
    lines.append("")
    for ch, c in poly_counter.most_common():
        grams = poly_grams[ch].most_common(6)
        g = "  ".join(f"{w}({n})" for w, n in grams)
        lines.append(f"- **{ch}** ×{c}  读法候选: {'/'.join(readings(ch))}")
        lines.append(f"  - {g}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"篇数 {len(texts)} | P0 {sum(rare_fail.values())} | P1 {sum(rare_l2.values())} | 多音字种 {len(poly_counter)}")
    print(f"报告: {OUT}")


if __name__ == "__main__":
    main()
