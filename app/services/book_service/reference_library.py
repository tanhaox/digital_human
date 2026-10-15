# -*- coding: utf-8 -*-
"""源头书库 (reference_library) — 跨书聚合 L0 references (2026-08-22).

背景: L0 每章提取 references(书内引用的书/理论/人), 此前是"孤岛"(提取了没消费).
本模块聚合全库 references → data/reference_library.json, 供:
- 选题池: 源头书 = 下一批拆书候选 (有分量, 读者信任)
- 内容关联: 逐集注入"作者还引用了《XX》…"
- 书单体系: 每本书引用了哪些源头书, 可作粉丝书单

结构:
{
  "sources": {"人间游戏": {"name","type","author","cited_in":[{book,chapters,context}]}},
  "books": {"蛤蟆先生去看心理医生": ["人间游戏", ...]}
}
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.config import get_config

logger = logging.getLogger(__name__)

__all__ = ["build_library", "load_library", "book_sources", "selection_pool", "leaderboard"]

# backmatter 参考文献区段判定词
_BACKM_REFS = ("参考文献", "参考书目", "延伸阅读", "参考书")


def _library_path() -> Path:
    return Path(get_config().defaults.l0_output_root) / "reference_library.json"


def _scan_l0_dirs(root: Path):
    """data/l0/ 下所有书的 l0-chapter-v1.json."""
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        p = d / "l0-chapter-v1.json"
        if p.exists():
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.warning("[ref-lib] 读取失败 %s: %s", p, exc)
    return out


def build_library() -> dict:
    """聚合全库 L0 references → data/l0/reference_library.json, 返回聚合结果."""
    root = Path(get_config().defaults.l0_output_root)
    sources: dict[str, dict] = {}
    books: dict[str, list[str]] = {}
    for l0 in _scan_l0_dirs(root):
        book = l0.get("book", "")
        cited: list[str] = []
        for ch in l0.get("chapters", []):
            for r in ch.get("references") or []:
                name = str(r.get("name", "")).strip()
                rtype = str(r.get("type", "book"))
                if not name:
                    continue
                key = f"{rtype}:{name}"
                src = sources.setdefault(key, {
                    "name": name, "type": rtype, "author": str(r.get("author", "") or ""),
                    "cited_in": [],
                })
                # 去重同书同章
                exists = any(c.get("book") == book and c.get("chapters") == [ch.get("idx")]
                             for c in src["cited_in"])
                if not exists:
                    src["cited_in"].append({"book": book, "chapters": [ch.get("idx")],
                                            "context": str(r.get("context", "") or "")})
                if name not in cited:
                    cited.append(name)
        # 2026-08-22: backmatter 参考文献并入 — 附录《》括起书名, 补章节内未提的源头书
        for sec in (l0.get("backmatter") or []):
            title = str(sec.get("name", ""))
            text = str(sec.get("text", ""))
            if not any(k in title or k in text[:120] for k in _BACKM_REFS):
                continue
            for m in re.findall(r"《([^》]{1,40})》", text):
                nm = m.strip()
                if not nm:
                    continue
                src = sources.setdefault(f"book:{nm}", {
                    "name": nm, "type": "book", "author": "", "cited_in": []})
                if not any(c.get("book") == book and c.get("source") == "backmatter"
                           for c in src["cited_in"]):
                    src["cited_in"].append({"book": book, "chapters": [], "source": "backmatter",
                                            "context": f"{title} 参考文献"})
                if nm not in cited:
                    cited.append(nm)
        if book and cited:
            books[book] = cited
    library = {"sources": sources, "books": books}
    _library_path().write_text(json.dumps(library, ensure_ascii=False, indent=1), encoding="utf-8")
    return library


def load_library() -> dict:
    """读已聚合的源头书库; 不存在返回空."""
    p = _library_path()
    if not p.exists():
        return {"sources": {}, "books": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"sources": {}, "books": {}}


def book_sources(book_title: str) -> list[dict]:
    """某本书引用的源头书清单 [{name, type, author, chapters}]."""
    lib = load_library()
    names = lib.get("books", {}).get(book_title, [])
    out = []
    for name in names:
        for key, src in lib.get("sources", {}).items():
            if src.get("name") == name:
                for c in src.get("cited_in", []):
                    if c.get("book") == book_title:
                        out.append({"name": name, "type": src.get("type"),
                                    "author": src.get("author"), "chapters": c.get("chapters", [])})
                break
    return out


def _norm_book(name: str) -> str:
    """书名归一: 去《》与空白."""
    return str(name).strip("《》 \t\r\n")


def _library_titles() -> set[str]:
    """书库已有书名 (文件名归一, 含蒸馏产物 .蒸馏.txt)."""
    from app.services.book_service.reader import clean_book_title, scan_book_sources
    return {clean_book_title(s["filename"].replace(".蒸馏", "")).strip("《》 \t\r\n")
            for s in scan_book_sources(Path(get_config().defaults.book_source_dir))}


def _in_library(nm: str, have: set[str]) -> bool:
    """书名是否已在书库; 同书异名(短边≥5字)按包含匹配."""
    return any(nm == h or (len(nm) >= 5 and nm in h) or (len(h) >= 5 and h in nm) for h in have)


def leaderboard(limit: int = 50) -> list[dict]:
    """源头书引用榜 — 跨书汇总所有源头书/理论/人物, 按被引用广度排序 (2026-08-23).

    供书库页「源头书引用榜」: 每拆一本书就累积, 展示哪些源头被反复引用(分量/信任度).
    同名跨 type 合并, 优先 book; book 类型标注是否已在书库(in_library).
    返回: [{name, type, author, weight, chapters_cited, in_library, cited_by:[{book,chapters}]}]
    """
    lib = load_library()
    if not lib.get("sources"):
        lib = build_library()
    have = _library_titles()
    rows: dict[str, dict] = {}
    for src in lib.get("sources", {}).values():
        nm = _norm_book(src.get("name", ""))
        if not nm:
            continue
        cur = rows.setdefault(nm, {"name": nm, "type": src.get("type", "book"),
                                   "author": "", "_seen": set(), "cited_by": []})
        if src.get("type") == "book" and cur["type"] != "book":
            cur["type"] = "book"
        if not cur.get("author") and src.get("author"):
            cur["author"] = src.get("author")
        for c in src.get("cited_in", []):
            key = f"{c.get('book')}:{c.get('chapters')}"
            if key not in cur["_seen"]:
                cur["_seen"].add(key)
                cur["cited_by"].append({"book": c.get("book"), "chapters": list(c.get("chapters") or [])})
    out = []
    for r in rows.values():
        out.append({
            "name": r["name"], "type": r["type"], "author": r.get("author", ""),
            "weight": len({c["book"] for c in r["cited_by"]}),
            "chapters_cited": len(r["cited_by"]),
            "in_library": _in_library(r["name"], have) if r["type"] == "book" else None,
            "cited_by": r["cited_by"],
        })
    out.sort(key=lambda x: (-x["weight"], -x["chapters_cited"]))
    return out[:limit]


def selection_pool() -> list[dict]:
    """源头书选题池 — 已拆书引用的源头书里, 书库缺失者 → 下一批拆书候选.

    排除: 已在书库(book_source_dir, 含已蒸馏产物)的源头书.
    排序: 引用广度(被几本书引用) → 引用章数, 大者优先.
    返回: [{name, author, weight, chapters_cited, cited_by:[{book, chapters}]}]
    并落地 data/l0/selection_pool.json 供导演/前端消费.
    """
    lib = load_library()
    if not lib.get("sources"):
        lib = build_library()
    # 书库已有书目 (文件名归一) — 含蒸馏产物 (.蒸馏.txt)
    have = _library_titles()

    cand: dict[str, dict] = {}
    for src in lib.get("sources", {}).values():
        if src.get("type") != "book":
            continue  # 仅 type=book 才可作拆书候选 (理论/人物不拆)
        nm = _norm_book(src.get("name", ""))
        if not nm or nm in cand:
            continue
        # 已在书库则排除 (含同书异名)
        if _in_library(nm, have):
            continue
        cited = src.get("cited_in", [])
        books = {c.get("book") for c in cited}
        cand[nm] = {"name": nm, "author": src.get("author", ""),
                    "weight": len(books), "chapters_cited": len(cited),
                    "cited_by": [{"book": c.get("book"), "chapters": c.get("chapters", [])}
                                 for c in cited]}
    pool = sorted(cand.values(), key=lambda x: (-x["weight"], -x["chapters_cited"]))
    (Path(get_config().defaults.l0_output_root) / "selection_pool.json").write_text(
        json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
    return pool


if __name__ == "__main__":
    import sys
    from app.config import load_config, set_config
    set_config(load_config())
    if "--pool" in sys.argv:
        pool = selection_pool()
        print(f"源头书选题池: {len(pool)} 个候选 (已拆书引用但书库缺失)")
        for c in pool[:20]:
            print(f"  ★{c['weight']} 章{c['chapters_cited']:>2} 《{c['name']}》"
                  f"{(' · '+c['author']) if c['author'] else ''}")
        sys.exit(0)
    lib = build_library()
    print(f"源头书库: {len(lib['sources'])} 个源头, {len(lib['books'])} 本书")
    for bk, srcs in lib["books"].items():
        print(f"  《{bk}》引用 {len(srcs)}: {', '.join(srcs[:6])}")
