# -*- coding: utf-8 -*-
"""L0 蒸馏管线  — 两级蒸馏第一层 (docs/design/蒸馏产物schema设计.md §7).

P1: reader 章节块 + 自适应打包落盘 data/l0/{book}/chapters/ (py)
P2: 每提取单元 Gemma 提取 l0-chapter schema (LLM)
P3: 校验 + 合并落盘 l0-chapter-v1.json + 全书 L0 总览 (py)

分界: py 干"书怎么变成章节"(结构), LLM 干"章节里有什么"(语义提取)。
命令行: python -m app.services.book_service.l0 <书路径> [--skip-llm]
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.config import get_config
from .reader import read_book, clean_book_title

logger = logging.getLogger(__name__)

__all__ = ["run_l0", "build_chapter_blocks", "pack_units", "extract_unit", "save_l0"]

_TARGET_CHARS = 12000  # 每提取单元 ≤12K 字符 (≈6-8K token, 留足输出 + 128k 余量, 稳不 OOM)
_SMALL_CHARS = 3000    # 小章 <3K 合并
_MAX_SMALL_GROUP = 4   # 小章一组 ≤4

_L0_EXTRACT_SYS = (
    "你是书籍 L0 提取器。对给定章节文本块做结构化穷尽提取，输出严格 JSON："
    '{"summary":str,"concepts":[{"name":str,"mech":str,"note_ref":str}],'
    '"cases":[{"desc":str,"scene":str}],'
    '"quotes":[{"text":str,"emphasis":bool,"source":str}],'
    '"entities":[str],'
    '"references":[{"type":"book|theory|person","name":str,"author":str,"context":str}],'
    '"note":str}。'
    "概念必须带机制解释；案例带细节与场景；quotes 原句逐字不得改写。"
    "文本中 §B§…§/B§(粗体)/§I§…§/I§(斜体)/§BOX§…§/BOX§(作者框出) 为排版强调，"
    "是金句/重点的高置信信号，提取 quotes/concepts 时优先参考；quotes 输出去掉 § 标记并标 emphasis:true。"
    "有注释的术语（若输入含 notes）用注释原文作 mech。只提取文本中存在的，不得编造。"
)


def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", str(s))[:60].strip() or "book"


def _l0_dir(book_title: str) -> Path:
    root = Path(get_config().defaults.l0_output_root)
    return root / _safe(book_title)


# ── P1 章节块 + 自适应打包 (py) ─────────────────────────────────
def build_chapter_blocks(path: str | Path) -> dict:
    """reader → {meta, toc, frontmatter[], chapters[{idx,name,text,role,notes?}], backmatter[]}.

    给章节补 idx（frontmatter/chapters/backmatter 全局递增），供打包/归并引用。
    """
    r = read_book(path)
    idx = 0
    for grp in (r["frontmatter"], r["chapters"], r["backmatter"]):
        for c in grp:
            idx += 1
            c["idx"] = idx
    return r


def pack_units(blocks: dict) -> list[dict]:
    """自适应打包: 小章合并 / 大章拆 part / 目标 ≤12K 字符.

    原则: 尊重书的语义边界 (按章), 别让本地 LLM 崩 (≤12K 稳定窗口).
    返回 [{chapters:[idx], titles:[str], text, part?}]
    """
    units: list[dict] = []
    small_buf: list[dict] = []
    small_len = 0

    def flush_small() -> None:
        nonlocal small_buf, small_len
        if not small_buf:
            return
        units.append({
            "chapters": [c["idx"] for c in small_buf],
            "titles": [c["name"] for c in small_buf],
            "text": "".join(c["text"] for c in small_buf),
        })
        small_buf, small_len = [], 0

    for ch in blocks["chapters"]:
        t = ch["text"]
        if len(t) > _TARGET_CHARS:
            flush_small()
            for i, part in enumerate(_split_into(t, _TARGET_CHARS)):
                units.append({
                    "chapters": [ch["idx"]],
                    "titles": [f"{ch['name']}#{i + 1}"],
                    "text": part, "part": i,
                })
        elif len(t) < _SMALL_CHARS:
            if len(small_buf) >= _MAX_SMALL_GROUP or small_len + len(t) > _TARGET_CHARS:
                flush_small()
            small_buf.append(ch)
            small_len += len(t)
        else:
            flush_small()
            units.append({"chapters": [ch["idx"]], "titles": [ch["name"]], "text": t})
    flush_small()
    return units


def _split_into(text: str, size: int) -> list[str]:
    """长文按段落切 ≤size 子块 (尽量段落边界断)."""
    paras = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            chunks.append(cur)
            cur = ""
        cur += p + "\n\n"
    if cur.strip():
        chunks.append(cur)
    return chunks or [text]


# ── P2 LLM 逐单元提取 (LLM) ─────────────────────────────────────
def extract_unit(unit: dict, client) -> dict:
    """单提取单元 → Gemma → l0-chapter 单元结构化."""
    user = f"【章节】{'、'.join(unit['titles'])}\n\n{unit['text']}"
    try:
        raw = client.chat(user, system=_L0_EXTRACT_SYS)
    except Exception as exc:
        logger.warning("[l0] 单元提取异常: %s", exc)
        return {"chapters": unit["chapters"], "error": str(exc)}
    data = client.parse_json_block(raw)
    if not data:
        logger.warning("[l0] 单元提取空 JSON: %s", unit["titles"][:1])
        return {"chapters": unit["chapters"], "error": "empty"}
    return {"chapters": unit["chapters"], **data}


# ── P3 校验 + 落盘 (py) ─────────────────────────────────────────
def save_l0(path: str | Path, blocks: dict, units_results: list[dict]) -> Path:
    """合并校验 → data/l0/{book}/ 落盘: chapters/ + meta.json + l0-chapter-v1.json + overview.json."""
    book_title = clean_book_title(Path(path).stem)
    d = _l0_dir(book_title)
    chapters_dir = d / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    # 章节块落盘 (原始块, 供第二层面向直接读)
    for c in blocks["chapters"]:
        (chapters_dir / f"chapter_{c['idx']:02d}.json").write_text(
            json.dumps({"idx": c["idx"], "title": c["name"], "role": c["role"],
                        "text": c["text"], "notes": c.get("notes", [])},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(blocks["meta"], ensure_ascii=False, indent=1), encoding="utf-8")

    # 单元结果 → 按章节归并
    by_ch: dict[int, dict] = {}
    for ur in units_results:
        for chidx in ur["chapters"]:
            base = by_ch.setdefault(chidx, {"summary": "", "concepts": [], "cases": [],
                                            "quotes": [], "entities": [], "references": [], "note": ""})
            for k in ("concepts", "cases", "quotes", "entities", "references"):
                v = ur.get(k, [])
                base[k] += v if isinstance(v, list) else []
            if ur.get("summary") and not base["summary"]:
                base["summary"] = ur["summary"]
            if ur.get("note") and not base["note"]:
                base["note"] = ur["note"]

    l0 = {
        "schema": "l0-chapter-v1",
        "book": book_title,
        "meta": blocks["meta"],
        "toc": blocks.get("toc", []),
        "frontmatter": blocks["frontmatter"],
        "chapters": [
            {"idx": c["idx"], "title": c["name"], "text": c["text"],
             "notes": c.get("notes", []), **by_ch.get(c["idx"], {})}
            for c in blocks["chapters"]
        ],
        "backmatter": blocks["backmatter"],
    }
    (d / "l0-chapter-v1.json").write_text(
        json.dumps(l0, ensure_ascii=False, indent=1), encoding="utf-8")

    overview = {"book": book_title, "meta": blocks["meta"],
                "chapters": [{"idx": c["idx"], "title": c["name"],
                              "summary": by_ch.get(c["idx"], {}).get("summary", "")}
                             for c in blocks["chapters"]]}
    (d / "overview.json").write_text(
        json.dumps(overview, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


# ── 主入口 ──────────────────────────────────────────────────────
def run_l0(path: str | Path, client=None, skip_llm: bool = False) -> dict:
    """单本 L0 全流程: P1 打包 → P2 提取 → P3 落盘.

    skip_llm=True: 只做结构层 (P1+P3), 不跑 Gemma (验证打包/落盘用).
    """
    from .distiller import GemmaClient, ensure_gemma

    blocks = build_chapter_blocks(path)
    units = pack_units(blocks)
    results: list[dict] = []
    if not skip_llm:
        client, started = ensure_gemma(client or GemmaClient())
        for i, u in enumerate(units, 1):
            logger.info("[l0] 提取单元 %d/%d: %s", i, len(units), u["titles"][:1])
            results.append(extract_unit(u, client))
    d = save_l0(path, blocks, results)
    return {
        "book": clean_book_title(Path(path).stem),
        "dir": str(d),
        "units": len(units),
        "extracted": sum(1 for r in results if not r.get("error")),
    }


if __name__ == "__main__":
    import sys
    from app.config import load_config, set_config

    set_config(load_config())
    if len(sys.argv) < 2:
        print("用法: python -m app.services.book_service.l0 <书路径> [--skip-llm]")
        sys.exit(1)
    r = run_l0(sys.argv[1], skip_llm="--skip-llm" in sys.argv)
    print(json.dumps(r, ensure_ascii=False, indent=1))
