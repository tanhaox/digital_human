# -*- coding: utf-8 -*-
"""元素级 PPT 布局分析 — 分带/分列/信息块聚类/宫格锚点检测.

拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

__all__ = ["_split_bands", "_content_columns", "_group_into_blocks",
           "_grid_anchors", "_detect_grid_anchors", "_dist2", "_is_marker_text",
           "_anchor_rows"]


def _split_bands(
    elems: list[dict], height_emu: float = 6858000,
) -> tuple[list[dict], list[dict], list[dict]]:
    """按纵向位置分带: (页头 top<12%, 内容带, 页脚 top>85%)."""
    header, body, footer = [], [], []
    for l in elems:
        tr = float(l.get("top", 0)) / height_emu if height_emu else 0
        if tr < 0.12:
            header.append(l)
        elif tr > 0.85:
            footer.append(l)
        else:
            body.append(l)
    return header, body, footer


def _content_columns(layers: list[dict], height_emu: float = 6858000) -> int:
    """内容带列数 (结构化判定): 剔除页头/页脚后按 left 聚类得几列."""
    _, body, _ = _split_bands(layers, height_emu)
    cols: list[list] = []
    for l in sorted(body, key=lambda x: float(x.get("left", 0))):
        if cols and abs(float(l.get("left", 0)) - float(cols[-1][0].get("left", 0))) <= 320000:
            cols[-1].append(l)
        else:
            cols.append([l])
    return len(cols)


def _is_marker_text(text: str) -> bool:
    """编号标记文本 (2026-09-02): 01 / 第X章 / CHAPTER N / PART 2 等短标记."""
    import re as _re
    t = (text or "").strip()
    if not t or len(t) > 12:
        return False
    pat = _re.compile(
        r"^(?:\d{1,3}|第[一二三四五六七八九十百\d]+[章回节讲集]?|"
        r"(?:chapter|chap|part|section|unit)\s*\d{1,3}[A-Za-z]?)$",
        _re.IGNORECASE,
    )
    return bool(pat.match(t))


def _is_marker_col(col: list[dict]) -> bool:
    """编号标记列 (2026-09-02): ≥2 个纯标记文本的列 (目录/清单页的编号列).

    编号列与右邻列是行配对语义 (01↔标题), 须合并后按行展示, 否则块级
    "左列→右列" 顺序会先出全部编号再出全部标题.
    允许 ≤1 个位于全部标记**上方**的非标记文本混入 (与编号列同 left 的
    跨列引言/小标题, 实测 P5 踩中) — 合并后 (top,left) 排序它自然最前.
    """
    texts = [l for l in col if l["kind"] == "text"]
    if len(texts) < 2:
        return False
    markers = [l for l in texts if _is_marker_text(l.get("text") or "")]
    others = [l for l in texts if not _is_marker_text(l.get("text") or "")]
    if len(markers) < 2:
        return False
    if not others:
        return True
    return (len(others) == 1
            and all(float(others[0].get("top", 0)) < float(m.get("top", 0))
                    for m in markers))


def _group_into_blocks(
    elems: list[dict],
    height_emu: float = 6858000,
    left_tol: float = 320000,
) -> list[list[dict]]:
    """按 PPT 版式把元素聚成信息块 (2026-08-21).

    - 页头 (top<12%高) → 独立块, 先显示
    - 页脚 (top>85%高) → 独立块, 后显示
    - 内容带按 left 聚类成列 (块): 三栏 PPT 的 01/02/03 各占一列
    - 图片随其列; 独立图作单块
    - 标记列合并 (2026-09-02): 纯编号列与右邻列并成一块, 块内 (top,left)
      排序天然行配对 (01+标题同批) — 修复目录页乱序
    返回块列表, 每块内元素无序 (排序由调用方).
    """
    header, body, footer = _split_bands(elems, height_emu)
    cols: list[list[dict]] = []
    for l in sorted(body, key=lambda x: float(x.get("left", 0))):
        if cols and abs(float(l.get("left", 0)) - float(cols[-1][0].get("left", 0))) <= left_tol:
            cols[-1].append(l)
        else:
            cols.append([l])
    merged: list[list[dict]] = []
    i = 0
    while i < len(cols):
        if _is_marker_col(cols[i]) and i + 1 < len(cols):
            merged.append(cols[i] + cols[i + 1])
            i += 2
        else:
            merged.append(cols[i])
            i += 1
    blocks: list[list[dict]] = []
    if header:
        blocks.append(header)
    blocks.extend(merged)
    if footer:
        blocks.append(footer)
    return blocks


def _grid_anchors(elems: list[dict], height_emu: float = 6858000) -> list[dict]:
    """宫格锚点候选 (2026-08-21): 数字标记(01/02/03) 或 短粗体标题(≥14pt≤20字),
    排除页头带. 返回锚点列表 (须再经行聚类判定是否真为宫格)."""
    import re as _re
    anchors = []
    for l in elems:
        if l["kind"] != "text":
            continue
        tr = float(l.get("top", 0)) / height_emu if height_emu else 0
        if tr < 0.12:
            continue
        text = (l.get("text") or "").strip()
        if not text:
            continue
        is_num = bool(_re.match(r"^\d{1,2}$", text))
        pt = float(l.get("pt") or 0)
        is_title = bool(l.get("bold")) and pt >= 14 and len(text) <= 20
        if is_num or is_title:
            anchors.append(l)
    return anchors


def _anchor_rows(anchors: list[dict], row_tol: float = 600000) -> list[list[dict]]:
    """锚点按 top 聚行 (top 差 ≤ row_tol 视为同行), 返回按 top 序的行列表."""
    rows: list[list[dict]] = []
    for a in sorted(anchors, key=lambda l: float(l.get("top", 0))):
        if rows and abs(float(a.get("top", 0)) - float(rows[-1][0].get("top", 0))) <= row_tol:
            rows[-1].append(a)
        else:
            rows.append([a])
    return rows


def _detect_grid_anchors(elems: list[dict], height_emu: float = 6858000) -> list[dict] | None:
    """宫格判定: 锚点按行聚类 (top 差≤600000 EMU), 任一行≥2 锚 → 宫格.

    优先数字标记(01), 其次短粗体标题. 返回最终锚点集; 非宫格返回 None.
    """
    anchors = _grid_anchors(elems, height_emu)
    if len(anchors) < 2:
        return None
    num_markers = [a for a in anchors if (a.get("text") or "").strip().isdigit()]
    pool = num_markers if len(num_markers) >= 2 else anchors
    rows = _anchor_rows(pool)
    if any(len(r) >= 2 for r in rows):
        return pool
    return None


def _dist2(a: dict, b: dict) -> float:
    return (float(a.get("left", 0)) - float(b.get("left", 0))) ** 2 + \
           (float(a.get("top", 0)) - float(b.get("top", 0))) ** 2
