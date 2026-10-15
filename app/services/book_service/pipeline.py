# -*- coding: utf-8 -*-
"""书线一键管线 v2 (0909) — 上传 epub → 确认总纲前全自动, 5 步一键跑完.

  ① 蒸馏:  章级提取+质检 → Gate A 合规 → 编纂+质检 → sections/ 料仓 → input_json 回填
  ② L0:    章节摘要 (Gemma) → facing 八面 → 释放显存
  ③ 视角:  迁移视角 (3~4 个, 按书适配)
  ④ 总纲:  灵魂三问 + 六集 (四模型评审规则全套)
  ⑤ 停机点: 用户确认总纲 → 后续逐集生成 (樊登→老谭)

用法:
  from app.services.book_service.pipeline import run_book_setup
  result = run_book_setup("/path/to/book.epub", db)  # 全自动到总纲
  # 之后用户在讲书页确认总纲 → 逐集生成走既有 episode_gen
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

__all__ = ["run_book_setup", "BookSetupResult", "SETUP_JOBS", "get_setup_state"]


class BookSetupResult(dict):
    """管线结果 (dict 兼容, 属性访问便捷)."""
    @property
    def ok(self) -> bool:
        return self.get("status") == "ok"
    @property
    def book_id(self) -> str | None:
        return self.get("book_id")
    @property
    def outline(self) -> list | None:
        return self.get("episodes")
    @property
    def failed_at(self) -> str | None:
        return self.get("failed_step")
    @property
    def gate_hits(self) -> int:
        return self.get("gate_hits", 0)


# 每书一键管线进度 (books router 读它给前端轮询)
SETUP_JOBS: dict[str, dict] = {}


def get_setup_state(book_id: str) -> dict:
    return SETUP_JOBS.get(book_id, {"status": "idle"})


def run_book_setup(db: Session, epub_path: str, *, create_book: bool = True,
                   on_log=None, book_id: str | None = None) -> BookSetupResult:
    """上传 epub → ①②③④ 全自动 → 总纲落库待用户确认.

    on_log(msg): 可选回调, 每步推送进度 (前端轮询用).
    book_id: create_book=False 时按 id 定位书 (免文件名匹配失败).
    """
    t0 = time.time()

    def log(msg):
        logger.info("[pipeline] %s (%.0fs)", msg, time.time() - t0)
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    from app.models import BookProject
    from .reader import clean_book_title
    book_title = clean_book_title(Path(epub_path).stem)

    def _fail(step: str, exc) -> BookSetupResult:
        logger.error("[pipeline] %s 失败: %s", step, exc)
        return BookSetupResult({"status": "failed", "failed_step": step, "error": str(exc)})

    # ── 前置: 建书 (或取已有) ──
    if create_book:
        book = db.query(BookProject).filter(
            BookProject.book_title == book_title).first()
        if not book:
            book = BookProject(book_title=book_title, status="created",
                              source_path=str(Path(epub_path).resolve()))
            db.add(book)
            db.commit()
            log(f"建书: {book_title} ({book.id[:8]})")
    else:
        book = db.get(BookProject, book_id) if book_id else None
        if not book:
            book = db.query(BookProject).filter(
                BookProject.book_title == book_title).first()
        if not book:
            return BookSetupResult({"status": "failed", "error": "书不存在"})
        book_title = book.book_title

    # ── ① 蒸馏 (纯代码闸门 + Gemma) ──
    # 已蒸馏的书跳过重蒸 (source_path 可能指向 .蒸馏.txt, 蒸蒸馏=废)
    from .distiller import DISTILL_SUFFIX
    from .l0 import _l0_dir
    distilled_txt = _l0_dir(book_title) / f"{book_title}{DISTILL_SUFFIX}.txt"
    already_distilled = distilled_txt.exists() or DISTILL_SUFFIX in (book.source_path or "")
    gate: dict = {}
    if not already_distilled:
        try:
            log("① 蒸馏启动")
            from .distiller import GemmaClient, ensure_gemma, stage_parse, stage_extract, \
                stage_gate_a, stage_compose
            client, started = ensure_gemma(GemmaClient())
            meta = stage_parse(epub_path)
            log(f"① 解析: {meta['n_chapters']} 章 → {meta['n_chunks']} 块, 覆盖率已验")
            while True:
                r = stage_extract(book_title, client=client)
                if r["done"] >= r["total"]:
                    break
            log(f"① 提取: {r['done']} 块 | {r['stats']}")
            gate = stage_gate_a(book_title)
            log(f"① Gate A: 删{gate['deleted']} 改写{gate['rewrite']}")
            out_txt = stage_compose(book_title, client=client)
            log(f"① 编纂: {out_txt}")
            book.source_path = str(Path(epub_path).resolve())
            db.commit()
            _backfill_input_json(db, book)
            log("① input_json 回填完成")
        except Exception as exc:
            return _fail("distill", exc)
    else:
        log("① 跳过蒸馏 (已有蒸馏 txt)")
        _backfill_input_json(db, book)
        log("① input_json 回填完成")

    # ── ② L0 (章节摘要) + facing 八面 — 非阻断; 已有 L0+facing 跳过 (免 17min 重跑) ──
    from .l0 import _l0_dir as _l0d2
    l0_dir2 = _l0d2(book_title)
    l0_done = (l0_dir2 / "l0-chapter-v1.json").exists() and (l0_dir2 / "facing").is_dir()
    if l0_done:
        log("② 跳过 L0/facing (已存在)")
    else:
        try:
            log("② L0/facing 启动")
            from .l0 import run_l0, _l0_dir as _l0d
            from .facing import run_facings
            run_l0(str(Path(epub_path).resolve()))
            run_facings(_l0d(book_title),
                        facings=["kernel", "units", "quotes", "cases",
                                 "compliance", "readers", "hooks", "relations"])
            log("② L0/facing 完成")
        except Exception as exc:
            log(f"② L0/facing 跳过 (非阻断): {exc}")

    # 释放 Gemma 显存
    try:
        from .distiller import shutdown_gemma_all
        shutdown_gemma_all()
        log("② 显存已释放")
    except Exception:
        pass

    # ── ③ 迁移视角 ──
    try:
        log("③ 迁移视角")
        _gen_viewpoints(db, book)
        log("③ 视角完成")
    except Exception as exc:
        log(f"③ 视角跳过: {exc}")

    # ── ③.5 樊登全书稿 (0912 樊登前置架构): 蒸馏一遍成型 → 总编剧按稿切六集 ──
    # 已有稿跳过 (重产走讲书页 🎭 按钮); 失败不阻断总纲 (总编剧无稿走旧路)
    try:
        from .fandeng_full import run_fandeng_full
        _ffr = run_fandeng_full(db, book)
        if _ffr.get("status") in ("ok", "exists"):
            log(f"③.5 樊登全书稿: {_ffr.get('status')} {_ffr.get('chars')}字")
        else:
            log(f"③.5 樊登全书稿跳过 (非阻断): {_ffr.get('error')}")
    except Exception as exc:
        log(f"③.5 樊登全书稿跳过 (非阻断): {exc}")

    # ── ④ 系列总纲 ──
    try:
        log("④ 总纲生成")
        from .series_outline import run_series_outline
        outline_result = run_series_outline(db, book, replace=True)
        if outline_result.get("status") != "ok":
            return _fail("outline", outline_result.get("issues"))
        log(f"④ 总纲: {len(outline_result.get('episodes') or [])} 集落库")
    except Exception as exc:
        return _fail("outline", exc)

    # ── ⑤ 停机点 ──
    book.status = "roadmap_review"
    db.commit()
    log(f"⑤ 完成 ({(time.time()-t0)/60:.0f}min) — 总纲待用户在讲书页确认")

    return BookSetupResult({
        "status": "ok", "book_id": book.id, "book_title": book_title,
        "episodes": outline_result.get("episodes"),
        "gate_hits": gate.get("rewrite", 0),
        "duration_min": round((time.time() - t0) / 60, 1),
    })


def _backfill_input_json(db: Session, book) -> None:
    """从新蒸馏 txt 回填 input_json 核心五字段."""
    from .creation_common import _llm, _parse_json
    txt = Path(book.source_path).read_text(encoding="utf-8", errors="ignore")
    sys_p = """从蒸馏精华稿中提取以下字段, 输出严格 JSON:
    {"全书核心主张": str, "关键概念清单": [str], "核心金句": [str], "核心案例": [str], "章节结构": [str]}
    概念取 10-15 个, 金句取 10 句, 案例取 8 个 (故事库标题), 章节取 6-8 条 (成功路径阶段名)。"""
    data = _parse_json(_llm().chat(sys_p, txt[:12000], model="flash", temperature=0.2))
    ij = dict(book.input_json or {})
    for k in ("全书核心主张", "关键概念清单", "核心金句", "核心案例", "章节结构"):
        v = data.get(k)
        if v:
            ij[k] = {"value": v, "tier": "L0"}
    book.input_json = ij
    db.commit()


def _gen_viewpoints(db: Session, book) -> None:
    """迁移视角: 按书性质产 3~4 个 (老谭侧专属, 静读书跳过)."""
    from .creation_common import _llm, _parse_json
    ij = book.input_json or {}
    if (ij.get("本书迁移视角") or {}).get("value"):
        return  # 已有
    claim = (ij.get("全书核心主张") or {}).get("value", "")
    sys_p = ('你是读书系统的视角规划模块。给定一本书的类型与核心主张, 产出 3~4 个'
             '「把这本书讲给谁听」的迁移视角——既要普惠又要有侧重, 按书的性质适配。'
             '每视角 = 名称 + 一句适用说明。输出严格 JSON: {"views": ["名称:说明", ...]}')
    user = f"书名：《{book.book_title}》\n核心主张：{claim[:200]}"
    vd = _parse_json(_llm().chat(sys_p, user, model="flash", temperature=0.3))
    views = [str(v) for v in ((vd or {}).get("views") or []) if str(v).strip()][:4]
    if views:
        ij["本书迁移视角"] = {"value": views, "tier": "L0"}
        book.input_json = ij
        db.commit()
