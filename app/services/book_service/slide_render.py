# -*- coding: utf-8 -*-
"""bs1 页单渲染服务 (2026-09-08) — plan → 预览 PNG → 单页重排, B 环节核心.

流程位: 稿件 → [本模块] 页单生成+逐页预览渲染 → 人工确认 (bs1_story 工坊) →
render ③④⑤ (ppt_pipeline bs1 分支: TTS→element_pages→jy2 草稿)。

页单产物 = job._JOBS[job_id]["pages"] (与 pptx job 共池, SSE/cancel 复用):
  [{type, narration, fields(分隔符串), img_query, img_cap,
    img_file(workdir 相对), img_pexels_id, png(workdir 相对), est_sec}]
确认后序列化落库 episodes.bs1_pages_json (断电/重开可恢复)。

渲染法: fill_template → 本地 html → playwright 直截静态定格 (与 tools/_shot.py
同法, networkidle+preview-play+settle; 不走 VisualRenderJob→mp4→末帧, 快一个量级)。
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .slide_gen import PagePlan

logger = logging.getLogger(__name__)

__all__ = ["Bs1Context", "build_context", "page_to_dict", "run_bs1_plan",
           "render_bs1_pages", "fetch_page_image", "page_input_data"]


@dataclass
class Bs1Context:
    """页单渲染上下文 (品牌/书信息, 全页共享)."""
    book_id: str
    ep_index: int
    total_eps: int
    book_title: str
    ep_title: str
    brand: str = "老谭读书"
    foot: str = ""
    brand_tag: str = "读一本书，升一级"
    logo_b64: str = ""
    script_text: str = ""


def build_context(db, book_id: str, ep_index: int) -> Bs1Context | None:
    """BookProject+Episode → 渲染上下文 (品牌/logo 复用 ppt_pipeline._book_brand)."""
    from app.models import BookProject, Episode
    from .slide_gen import _cn_num
    book = db.query(BookProject).filter(BookProject.id == book_id).first()
    if not book:
        return None
    ep = db.query(Episode).filter(Episode.book_id == book.id,
                                   Episode.ep_index == ep_index).first()
    if not ep or not ep.script_text:
        return None
    brand, logo = "", ""
    brand_tag = "读一本书，升一级"  # 老谭读书账号级品牌句 (0907)
    try:
        from app.services.ppt_pipeline import _book_brand
        brand, logo = _book_brand(db, book)  # 无 persona 回退静读书 (闸门在 plan 端点拦)
    except Exception as exc:
        logger.warning("[bs1] 品牌解析失败, 回退老谭读书: %s", exc)
        brand, logo = "老谭读书", ""
    try:
        from app.models import Persona
        pid = (book.input_json or {}).get("persona_id")
        if pid:
            p = db.query(Persona).filter(Persona.id == pid).first()
            if p:
                brand = p.brand_name or brand
                brand_tag = p.brand_tag or brand_tag
    except Exception:
        pass
    return Bs1Context(
        book_id=book.id, ep_index=ep.ep_index,
        total_eps=len(book.episodes) or 6,
        book_title=book.book_title or "", ep_title=ep.title or "",
        brand=brand, foot=f"{brand} ·《{book.book_title}》第{_cn_num(ep.ep_index)}集",  # 中文集号 (布局铁律)
        brand_tag=brand_tag, logo_b64=logo,
        script_text=ep.script_text,
    )


# ── PagePlan ↔ job 页 dict ────────────────────────────────────
def page_to_dict(p: PagePlan) -> dict:
    return {"type": p.type, "narration": p.narration, "fields": dict(p.fields),
            "img_query": p.img_query, "img_cap": p.img_cap,
            "img_file": "", "img_pexels_id": 0, "png": "", "est_sec": p.est_sec}


def dict_to_page(pd: dict) -> PagePlan:
    return PagePlan(pd["type"], pd.get("narration", ""),
                    dict(pd.get("fields") or {}), pd.get("img_query", ""),
                    pd.get("img_cap", ""))


def _write_pages(workdir: Path, pages: list[dict], meta: dict | None = None) -> None:
    (workdir / "pages.json").write_text(
        json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf-8")
    # bs1_meta.json (0908): 后端重启 _JOBS 丢失时, state 端点据此恢复未确认 job
    if meta is not None:
        (workdir / "bs1_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def load_meta(workdir: Path) -> dict:
    f = workdir / "bs1_meta.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_pages(workdir: Path) -> list[dict]:
    f = workdir / "pages.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


# ── Pexels 配图 ────────────────────────────────────────────────
def fetch_page_image(query: str, workdir: Path, avoid_ids: set[int] | None = None) -> tuple[str, int]:
    """img_query → Pexels 横图 → workdir/imgs/ 落盘, 返回 (相对路径, pexels_id).

    失败/无结果返回 ("", 0) — 调用方回退纯色底 (模板优雅降级)。
    """
    if not query:
        return "", 0
    try:
        from app.services.pexels_service import pexels_service
        from app.services.pexels_service._http import http_session, search_pexels_photos
        pexels_service._ensure_config()
        pexels_service._ensure_session()
        photos = search_pexels_photos(pexels_service, query, per_page=10,
                                      orientation="landscape")
        avoid = avoid_ids or set()
        for ph in photos:
            pid = int(ph.get("id") or 0)
            if not pid or pid in avoid:
                continue
            src = (ph.get("src") or {}).get("large") \
                or (ph.get("src") or {}).get("large2x") \
                or (ph.get("src") or {}).get("original")
            if not src:
                continue
            resp = http_session(pexels_service).get(src, timeout=(10, 60))
            if resp.status_code != 200:
                continue
            imgs = workdir / "imgs"
            imgs.mkdir(parents=True, exist_ok=True)
            rel = f"imgs/pexels_{pid}.jpg"
            (workdir / rel).write_bytes(resp.content)
            return rel, pid
    except Exception as exc:
        logger.warning("[bs1] Pexels 取图失败 (%s): %s", query, exc)
    return "", 0


def _img_b64(workdir: Path, rel: str) -> str:
    """已落盘图 → data URI (注入模板 img)."""
    if not rel:
        return ""
    f = workdir / rel
    if not f.exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()


# ── 页面渲染 (fill → html → playwright 截图) ──────────────────
def page_input_data(pd: dict, ctx: Bs1Context, workdir: Path) -> dict:
    """job 页 dict → fill_template 的 input_data (字段+品牌注入)."""
    inp: dict = {k: v for k, v in (pd.get("fields") or {}).items()
                 if isinstance(v, (str, int, float))}
    inp["duration_sec"] = pd.get("est_sec") or 6
    inp["foot"] = ctx.foot
    if pd["type"] == "cover":
        inp.setdefault("book", f"拆《{ctx.book_title}》")
        inp["brand_tag"] = ctx.brand_tag
    if pd["type"] == "outro":
        inp["brand_tag"] = ctx.brand_tag
    # 0910 用户令: 页面不烤 logo (cover/outro 都不), 台标一律走剪映草稿 logo 轨
    # (ppt_pipeline watermark 分支) — 页 PNG 干净, logo 可在剪辑里随时增删换位
    img = _img_b64(workdir, pd.get("img_file") or "")
    if img:
        inp["img"] = img
    return inp


async def _shot_pages(items: list[tuple[Path, Path]]) -> None:
    """[(html, png)] → 单浏览器会话批量截图 (networkidle+preview-play+settle)."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        for html_path, out_png in items:
            await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            try:
                await page.evaluate("window.postMessage({type:'preview-play'}, '*')")
            except Exception:
                pass
            await page.wait_for_timeout(1400)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(out_png))
        await browser.close()


def render_bs1_pages(pages: list[dict], ctx: Bs1Context, workdir: Path,
                     only: list[int] | None = None) -> list[int]:
    """逐页 fill_template → html → PNG。only=指定下标 (单页重渲染), None=全量。

    返回成功渲染的下标列表; png 落 workdir/pages/p{idx:02d}.png, html 留档同目录。
    """
    import asyncio
    from app.config import get_config
    from app.services.template_filler import fill_template
    from app.services.template_library import get_template, resolve_template_dir

    cfg = get_config().defaults
    todo: list[int] = []
    for idx in (only if only is not None else range(len(pages))):
        if not (0 <= idx < len(pages)):
            continue
        pd = pages[idx]
        tpl_id = f"bs1-{pd['type']}-v1"
        meta = get_template(tpl_id)
        if not meta:
            logger.warning("[bs1] 模板未注册: %s", tpl_id)
            continue
        try:
            tdir = resolve_template_dir(tpl_id, cfg.hf_template_root)
            ws = fill_template(tdir, tpl_id, page_input_data(pd, ctx, workdir),
                               job_id=f"bs1_{ctx.book_id[:8]}_ep{ctx.ep_index}_p{idx:02d}")
            pdir = workdir / "pages"
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / f"p{idx:02d}.html").write_text(
                (ws / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
            todo.append(idx)
        except Exception as exc:
            logger.warning("[bs1] 页 %d fill 失败: %s", idx, exc)
    if not todo:
        return []
    pairs = []
    for idx in todo:
        pairs.append((workdir / "pages" / f"p{idx:02d}.html",
                      workdir / "pages" / f"p{idx:02d}.png"))
    try:
        asyncio.run(_shot_pages(pairs))
    except Exception as exc:
        logger.warning("[bs1] 截图失败: %s", exc)
        return []
    ok = [idx for idx, (_, png) in zip(todo, pairs) if png.exists()]
    for idx in ok:
        pages[idx]["png"] = f"pages/p{idx:02d}.png"
    return ok


# ── 规划主流程 (后台线程) ──────────────────────────────────────
def run_bs1_plan(job_id: str, book_id: str, ep_index: int) -> None:
    """① PPT生产: A 规划 → Pexels 配图 → 逐页预览渲染 → job status=planned。"""
    from app.services.ppt_pipeline import _evt, _job_workdir, _JOBS
    from app.database import db_session

    from .slide_gen import plan_episode_pages

    db = db_session().__enter__()
    workdir = _job_workdir(job_id)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        ctx = build_context(db, book_id, ep_index)
        if not ctx or not ctx.script_text:
            _evt(job_id, "书的集稿缺失或书不存在", "error", type="bs1_error")
            return
        _evt(job_id, f"页单规划中 (稿 {len(ctx.script_text)} 字, LLM 约 2~5 分钟)…", "info")
        plans = plan_episode_pages(ctx.script_text, ep_index=ctx.ep_index,
                                   total_eps=ctx.total_eps, ep_title=ctx.ep_title,
                                   book_title=ctx.book_title)
        if not plans:
            _evt(job_id, "规划失败: 无页单产出", "error", type="bs1_error")
            return
        pages = [page_to_dict(p) for p in plans]
        _evt(job_id, f"页单 {len(pages)} 页生成, 开始配图 (Pexels)…", "ok")

        # 配图: 按 img_query 分组拉一次 (同词页共享), cover/chapter/outro 必配
        q_map: dict[str, tuple[str, int]] = {}
        for i, pd in enumerate(pages, 1):
            q = pd.get("img_query") or ""
            need = q and (pd["type"] in ("cover", "chapter", "outro") or not pd.get("img_file"))
            if not need:
                continue
            if q in q_map:
                rel, pid = q_map[q]
            else:
                _evt(job_id, f"取图 {i}/{len(pages)}: {q}", "info", progress=f"img {i}/{len(pages)}")
                rel, pid = fetch_page_image(q, workdir)
                q_map[q] = (rel, pid)
            pd["img_file"], pd["img_pexels_id"] = rel, pid

        _evt(job_id, "逐页渲染预览 PNG…", "info")
        ok = render_bs1_pages(pages, ctx, workdir)
        _write_pages(workdir, pages, meta={
            "job_id": job_id, "book_id": book_id, "ep_index": ep_index,
            "status": "planned", "ep_title": ctx.ep_title,
        })
        from app.services.ppt_pipeline import _JOBS
        _JOBS.setdefault(job_id, {}).update(
            mode="bs1", book_id=book_id, ep_index=ep_index,
            pages=pages, status="planned",
        )
        n_img = sum(1 for p in pages if p.get("img_file"))
        _evt(job_id, f"页单就绪: {len(pages)} 页 (渲染 {len(ok)}, 配图 {n_img}) — 请逐页确认",
             "ok", type="bs1_planned")
    except Exception as exc:
        logger.exception("[bs1] plan 失败")
        _JOBS.setdefault(job_id, {})['status'] = 'failed'  # 0910: 不标则永久卡 planning → SSE 重连风暴
        _evt(job_id, f"规划异常: {exc}", "error", type="bs1_error")
    finally:
        db.close()
