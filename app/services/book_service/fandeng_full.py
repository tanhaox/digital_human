# -*- coding: utf-8 -*-
"""樊登全书稿 (0912 架构定稿): 蒸馏 → 一遍成型全书讲述 → 总编剧按稿切六集.

用户令: 蒸馏后每集各喂一次樊登 → 改为大纲前一次全书稿。
统一性 (一个叙事者, 故事只讲一次, 分配问题构造性消失) + 省生成 (6次→1次)。
稿落 data/l0/{书}/fandeng_full.txt; 段落编号/切片 helper 供 series_outline 与 episode_gen 共用。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import BookProject

logger = logging.getLogger(__name__)

_SEG_TARGET = 6000      # 每段喂料字数 (输入侧)
_TAIL_CONTEXT = 300     # 续写时带的前文尾部字数 (600 实测诱发回声复读, 收窄)
_SEG_MIN = 1500         # 单段健康下限 (目标3500-4500; 低于此=模型空转, 重写)

# 同书并发闸 (0912 撞车实锤: 页面点击 × API 重产竞写, 后写覆盖前写):
# 生成中再调直接返回 running, 不再双烧
_FF_RUNNING: dict[str, bool] = {}


def _para_sim(a: str, b: str) -> float:
    """字符 bigram Jaccard — 整段复读≈1.0, 不同故事<0.3。"""
    A = {a[i:i + 2] for i in range(len(a) - 1)}
    B = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(A & B) / max(1, len(A | B))


def _clean_segment(seg: str, seen: list[str]) -> tuple[str, int]:
    """剥 --- 噪声行 + 段内句级复读折叠 + 段级近重复丢弃 (对 seen 全库, >=0.6 判复读)。
    HBO 首跑实锤: 160段里72段复读 (模型回声环), 确定性去重是唯一可靠闸。"""
    seg = re.sub(r"(?m)^\s*-{3,}\s*$", "", seg or "")
    out, dropped = [], 0

    def _fold(p: str) -> str:
        # 句级: 同句在段内反复出现 → 只留首现 (句 sim 阈值抬高防误杀相似短句)
        if len(p) < 60:
            return p
        s_seen, s_out = [], []
        for s in (x for x in re.split(r"(?<=[。！？])", p) if x.strip()):
            if len(s) >= 15 and any(_para_sim(s, q) >= 0.75 for q in s_seen):
                continue
            s_seen.append(s)
            s_out.append(s)
        return "".join(s_out)

    for p in (x.strip() for x in seg.split("\n")):
        if not p:
            continue
        p = _fold(p)
        if not p:
            continue
        if len(p) < 30 or not any(_para_sim(p, q) >= 0.6 for q in seen):
            out.append(p)
            if len(p) >= 30:
                seen.append(p)
        else:
            dropped += 1
    return "\n\n".join(out), dropped


def fandeng_full_path(book_title: str) -> Path:
    from .l0 import _l0_dir
    return _l0_dir(book_title) / "fandeng_full.txt"


def load_fandeng_full(book_title: str) -> str:
    p = fandeng_full_path(book_title)
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        return ""


# ── 段落编号/切片 (总编剧喂入与逐集取料必须同一套切分规则) ──────────
def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]


def numbered_paragraphs(text: str) -> list[tuple[int, str]]:
    """非空段落编号 P1..Pn (喂总编剧用, 切片引用此编号)。"""
    return [(i + 1, p) for i, p in enumerate(_paragraphs(text))]


def slice_paragraphs(text: str, start_p: int, end_p: int) -> str:
    paras = _paragraphs(text)
    if start_p < 1 or end_p < start_p or start_p > len(paras):
        return ""
    end_p = min(end_p, len(paras))
    return "\n\n".join(paras[start_p - 1:end_p])


def _full_material(book: BookProject) -> str:
    """全书喂料: 蒸馏 sections 叙事序组装 (一页核压轴前置于元问题后)。"""
    from .distiller import load_section
    parts = []
    for name, cap in (("元问题", 300), ("一页核", 1200),
                      ("故事库", 0), ("方法与清单", 3000),
                      ("概念与机制", 3000), ("成功路径", 2000)):
        t = load_section(book.book_title, name)
        if t:
            parts.append(t[:cap] if cap else t)
    return "\n\n".join(parts)


def _chunk_material(mat: str, target: int = _SEG_TARGET) -> list[str]:
    """按空行段落攒块 (不撕段), 段超长硬切。"""
    paras = _paragraphs(mat)
    chunks, cur, used = [], [], 0
    for p in paras:
        take = len(p)
        if cur and used + take > target:
            chunks.append("\n\n".join(cur))
            cur, used = [], 0
        if take > target:  # 单段超长 → 硬切
            for i in range(0, take, target):
                chunks.append(p[i:i + target])
            continue
        cur.append(p)
        used += take
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def run_fandeng_full(db: Session, book: BookProject, *, force: bool = False) -> dict:
    """产全书樊登稿 (分段续写) → 合规清洗 → 落盘。幂等: 已有稿且非 force 直接返回。"""
    if _FF_RUNNING.get(book.id):
        return {"status": "running", "error": "该书樊登全书稿正在生成中, 请稍候"}
    _FF_RUNNING[book.id] = True
    try:
        return _run_fandeng_full_inner(db, book, force=force)
    finally:
        _FF_RUNNING[book.id] = False


def _run_fandeng_full_inner(db: Session, book: BookProject, *, force: bool = False) -> dict:
    out_path = fandeng_full_path(book.book_title)
    if out_path.exists() and not force:
        txt = out_path.read_text(encoding="utf-8")
        return {"status": "exists", "chars": len(txt), "path": str(out_path)}

    sys_p = (Path(__file__).resolve().parents[3] / "config" / "fandeng_fullbook.txt")
    sys_p = sys_p.read_text(encoding="utf-8") if sys_p.exists() else ""
    if len(sys_p) < 200:
        return {"status": "failed", "error": "config/fandeng_fullbook.txt 缺失或过短"}

    mat = _full_material(book)
    if len(mat) < 800:
        # 0925 修: 蒸馏txt在但料仓空 = 旧蒸馏废稿形态 (机械兜底裸行节头实锤) →
        # 指路重蒸馏, 不再误报 "先跑蒸馏"
        _sec_d = Path(__file__).resolve().parents[3] / "data" / "l0" / book.book_title / "sections"
        _n_sec = len([f for f in _sec_d.glob("*.txt") if f.name != "_头部.txt"]) if _sec_d.is_dir() else 0
        _has_txt = bool(book.source_path and Path(book.source_path).exists())
        if _has_txt and _n_sec == 0:
            return {"status": "failed",
                    "error": "蒸馏稿缺节构 (料仓空, 废稿形态) — 书库对本书点「🔄 重蒸馏」重建后再试"}
        return {"status": "failed", "error": f"蒸馏材料过薄 ({len(mat)}字), 先跑蒸馏"}
    chunks = _chunk_material(mat)
    from .creation_common import _llm
    from .compliance_gate import sanitize_material

    t0 = time.time()
    segs: list[str] = []
    seen: list[str] = []          # 已产出段库 (跨段累积, 复读判定基准)
    dedup_log: list[int] = []
    for i, chunk in enumerate(chunks):
        _warn = ""
        for _attempt in (1, 2):   # 复读重试: 首跑丢段率高 → 带警告重写一次 (只重试一轮)
            user = ""
            if segs:  # 续写: 带前文尾部
                user += (f"【前文最后一段原文 (仅供衔接语气 — 它已讲完, 复述其中任何句子=废稿)】\n"
                         f"{segs[-1][-_TAIL_CONTEXT:]}\n\n---\n")
            user += (f"【全书任务】全书共 {len(chunks)} 段讲述, 这是第 {i + 1} 段"
                     f"{' (开篇, 自然开场)' if i == 0 else ''}。本段产出 3500-4500 字。\n\n")
            if _warn:
                user += f"【⚠️ 重写警告】{_warn}\n\n"
            user += f"【本段蒸馏材料】\n{chunk}"
            out = _llm().chat(sys_p, user, model="pro", temperature=0.6, timeout=600)
            out = (out or "").strip()
            if not out:
                return {"status": "failed", "error": f"第 {i + 1} 段输出为空"}
            clean, dropped = _clean_segment(out, seen)
            n_paras = len([x for x in out.split("\n") if x.strip()])
            _ok_dedup = dropped <= max(3, n_paras * 0.3)
            _ok_len = len(clean) >= _SEG_MIN
            if (_ok_dedup and _ok_len) or _attempt == 2:
                if not _ok_len and len(clean) < 1000:
                    # 撞车残稿实锤: seg2 只出 90 字 — 空段落是全书洞, 不落盘
                    return {"status": "failed",
                            "error": f"第 {i + 1} 段重写后仍过短 ({len(clean)}字), 放弃落盘"}
                segs.append(clean)
                dedup_log.append(dropped)
                if dropped > 3 or not _ok_len:
                    logger.warning("[fandeng-full] %s 段%d 异常通过: 丢%d段, 留%d字",
                                   book.book_title, i + 1, dropped, len(clean))
                break
            _why = (f"复读 {dropped}/{n_paras} 段" if not _ok_dedup
                    else f"过短 ({len(clean)}字, 目标3500-4500)")
            _warn = (f"上一稿{_why}, 已作废。"
                     f"本段只讲【本段蒸馏材料】里的新内容, 严禁重复已讲内容, 篇幅写足。")
            logger.warning("[fandeng-full] %s 段%d %s, 重写", book.book_title, i + 1, _why)
        logger.info("[fandeng-full] %s 段%d/%d: %d字 (去复读后)",
                    book.book_title, i + 1, len(chunks), len(segs[-1]))

    full = "\n\n".join(segs)
    safe, _hits = sanitize_material(full, book.book_title, source_path=book.source_path or "")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(safe, encoding="utf-8")
    meta = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "chars": len(safe),
            "segments": len(segs), "seg_chars": [len(s) for s in segs],
            "dedup_dropped": dedup_log,
            "elapsed_min": round((time.time() - t0) / 60, 1)}
    (out_path.parent / "fandeng_full.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("[fandeng-full] %s 全书稿落盘: %d字 %d段 %.1fmin",
                book.book_title, len(safe), len(segs), meta["elapsed_min"])
    return {"status": "ok", **meta, "path": str(out_path)}
