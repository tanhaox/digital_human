# -*- coding: utf-8 -*-
"""书库解析 (2026-08-22 v3) — txt/epub 提取 + L0 结构 (docs/design/蒸馏产物schema设计.md §7).

L0 P0 增强：
- 排版强调保留：`<b>/<strong>`/class bold → §B§…§/B§，`<i>/<em>` → §I§…§/I§，
  `<blockquote>/<table>` → §BOX§…§/BOX§（不再全剥标签压空白，保句读）
- epub OPF 元数据：publisher/isbn/pub_date/creator → meta（书内，非爬虫）
- epub 目录：toc.ncx / nav.xhtml → toc（比文件名排序准）
- 注释/角标：epub 锚点 `<a href="#noteN">`+注释块 / txt 角标 `【N】` → notes
- 序言/附录定位：frontmatter / backmatter role（内容/标题特征，不靠文件名）

返回：{book_title, meta, frontmatter[], chapters[], backmatter[], total_chars}
epub = zip 装排版文本，非扫描版零 OCR。
"""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["scan_book_sources", "read_book", "clean_book_title"]

_TAG_RE = re.compile(r"<[^>]+>")
# 文件名后缀: (zhihailib.com) / （Z-Library） / [电子书] 等来源标记
_SUFFIX_RE = re.compile(r"[\(（\[][^()（）\[\]]*?(zhihailib|zlibrary|libgen|ebook|电子书)[^()（）\[\]]*?[\)）\]]", re.IGNORECASE)
# 章节头: 第X章/节/部 (中文数字或阿拉伯)
_HEAD_RE = re.compile(r"^第[一二三四五六七八九十百零\d]+[章节部]")
# 文中章节头 (无锚点, 供前料截断用)
_HEAD_ANY_RE = re.compile(r"第[一二三四五六七八九十百零\d]+[章节部]")

# 序言 (frontmatter) / 附录 (backmatter) 关键词 (内容/标题特征)
_FRONT_KEYWORDS = ("作者序", "自序", "再版", "新版序", "序言", "前言", "序", "导言", "引子", "写在", "译者序")
_BACK_KEYWORDS = ("附录", "术语表", "参考文献", "索引", "后记", "致谢", "附言", "鸣谢", "注释")

# ── 排版强调内联标记 ─────────────────────────────────────────────
# 作者/出版商的排版标记 = 划重点信号 (金句/公式/重点高置信标注), L0 必须保留.
def _strip_html_with_emphasis(raw: str) -> str:
    """XHTML → 文本, 保留排版强调为内联标记 (§B§/§I§/§BOX§), 不压空白保句读."""
    raw = re.sub(r"<(?:b|strong)(\s[^>]*)?>", "§B§", raw, flags=re.I)
    raw = re.sub(r"</(?:b|strong)>", "§/B§", raw, flags=re.I)
    raw = re.sub(r"<(?:i|em)(\s[^>]*)?>", "§I§", raw, flags=re.I)
    raw = re.sub(r"</(?:i|em)>", "§/I§", raw, flags=re.I)
    raw = re.sub(r"<(?:blockquote|table)(\s[^>]*)?>", "§BOX§", raw, flags=re.I)
    raw = re.sub(r"</(?:blockquote|table)>", "§/BOX§", raw, flags=re.I)
    # class 含 bold/strong/em 的 span/div/p → §B§ (排除 calibre 系排版类)
    def _class_em(m: re.Match[str]) -> str:
        cls = m.group(1).lower()
        if "calibre" not in cls and any(k in cls for k in ("bold", "strong", "em", "emphasis")):
            return "§B§"
        return ""
    raw = re.sub(r'<(?:span|div|p)\b[^>]*class="([^"]*)"[^>]*>', lambda m: _class_em(m), raw, flags=re.I)
    text = re.sub(r"<[^>]+>", "", raw)
    # 归一化: 横向空白压一个空格, 保留换行/段落
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _cut_frontmatter(text: str) -> str:
    """ epub 首章常混入目录/版权页前料: 若含"目录/版权"且后出现章节头, 从首个章节头截断."""
    if ("目录" in text[:200] or "版权" in text[:200]):
        m = _HEAD_ANY_RE.search(text)
        if m:
            return text[m.start():]
    return text


def clean_book_title(filename_stem: str) -> str:
    """文件名 → 书名: 去来源后缀标记 + 尾部噪声.

    0907: 尾部 +、点号等噪声剥除 — "HBO的内容战略+(zhihailib.com).epub"
    曾带 "+" 进书名, 口播念成"战略加"、字幕/封面带+ (豆包审稿实锤)。
    蒸馏txt与原书文件经同一清洗对齐, pending 判定不受影响。
    """
    s = _SUFFIX_RE.sub("", filename_stem).strip()
    s = re.sub(r"[\s\+\-—·、,，.。]+$", "", s).strip()
    return s


def scan_book_sources(root: str | Path) -> list[dict]:
    """扫描书库目录: txt/md/epub, 文件名=书名. 返回按文件名排序的清单."""
    root = Path(root)
    if not root.is_dir():
        logger.warning("[book] 书库目录不存在: %s", root)
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.suffix.lower() in (".txt", ".md", ".epub"):
            st = p.stat()
            out.append({
                "filename": p.name,
                "path": str(p),
                "book_title": clean_book_title(p.stem),
                "ext": p.suffix.lower().lstrip("."),
                "size": st.st_size,
                "mtime": st.st_mtime,  # 2026-08-20: 排序/折叠用
            })
    return out


# ── epub 元数据 / 目录 / 注释 (标准库, 零新依赖) ─────────────────
def _epub_meta(z: zipfile.ZipFile) -> dict:
    """epub OPF metadata → {author, publisher, isbn, pub_date}. 书内来源, 非爬虫."""
    meta: dict = {}
    try:
        container = z.read("META-INF/container.xml").decode("utf-8", errors="replace")
        m = re.search(r'full-path="([^"]+\.opf)"', container)
        if not m:
            return meta
        opf = z.read(m.group(1)).decode("utf-8", errors="replace")
        md = re.search(r"<metadata.*?</metadata>", opf, re.S)
        if not md:
            return meta
        block = md.group(0)

        def _dc(tag: str) -> str:
            mm = re.search(rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", block, re.S)
            if not mm:
                return ""
            return re.sub(r"<[^>]+>", "", mm.group(1)).strip()

        author = _dc("creator")
        if author:
            meta["author"] = author
        pub = _dc("publisher")
        if pub:
            meta["publisher"] = pub
        ident = _dc("identifier")
        if ident:
            meta["isbn"] = ident
        date = _dc("date")
        if date:
            meta["pub_date"] = date[:10]  # YYYY-MM-DD
    except Exception as exc:
        logger.warning("[book] epub 元数据解析失败: %s", exc)
    return meta


def _epub_toc(z: zipfile.ZipFile) -> list[str]:
    """epub 目录 (toc.ncx / nav.xhtml) → 章节标题列表."""
    toc: list[str] = []
    for name in z.namelist():
        ln = name.lower()
        if "toc.ncx" in ln or (ln.endswith((".xhtml", ".html")) and re.search(r"/(nav|toc)", ln)):
            try:
                raw = z.read(name).decode("utf-8", errors="replace")
            except Exception:
                continue
            titles = re.findall(r"<text[^>]*>(.*?)</text>", raw, re.S)  # ncx
            if not titles:
                titles = re.findall(r"<a[^>]*>(.*?)</a>", raw, re.S)  # nav
            toc = [re.sub(r"<[^>]+>", "", t).strip() for t in titles]
            toc = [t for t in toc if t]
            if toc:
                break
    return toc[:60]


def _epub_toc_map(z: zipfile.ZipFile) -> dict[str, str]:
    """epub 目录 → {章节文件: 标题} (toc.ncx/nav) — 章节名用标题替代文件名 (2026-08-22).

    0908 修 (HBO 实锤章名全丢): ncx 的 src 带锚点 (`text00004.html#pre4_4`) 且常无
    OEBPS/ 目录前缀 — 键规范化为「basename + 全路径」双键 (去锚点), 匹配两头都命中。
    同文件多锚点条目 (一书多节) 后条覆盖, 粗粒度可接受。
    """
    def _keys(src: str) -> list[str]:
        p = src.split("#")[0].lstrip("/")
        if not p:
            return []
        return [p, p.split("/")[-1]] if "/" in p else [p]

    m: dict[str, str] = {}
    for name in z.namelist():
        ln = name.lower()
        if "toc.ncx" in ln or (ln.endswith((".xhtml", ".html")) and re.search(r"/(nav|toc)", ln)):
            try:
                raw = z.read(name).decode("utf-8", errors="replace")
            except Exception:
                continue
            if "toc.ncx" in ln:
                for mm in re.finditer(
                        r"<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content src=\"([^\"]+)\"", raw, re.S):
                    t = re.sub(r"<[^>]+>", "", mm.group(1)).strip()
                    for k in _keys(mm.group(2)):
                        if t:
                            m[k] = t
            else:
                for mm in re.finditer(
                        r'<a[^>]*href="([^"]+\.(?:x?html))"[^>]*>(.*?)</a>', raw, re.S):
                    t = re.sub(r"<[^>]+>", "", mm.group(2)).strip()
                    for k in _keys(mm.group(1)):
                        if t:
                            m[k] = t
            if m:
                break
    return m


def _epub_notes(raw: str) -> list[dict]:
    """epub 注释锚点 → [{ref_marker, explanation}]. `<a href="#noteN">[2]</a>` + `<div id="noteN">`."""
    notes: list[dict] = []
    id_blocks: dict[str, str] = {}
    for m in re.finditer(r'<(?:div|p|span)[^>]*id="([^"]*)"[^>]*>(.*?)</(?:div|p|span)>', raw, re.S):
        nid, content = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if re.search(r"(note|footnote|注释|注\d)", nid, re.I) and content:
            id_blocks[nid] = content
    for m in re.finditer(r'<a[^>]*href="#([^"]+)"[^>]*>\s*([\[【（(]?\d+[\]】）)]?|.)\s*</a>', raw, re.I):
        target, marker = m.group(1), m.group(2).strip()
        if target in id_blocks:
            notes.append({"ref_marker": marker, "explanation": id_blocks[target]})
    return notes


def _epub_spine_order(z: zipfile.ZipFile) -> list[str] | None:
    """epub OPF spine → 章节文件顺序 (manifest idref→href 映射). 失败返回 None (回退文件名排序)."""
    try:
        container = z.read("META-INF/container.xml").decode("utf-8", errors="replace")
        m = re.search(r'full-path="([^"]+\.opf)"', container)
        if not m:
            return None
        opf = z.read(m.group(1)).decode("utf-8", errors="replace")
        href_by_id: dict[str, str] = {}
        for im in re.finditer(r'<item\b[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf):
            href_by_id[im.group(1)] = im.group(2)
        spine = re.findall(r'<itemref\b[^>]*idref="([^"]+)"', opf)
        order = []
        for rid in spine:
            href = href_by_id.get(rid)
            if href and href not in order:
                order.append(href)
        return order or None
    except Exception as exc:
        logger.warning("[book] epub spine 解析失败, 回退文件名排序: %s", exc)
        return None


def _split_chapters(text: str) -> list[tuple[str, str]]:
    """epub 文件内按"第X章 标题"分节 (2026-08-22) — 无结构化 h1/h2 的书, 章是正文"第X章"文本.

    - 去排版标记(§B§/§I§)后匹配"第X章" (正文章节标题常带粗体标记)
    - 标题行 + 下一行短标题合并 (如"第一章"+"整个人都不太好" → "第一章 整个人都不太好")
    - 目录项(无正文的短节)由调用方按长度过滤
    """
    lines = text.split("\n")
    secs: list[tuple[str, str]] = []
    cur_name, cur = "", []
    i = 0
    while i < len(lines):
        s = re.sub(r"§/?[BI]§", "", lines[i]).strip()
        if _HEAD_RE.match(s) and len(s) <= 40:
            title = s
            if i + 1 < len(lines):
                nxt = re.sub(r"§/?[BI]§", "", lines[i + 1]).strip()
                if nxt and len(nxt) <= 30 and not _HEAD_RE.match(nxt) \
                        and "目录" not in nxt and "CONTENTS" not in nxt and "contents" not in nxt:
                    title = f"{s} {nxt}"
                    i += 1
            if cur_name or cur:
                secs.append((cur_name, "\n".join(cur)))
            cur_name, cur = title, []
        else:
            cur.append(lines[i])
        i += 1
    if cur_name or cur:
        secs.append((cur_name, "\n".join(cur)))
    return secs or [("", text)]


def _section_role(name: str, text: str) -> str:
    """章节角色: frontmatter(序/前言) / backmatter(附录/参考文献/后记) / body.

    0908 修 (HBO 实锤 24/43 章被吞): 一文件一章的书, 每章开头常见"引子/导言/序"
    段落 — head 关键词对长正文文件误杀整章。改为: **长文件 (>4000字) 只按文件名
    判**, head 关键词仅对短文件 (真版权页/扉页/短序) 生效; 宁可漏杀进 body, 不可
    误杀丢整章。
    """
    head = text[:80]
    use_head = len(text) <= 4000
    for k in _BACK_KEYWORDS:
        if k in name or (use_head and k in head):
            return "backmatter"
    for k in _FRONT_KEYWORDS:
        if k in name or (use_head and k in head):
            return "frontmatter"
    return "body"


def _dedup_chapters(chapters: list[dict]) -> tuple[list[dict], int]:
    """1005 治本闸: 同文本重复章的确定性去重 (改写级同题重复归蒸馏编纂层治, 见
    distiller._COMPOSE_SYS 多形态去重律 — 本闸只治文本层).

    判定 (内容指纹, 不认文件名/标题):
      ①整文指纹: 归一化 (剥空白) 后 SHA1 相同;
      ②shingle 近重: 6字gram (步长3) Jaccard ≥ 0.8 — 治轻微改版/排版差异的复制件。
    保留策略: 按正文长度降序收编 (等长保持 epub 原序) — 同组保最长。
    """
    import hashlib

    def _norm(t: str) -> str:
        return re.sub(r"\s+", "", t)

    def _shingles(norm: str, n: int = 6, stride: int = 3) -> set[str]:
        return {norm[i:i + n] for i in range(0, max(len(norm) - n, 0), stride)}

    kept: list[dict] = []
    kept_shingles: list[set[str]] = []
    seen_hash: set[str] = set()
    for item in sorted(chapters, key=lambda c: -len(c.get("text", ""))):
        norm = _norm(item.get("text", ""))
        if not norm:
            continue
        h = hashlib.sha1(norm.encode("utf-8")).hexdigest()
        if h not in seen_hash:
            sh = _shingles(norm)
            dup = any(
                len(sh & ks) * 10 >= 8 * len(sh | ks)   # Jaccard ≥ 0.8
                for ks in kept_shingles if ks
            ) if sh else False
            if not dup:
                kept.append(item)
                kept_shingles.append(sh)
                seen_hash.add(h)
                continue
        # 重复 → 丢弃 (更长正文已在 kept)
    dropped = len(chapters) - len(kept)
    if dropped:
        logger.info("[book] 去重闸: %d → %d 章 (吸收 %d 条同文本重复)",
                    len(chapters), len(kept), dropped)
    # 恢复 epub 原序 (长度排序只用于保留方向判定)
    order = {id(c): i for i, c in enumerate(chapters)}
    kept.sort(key=lambda c: order.get(id(c), 0))
    return kept, dropped


def _read_epub(path: Path) -> dict:
    """epub → {meta, toc, frontmatter[], chapters[], backmatter[]}."""
    z = zipfile.ZipFile(path)
    meta = _epub_meta(z)
    toc = _epub_toc(z)
    toc_map = _epub_toc_map(z)  # 2026-08-22: 章节名用 toc 标题替代文件名
    order = _epub_spine_order(z)
    names = [n for n in z.namelist() if n.lower().endswith((".html", ".xhtml", ".htm"))]
    names = [n for n in names if not re.search(r"(cover|toc|nav|title|ncx|opf)", n, re.I)] or names
    if order:
        by_name = {n: n for n in names}
        ordered = [o for o in order if o in by_name or o.lstrip("/") in by_name]
        if ordered:
            names = ordered
    else:
        names.sort()

    frontmatter, chapters, backmatter = [], [], []
    for n in names:
        try:
            raw = z.read(n).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("[book] 章节读取失败 %s: %s", n, exc)
            continue
        notes = _epub_notes(raw)
        text = _strip_html_with_emphasis(raw)
        text = _cut_frontmatter(text)
        if len(text) <= 200:  # 过短视为版权页/扉页
            continue
        fname = n.split("/")[-1]
        base_name = toc_map.get(n.lstrip("/")) or toc_map.get(fname) or fname
        # 文件内按"第X章"分节 (2026-08-22) — 粒度 = 真实章
        for sec_name, sec_text in _split_chapters(text):
            if len(sec_text) <= 200:
                continue
            name = sec_name or base_name
            role = _section_role(name, sec_text)
            item: dict = {"name": name, "text": sec_text, "role": role}
            if notes:
                item["notes"] = notes
            if role == "frontmatter":
                frontmatter.append(item)
            elif role == "backmatter":
                backmatter.append(item)
            else:
                chapters.append(item)
    chapters, _ = _dedup_chapters(chapters)  # 1005 治本闸: 多形态重复吸收
    return {"meta": meta, "toc": toc,
            "frontmatter": frontmatter, "chapters": chapters, "backmatter": backmatter}


def _read_txt(path: Path) -> dict:
    """txt/md → {meta, toc, frontmatter[], chapters[], backmatter[]}. 角标【N】→ notes."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    # 开头元信息: 目录 / 作者简介 (前 8KB 扫描)
    meta: dict = {}
    head = raw[:8000]
    for kw in ("关于作者", "作者简介", "作者简介：", "作者简介:"):
        i = head.find(kw)
        if i >= 0:
            seg = head[i + len(kw):i + len(kw) + 400]
            j = seg.find("\n\n")
            bio = seg if j < 0 else seg[:j]
            if bio.strip() and len(bio.strip()) < 300:
                meta["author_bio"] = re.sub(r"\s+", " ", bio).strip()
                break

    chapters: list[dict] = []
    cur_name, cur = "全文", []
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            continue
        if _HEAD_RE.match(s) and len(s) <= 30 and cur:
            chapters.append({"name": cur_name, "text": "".join(cur)})
            cur_name, cur = s, []
        cur.append(s)
    chapters.append({"name": cur_name, "text": "".join(cur)})
    chapters = [c for c in chapters if c["text"].strip()]

    frontmatter, body, backmatter = [], [], []
    for c in chapters:
        c["role"] = _section_role(c["name"], c["text"])
        # 角标注释: 【N】在正文, 匹配章节内"注释"段 (简化: 仅识别存在, 精确关联由第二层)
        m = re.findall(r"[【\[](\d{1,3})[】\]]", c["text"][:2000])
        if m:
            c["notes_hint"] = f"正文含角标 {len(m)} 处"
        if c["role"] == "frontmatter":
            frontmatter.append(c)
        elif c["role"] == "backmatter":
            backmatter.append(c)
        else:
            body.append(c)

    # 目录: 开头"目录"段 (若无章节头书, 从"目录"到首个章节头)
    toc: list[str] = []
    if "目录" in raw[:500]:
        i = raw.find("目录", 0, 500)
        seg = raw[i:i + 1500].split("\n")
        for line in seg[1:]:
            s = line.strip()
            if not s:
                continue
            if _HEAD_RE.match(s) and len(s) <= 30:
                toc.append(s)
            elif toc and _HEAD_RE.match(s):
                break
    return {"meta": meta, "toc": toc[:60],
            "frontmatter": frontmatter, "chapters": body, "backmatter": backmatter}


_CJK_RE = re.compile(r"[一-鿿]")


def _cjk_ratio(text: str) -> float:
    """去空白后 CJK 占比 — 章节语言判定 (0925 中英双版避坑)."""
    body = re.sub(r"\s", "", text or "")
    return (len(_CJK_RE.findall(body)) / len(body)) if body else 0.0


def strip_english_edition(parsed: dict, *, chapter_th: float = 0.3,
                          para_th: float = 0.05, para_min: int = 40,
                          min_en_frac: float = 0.10,
                          min_keep_chars: int = 30_000) -> dict:
    """中英双版书 → 只留中文版 (0925 用户令: 解读中文版即可, 英文版不蒸馏).

    前后双版典型 (卓有成效实锤: 30 中文章 cjk≥0.84 + 29 英文章 cjk=0):
    - 章级: cjk 占比 < chapter_th 判英文版章, 整章剔除;
    - 段级: 保留章内纯英文长段 (≥para_min 字且 cjk < para_th) 同步剔除
      (交错排版/英文原文引句噪声);
    - 守卫: 英文占比 < min_en_frac 视为普通中文书 (零风险旁路, 原样返回);
      剔除后中文存量 < min_keep_chars 视为纯英文书误判, 也原样返回
      (绝对值下限 — 卓有成效英文原文占 76%, 相对比例会把正经双版书误杀)。
    total_chars 保持原始口径不动 (蒸馏 epub 覆盖率质检基准), 剔除量记
    en_stripped_chars 供日志/UI 播报。
    """
    chs = parsed.get("chapters") or []
    total = sum(len(c["text"]) for c in chs)
    if not chs or not total:
        return parsed
    kept: list[dict] = []
    en_chars = 0
    for c in chs:
        if _cjk_ratio(c["text"]) < chapter_th:
            en_chars += len(c["text"])
            continue
        paras = str(c["text"]).split("\n")
        kept_paras = []
        for p in paras:
            b = p.strip()
            if len(b) >= para_min and _cjk_ratio(b) < para_th:
                en_chars += len(p)
                continue
            kept_paras.append(p)
        kept.append(dict(c, text="\n".join(kept_paras)))
    if en_chars < total * min_en_frac or (total - en_chars) < min_keep_chars:
        return parsed
    parsed["chapters"] = kept
    parsed["en_stripped_chars"] = en_chars
    logger.info("[reader] 中英双版剔除英文 %s 字 (%d→%d 章)",
                f"{en_chars:,}", len(chs), len(kept))
    return parsed


def read_book(path: str | Path) -> dict:
    """解析 txt/md/epub → {book_title, meta, frontmatter[], chapters[], backmatter[], total_chars}.

    txt: 有"第X章"头则按头分章, 否则单章"全文"; epub: 按 spine 顺序自然章节。
    chapters[]: {name, text(带 § 排版强调标记), role, notes?}
    0925: 中英双版书自动只留中文版 (strip_english_edition); total_chars 保持
    原始口径 (覆盖率质检基准), 剔除量见 en_stripped_chars。
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".epub":
        parsed = _read_epub(path)
    elif ext in (".txt", ".md"):
        parsed = _read_txt(path)
    else:
        raise ValueError(f"不支持的书源格式: {ext} (txt/md/epub)")
    total = sum(len(c["text"]) for c in parsed["chapters"])
    parsed = strip_english_edition(parsed)
    parsed["book_title"] = clean_book_title(path.stem)
    parsed["total_chars"] = total
    return parsed


# ── 知海书页元数据爬取 (2026-08-19, L0 元信息兜底) ───────────────
# 仅作书内缺失字段的可选补全 (如 epub 无作者简介时), 非必需路径.
_ZHL_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_zhihailib_meta(url: str, timeout: int = 30) -> dict | None:
    """知海书页 → {title, author, publisher, pub_date, blurb, toc}. 失败返回 None."""
    import requests as _rq
    try:
        r = _rq.get(url, headers=_ZHL_UA, timeout=timeout)
        r.raise_for_status()
        t = r.text
    except Exception as exc:
        logger.warning("[book] 知海书页抓取失败: %s", exc)
        return None

    meta: dict = {"url": url}
    m = re.search(r"《([^》]+)》[^:]*:?\s*作者[:：]\s*([^,，]+)[,，]\s*出版社[:：]\s*([^,，]+)[,，]\s*出版日期[:：]\s*([^,，]+)", t)
    if m:
        meta.update(title=m.group(1).strip(), author=m.group(2).strip(),
                    publisher=m.group(3).strip(), pub_date=m.group(4).strip())
    plain = re.sub(r"<[^>]+>", "\n", t)
    plain = re.sub(r"\n\s*\n+", "\n", plain)

    def _block(start_key: str, end_keys: tuple, cap: int) -> str:
        i = plain.find(start_key)
        if i < 0:
            return ""
        i += len(start_key)
        j = min((plain.find(k, i) for k in end_keys if plain.find(k, i) >= 0), default=i + cap)
        return re.sub(r"\s+", " ", plain[i:min(j, i + cap)]).strip()

    meta["blurb"] = _block("内容简介", ("作者简介", "译者简介", "作者／译者简介", "目录"), 3000)
    # 目录: 条目本身可能含"作者／译者简介"等词, 不能当终止符 → 按条目模式逐行收
    toc: list[str] = []
    i = plain.find("\n目录\n")
    if i >= 0:
        pat = re.compile(r"^(\d{1,3}[　\s]|第[一二三四五六七八九十\d]+[章节部]|人物表|作者|译者|前言|序|附录|后记|致谢)")
        for line in plain[i + 4:].split("\n"):
            s = line.strip()
            if not s:
                continue
            if pat.match(s) and len(s) <= 60:
                toc.append(s)
            elif toc:
                break
            if len(toc) >= 60:
                break
    meta["toc"] = toc
    return meta
