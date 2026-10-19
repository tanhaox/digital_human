# -*- coding: utf-8 -*-
"""拆书项目端点 (2026-08-19) — 5 步创作流 + 级联重跑 + 挂车清单.

方案: docs/design/拆书项目-实施方案.md。同步端点 (P1): 单书人工确认节奏, 无需异步队列。
"""
from __future__ import annotations

import json
import logging
from datetime import timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_config
from app.database import get_db
from app.models import BookProject, Episode
from app.models.base import _now
from app.services.book_service import orchestrator as orch
from app.services.book_service.persona import ensure_book_account
from app.services.book_service.reader import scan_book_sources

from ._sse import sse_stream

logger = logging.getLogger(__name__)

router = APIRouter(tags=["books"])

__all__ = ["router"]


class BookCreate(BaseModel):
    book_title: str
    author: str | None = None
    publisher: str | None = None
    isbn: str | None = None
    cart_url: str | None = None
    cover_url: str | None = None
    selling_point: str | None = None
    source_path: str | None = None  # L0 精华/原书; 空则 UI 从书库选
    source_url: str | None = None  # 知海书页: 爬元数据/简介/目录当 L1
    # 拆书人物即账号 (2026-09-07 双人物): 选定 = 人设模板+音色+品牌;
    # 落 input_json.persona_id, 逐集生成/进产线/尾卡品牌/音色全链消费
    persona_id: str | None = None


class BookRename(BaseModel):
    title: str


class EpisodeEdit(BaseModel):
    script_text: str | None = None
    title: str | None = None
    target_duration_sec: float | None = None


class ProduceRequest(BaseModel):
    """进产线请求: 可选指定人设 (人物即账号)."""
    persona_id: str | None = None
    # 强制重跑 (2026-08-20): 删旧 Script/segments 重建 — 发现错误/换新产线时用
    force: bool = False
    # TTS 先行试听 (0912 用户令): 草稿态即可预产音频供人耳验收 (稿→音→确认);
    # 稿已变则自动重建 segments, 未变复用现 Script
    preview: bool = False



_SETUP_JOBS: dict = {}


@router.post("/books/{book_id}/fandeng-full")
def fandeng_full(book_id: str, body: dict | None = None, db: Session = Depends(get_db)):
    """樊登全书稿 (0912 樊登前置架构): 蒸馏一遍成型全书讲述, 分段续写, 2-5min.

    总编剧吃它 (编段落号) 输出每集樊登切片; 逐集生成拐棍=切片 (零 LLM 调用)。
    force=true 重产。已有稿时幂等返回 exists。
    """
    b = _book_or_404(db, book_id)
    from app.services.book_service.fandeng_full import run_fandeng_full
    r = run_fandeng_full(db, b, force=bool((body or {}).get("force")))
    if r.get("status") == "failed":
        raise HTTPException(500, r.get("error") or "樊登全书稿失败")
    return r


@router.get("/books/{book_id}/fandeng-full")
def fandeng_full_text(book_id: str, db: Session = Depends(get_db)):
    """樊登全书稿原文 (纯文本, 页面 📖 按钮新标签打开; 存 txt 不入库)."""
    b = _book_or_404(db, book_id)
    from app.services.book_service.fandeng_full import load_fandeng_full
    txt = load_fandeng_full(b.book_title)
    if not txt:
        raise HTTPException(404, "樊登全书稿不存在, 先点 🎭 生成")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(txt, media_type="text/plain; charset=utf-8")


@router.post("/books/{book_id}/episodes/{n}/hook-variants")
def hook_variants(book_id: str, n: int, db: Session = Depends(get_db)):
    """钩子段多版×KPI分化 (0913 机制⑤⑦): 复用最新留档的 03_user 全料,
    一次产 3 版各标 KPI 轴 (收藏型/评论型/共鸣型), 人工选版 = 用户网页版选择器角色产线化。
    """
    import re as _re
    b = _book_or_404(db, book_id)
    ep = _ep_or_404(db, book_id, n)
    root = Path(__file__).resolve().parents[1] / "outputs" / "拆书"
    bdir = _re.sub(r'[\\/:*?"<>|\s]+', "_", b.book_title).strip("_") or "unnamed"
    gens = sorted((root / bdir / f"ep{n}").glob("gen_*")) if (root / bdir / f"ep{n}").exists() else []
    if not gens or not (gens[-1] / "03_user.txt").exists():
        raise HTTPException(400, "该集无生成留档, 先跑一次「生成 (pro)」")
    user_p = (gens[-1] / "03_user.txt").read_text(encoding="utf-8")
    from app.services.book_service.creation_common import (
        _llm,
        _parse_json,
        elastic_labels,
    )
    from app.services.book_service.feed_registry import load_ban_list
    h = elastic_labels(ep.target_duration_sec)[0]
    cap = int((h[1] - h[0]) * 4.8)
    v_sys = (
        "你是顶级短视频编导。只写钩子段 (第一个时间标签段的内容), 一次产出 3 版, 按 KPI 分化:\n"
        "A收藏型 — 判定动作/口诀前置, 让人想截屏 (工具感最强);\n"
        "B评论型 — 职场共鸣争议钩, 让人忍不住站队说话 (从读者共鸣料取场景);\n"
        "C共鸣型 — 单一人群场景深扎, 只对一类人说话 (从层级清单取一类)。\n"
        f"每版结构: 断言(狠话无弱化词)→锤连击(数字+结局双要素, 只许观众世界的数字)→"
        f"认领收窄→悬念收口(断言式半截话, 禁猜谜问句)。每版 ≤{cap}字。\n"
        "铁律: 钩子段零书世界 (书名/书里/书中/《作品名》全禁); 禁弱化词(可能/也许/大概/搞不好/说不定);"
        "数字锤离谱具体带参照物; 概念名最多一个。"
        + load_ban_list()
        + '\n\n输出严格 JSON (无围栏): {"variants":[{"kpi":"收藏型","axis":"判定表口诀前置",'
          '"text":"..."},{"kpi":"评论型","axis":"...","text":"..."},{"kpi":"共鸣型","axis":"...","text":"..."}]}'
    )
    raw = _llm().chat(v_sys, user_p, model="pro", temperature=0.8)
    data = _parse_json(raw) or {}
    variants = [v for v in (data.get("variants") or []) if (v.get("text") or "").strip()]
    if not variants:
        raise HTTPException(500, "钩子三版产出不可解析")
    try:
        (gens[-1] / "08_hook_variants.json").write_text(
            json.dumps({"cap": cap, "variants": variants}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    except Exception:
        pass
    return {"cap": cap, "variants": variants, "gen_dir": str(gens[-1])}


@router.post("/books/{book_id}/setup-full")
def setup_full(book_id: str, db: Session = Depends(get_db)):
    """一键管线 (0909): 蒸馏(跳过已蒸)→L0→视角→总纲 全自动, 后台跑, 不阻塞 HTTP.

    返回 setup_id; 轮询 GET /books/{book_id}/setup-status 看进度;
    完成后 status=ok 且总纲落库待确认。
    """
    import threading
    book = _book_or_404(db, book_id)
    if not book.source_path:
        raise HTTPException(400, "书无源文件 (source_path 空)")
    from app.services.book_service import pipeline as _pipe
    if _pipe.SETUP_JOBS.get(book_id, {}).get("status") == "running":
        raise HTTPException(409, "该书管线正在跑")
    _pipe.SETUP_JOBS[book_id] = {"status": "running", "steps": []}

    def _on_log(msg):
        _pipe.SETUP_JOBS.setdefault(book_id, {"status": "running", "steps": []})
        _pipe.SETUP_JOBS[book_id]["steps"].append(msg)
        # 只保留最近 60 步防内存涨
        _pipe.SETUP_JOBS[book_id]["steps"] = _pipe.SETUP_JOBS[book_id]["steps"][-60:]

    def _run():
        from app.database import db_session
        from app.services.book_service.pipeline import run_book_setup
        try:
            with db_session() as db2:
                result = run_book_setup(db2, book.source_path, create_book=False,
                                        on_log=_on_log, book_id=book_id)
            _pipe.SETUP_JOBS[book_id] = dict(result, steps=_pipe.SETUP_JOBS.get(book_id, {}).get("steps", []))
        except Exception as exc:
            _pipe.SETUP_JOBS[book_id] = {"status": "failed", "error": str(exc),
                                         "steps": _pipe.SETUP_JOBS.get(book_id, {}).get("steps", [])}

    threading.Thread(target=_run, daemon=True).start()
    return {"setup_id": book_id, "status": "running"}


@router.get("/books/{book_id}/setup-status")
def setup_status(book_id: str):
    from app.services.book_service import pipeline as _pipe
    return _pipe.get_setup_state(book_id)

class RoadmapRow(BaseModel):
    """总纲单行: 可编辑字段 (与 build_roadmap 生成结构对齐; 0908 加秘籍/卖点)."""
    ep: int
    主题: str = ""
    本集秘籍: str = ""
    对应书中内容: str = ""
    核心任务: str = ""
    卖点: str = ""
    承上: str = ""
    启下: str = ""
    概念: list[str] = []
    # 构件/拆解三步 = 导演层字段 (总纲生成时落库), 讲书页表格不编辑不提交 —
    # 0913b 修: 从 update 映射摘除, 防 UI 全量保存时把空串覆写上去 (构件曾被此路径抹掉)


class RoadmapUpdate(BaseModel):
    """总纲批量编辑请求 (6 集全量提交)."""
    episodes: list[RoadmapRow]


def _book_or_404(db: Session, book_id: str) -> BookProject:
    b = db.get(BookProject, book_id)
    if not b:
        raise HTTPException(404, f"书不存在: {book_id}")
    return b


def _ep_or_404(db: Session, book_id: str, n: int) -> Episode:
    ep = db.query(Episode).filter(
        Episode.book_id == book_id, Episode.ep_index == n).first()
    if not ep:
        raise HTTPException(404, f"第 {n} 集不存在")
    return ep


@router.get("/book-sources")
def list_book_sources():
    """书库目录扫描 (精华/原书/蒸馏, 文件名=书名)."""
    from app.services.book_service import distiller
    srcs = scan_book_sources(get_config().defaults.book_source_dir)
    for s in srcs:
        s["kind"] = ("distilled" if distiller.DISTILL_SUFFIX in s["filename"]
                     else "full" if (s["ext"] == "epub" or s["size"] > distiller._FULL_TEXT_MIN * 3)
                     else "essence")
    return {"sources": srcs}


# ── 离线蒸馏 (本地 Qwen3.6 批量, 批次结束全杀腾卡) ────────────────
@router.get("/distill/pending")
def distill_pending():
    from app.services.book_service import distiller
    return {"pending": distiller.pending_distill_books(
        scan_book_sources(get_config().defaults.book_source_dir))}


@router.post("/distill/start")
def distill_start():
    from app.services.book_service import distiller
    try:
        distiller.start_batch(get_config().defaults.book_source_dir)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"started": True}


@router.post("/distill/start-single")
def distill_start_single(path: str, force: bool = False):
    """单本书蒸馏 (2026-08-20): 拉起本地 Qwen3.6 对指定书蒸馏.

    路径限定 book_source_dir 内防穿越; 该 book 须是可蒸馏形态 (全书/大txt 且无 .蒸馏).
    force=true (0925 修): 已有蒸馏稿也重跑 — 旧稿废 (节构缺失) 时 UI「重蒸馏」用;
    前端 confirm 已警示覆盖.
    """
    from app.services.book_service import distiller
    root = Path(get_config().defaults.book_source_dir).resolve()
    p = Path(path).resolve()
    if p.parent != root or not p.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    # 校验可蒸馏: 复用 pending_distill_books 判定 (全书/大txt 且无同名 .蒸馏)
    from app.services.book_service.reader import scan_book_sources
    matching = [s for s in scan_book_sources(root) if s["path"] == str(p)]
    if not matching:
        raise HTTPException(404, f"书库中无此文件: {path}")
    if not force and not distiller.pending_distill_books(matching):
        raise HTTPException(409, "该书已有蒸馏精华, 或不是全书形态")
    try:
        distiller.start_single(p, str(root))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"started": True, "book": p.stem}


@router.get("/distill/status")
def distill_status():
    from app.services.book_service import distiller
    return distiller.batch_status()


@router.post("/distill/clear")
def distill_clear():
    """清空蒸馏状态/事件缓冲 (2026-08-20): 前端确认完成后恢复页面."""
    from app.services.book_service import distiller
    try:
        distiller.clear_state()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"cleared": True}


@router.get("/distill/events")
async def distill_events(request: Request):
    """SSE: 蒸馏批次关键节点实时推送 (启动/每书/每章/完成/腾卡)."""
    from fastapi.responses import StreamingResponse

    from app.services.director_events import subscribe, unsubscribe

    def _heartbeat() -> str:
        return f"data: {json.dumps({'level': 'info', 'msg': '…'}, ensure_ascii=False)}\n\n"

    queue = subscribe("distill")
    return StreamingResponse(
        sse_stream(
            request, queue,
            heartbeat=_heartbeat,
            timeout=20.0,
            terminal_types=("distill_done", "distill_error"),
            on_close=lambda: unsubscribe("distill", queue),
        ),
        media_type="text/event-stream",
    )


@router.get("/book-sources/leaderboard")
def source_leaderboard():
    """源头书引用榜 (2026-08-23): 跨书汇总所有源头书/理论/人物, 按被引用广度排序.

    数据来自 reference_library.leaderboard. book 类型标注 in_library (已入库/可拆候选).
    """
    from app.services.book_service.reference_library import leaderboard
    return {"board": leaderboard()}


@router.get("/book-sources/ready")
def ready_book_sources():
    """可建书的书源 (2026-09-07 修正): 有蒸馏 txt 即入列 — 恢复原设计意图
    "蒸馏合格就出现在下拉"。L0/facing 由建书流程自动补跑 (POST /books 的
    _run_prepare 后台线程 + 前端轮询 /prep), 不该是下拉的前置条件 —
    2026-08-23 加 _book_l0_ready 过滤后, 只有蒸馏 txt 的书全被挡, 补跑路径
    成死代码 (只剩蛤蟆一本可建, 用户实锤)。
    """
    from app.services.book_service import distiller
    from app.services.book_service.reader import clean_book_title
    out = []
    for s in scan_book_sources(get_config().defaults.book_source_dir):
        if distiller.DISTILL_SUFFIX not in s["filename"]:
            continue
        title = clean_book_title(Path(s["filename"]).stem).replace(".蒸馏", "").strip()
        out.append({"path": s["path"], "book_title": title,
                    "l0_ready": _book_l0_ready(title)})
    return {"ready": out}


@router.get("/book-sources/pool")
def source_pool():
    """源头书选题池 (2026-08-23): 已拆书引用的源头书里, 书库缺失者 → 下一批拆书候选.

    数据来自 reference_library.selection_pool (data/l0/selection_pool.json).
    排序: 被几本书引用 → 引用章数; 候选补电子书进 book_source_dir 即可蒸馏开拆.
    """
    from app.services.book_service.reference_library import selection_pool
    return {"pool": selection_pool()}


@router.get("/book-sources/preview")
def book_source_preview(filename: str):
    """书库文件预览 (前 1500 字). 路径限定 book_source_dir 防穿越."""
    root = Path(get_config().defaults.book_source_dir).resolve()
    p = (root / filename).resolve()
    if p.parent != root or not p.exists():
        raise HTTPException(404, f"文件不存在: {filename}")
    return {"filename": filename, "preview": p.read_text(encoding="utf-8", errors="replace")[:1500]}


def _book_l0_ready(book_title: str) -> bool:
    """全流程蒸馏就绪: L0 章节 + 全部 facing 面向齐 (新书下拉/建书判定)."""
    from app.services.book_service.facing import FACINGS
    from app.services.book_service.l0 import _l0_dir
    d = _l0_dir(book_title)
    if not (d / "l0-chapter-v1.json").exists():
        return False
    return all((d / "facing" / spec["file"]).exists() for spec in FACINGS.values())


def _book_source_file(book_title: str) -> str | None:
    """书库里该书名对应的原始书源 (epub/txt/md, 排除蒸馏txt) — L0 要全文本.

    目录名来自 clean_book_title(文件名stem), 蒸馏txt 会带 .蒸馏 后缀派生错目录, 故必须用原书.
    """
    from app.services.book_service.reader import clean_book_title, scan_book_sources
    root = Path(get_config().defaults.book_source_dir)
    base = book_title.strip("《》 \t\r\n")
    for s in sorted(scan_book_sources(root), key=lambda x: -(x.get("mtime") or 0)):
        if s.get("ext") not in ("txt", "md", "epub"):
            continue
        if "蒸馏" in s["filename"]:
            continue
        clean = clean_book_title(Path(s["filename"]).stem).strip("《》 \t\r\n")
        if clean == base or (len(base) >= 4 and base in clean) or (len(clean) >= 4 and clean in base):
            return s["path"]
    return None


def _prepare_l0_facing(book_title: str) -> dict:
    """补跑缺失的 L0+facing (2026-08-23).

    蒸馏txt 只表示 distiller 出过精华稿; L0/facing 是独立步骤, 缺了 auto_fill 静默空.
    返回 {"ran":[...], "note":str|None}.
    """
    from app.services.book_service.facing import FACINGS, run_facings
    from app.services.book_service.l0 import _l0_dir, run_l0
    d = _l0_dir(book_title)
    l0p = d / "l0-chapter-v1.json"
    ran: list[str] = []
    try:
        if not l0p.exists():
            src = _book_source_file(book_title)
            if not src:
                return {"ran": ran, "note": "书库找不到原始书源(epub/txt), 无法补跑 L0"}
            run_l0(src)
            ran.append("l0")
        facing_dir = d / "facing"
        missing = [name for name, spec in FACINGS.items()
                   if not (facing_dir / spec["file"]).exists()]
        if missing:
            run_facings(d, facings=missing)
            ran.append(f"facing({','.join(missing)})")
    except Exception as exc:
        logger.exception("[book] L0/facing 补跑失败")
        return {"ran": ran, "note": f"补跑失败: {exc}"}
    return {"ran": ran, "note": None}


# ── 建书后台准备 (2026-08-23): L0+facing 未就绪时后台补跑, 前端轮询 /prep ──
import threading as _threading

_PREP: dict[str, dict] = {}  # book_id -> {status, step, note, ran}


def _attach_douban(db: Session, book: BookProject) -> dict:
    """豆瓣高赞短评+书评 → input_json.douban (2026-08-23). 秒级 HTTP, 失败静默.

    短评=真实读者情绪/共识; 书评=笔记/深入分析. 搜不到的书优雅降级.
    """
    try:
        from app.services.book_service.douban import book_highlights
        r = book_highlights(book.book_title, limit=10)
        if not r.get("subject"):
            return {"status": "not_found"}
        inp = dict(book.input_json or {})
        inp["douban"] = {
            "subject": r["subject"],
            "comments": (r.get("comments") or [])[:10],
            "reviews": [{"title": v.get("title", ""), "author": v.get("author", ""),
                         "votes": v.get("votes", 0), "summary": v.get("summary", ""),
                         "body": (v.get("body") or "")[:2000]} for v in (r.get("reviews") or [])],
        }
        book.input_json = inp
        db.commit()
        return {"status": "ok", "comments": len(inp["douban"]["comments"]),
                "reviews": len(inp["douban"]["reviews"])}
    except Exception as exc:
        logger.warning("[book] 豆瓣抓取失败: %s", exc)
        return {"status": "error", "note": str(exc)}


def _extract_cover(book_title: str) -> dict:
    """书皮提取 (0925 用户令): 建书即提 epub 封面 — 开场前5s素材, 提前暴露提错.

    幂等 (缓存 00_本书_*.jpg 存在即返); 书库无 epub/提不出 → ok=False, 不阻断建书.
    """
    from app.services.anim_pipeline import covers as _covers
    try:
        p = _covers.extract_book_cover(book_title)
    except Exception as exc:  # noqa: BLE001 — 提取意外不炸建书链
        logger.warning("[book] 书皮提取失败 %s: %s", book_title, exc)
        p = None
    return {"ok": bool(p), "path": str(p) if p else None}


def _run_prepare(book_id: str, book_title: str) -> None:
    from app.database import _session_maker
    try:
        _PREP[book_id]["step"] = "l0"
        prep = _prepare_l0_facing(book_title)
        if prep.get("note"):
            _PREP[book_id].update(status="error", note=prep["note"], ran=prep["ran"])
            return
        _PREP[book_id].update(ran=prep["ran"])
        _PREP[book_id]["step"] = "fill"
        with _session_maker() as db:
            b = db.get(BookProject, book_id)
            if b:
                orch.auto_fill_from_l0(db, b)
                _attach_douban(db, b)  # 2026-08-23: auto_fill 后抓豆瓣(防被 input_json 覆盖)
        _PREP[book_id]["step"] = "cover"
        if _extract_cover(book_title)["ok"]:
            _PREP[book_id]["ran"].append("cover")
        _PREP[book_id].update(status="ready", note=None)
    except Exception as exc:
        logger.exception("[book] 建书准备任务失败 %s", book_id)
        _PREP[book_id].update(status="error", note=str(exc))
    finally:
        _PREP[book_id]["step"] = None


@router.post("/books")
def create_book(body: BookCreate, db: Session = Depends(get_db)):
    ensure_book_account(db)  # 幂等建书账号人设
    # 人物即账号 (2026-09-07): persona_id 落 input_json, 逐集生成/进产线/
    # 尾卡品牌/音色全链消费; 未选回退静读书 (既有行为)
    if body.persona_id:
        from app.models import Persona as _P
        if not db.query(_P).filter(_P.id == body.persona_id).first():
            raise HTTPException(404, f"人物不存在: {body.persona_id}")
    book = BookProject(**body.model_dump(exclude={"persona_id"}, exclude_none=True))
    book.input_json = {"persona_id": body.persona_id} if body.persona_id else {}
    db.add(book)
    db.commit()
    db.refresh(book)
    # 2026-08-23: L0+facing 就绪 → 同步 auto_fill (快); 未就绪 → 后台补跑, 前端轮询 /prep
    if _book_l0_ready(book.book_title):
        try:
            filled = orch.auto_fill_from_l0(db, book)
        except Exception as exc:
            logger.warning("[book] 创建后自动填充失败: %s", exc)
            filled = []
        # 作者兜底: 前端不再手填, 从 L0 meta 自动带
        if not book.author:
            try:
                from app.services.book_service.l0 import _l0_dir
                m = (json.loads((_l0_dir(book.book_title) / "l0-chapter-v1.json")
                                 .read_text(encoding="utf-8")) or {}).get("meta") or {}
                if m.get("author"):
                    book.author = m["author"]
                    db.commit()
            except Exception:
                pass
        douban_res = _attach_douban(db, book)  # 2026-08-23: auto_fill 后抓豆瓣高赞
        cover = _extract_cover(book.book_title)  # 0925: 建书即提书皮 (开场素材前移)
        return {"id": book.id, "status": book.status,
                "prep": {"status": "ready", "step": None, "note": None,
                         "ran": ["cover"] if cover["ok"] else [], "filled": filled},
                "douban": douban_res, "cover": cover}
    _PREP[book.id] = {"status": "preparing", "step": "l0", "note": "L0/facing 蒸馏中…", "ran": []}
    _threading.Thread(target=_run_prepare, args=(book.id, book.book_title), daemon=True).start()
    return {"id": book.id, "status": book.status,
            "prep": {"status": "preparing", "step": "l0", "note": "L0/facing 蒸馏中…", "ran": []}}


@router.post("/books/{book_id}/rename")
def rename_book(book_id: str, body: BookRename, db: Session = Depends(get_db)):
    """改名 (1018 用户令): 只改 book_title, 同步迁移按书名 key 的磁盘产物防孤岛化.

    覆盖: L0蒸馏 / 拆书留档 / 封面缓存 / 动画ep树(outputs/动画/书名) /
    _资产(圣经·品牌卡·开场白·角色设计·风格选型·负词台账) / shots.json·board.json
    顶层 book_title 字段 (save_doc 靠它回写路径)。书库源文件不动 — 匹配是子串容错。
    ① 简单对失败 → 回滚已动; ② 动画侧失败 → 逆移已动项 (回收站项不逆, 重生成即可)。
    """
    import re as _re

    from app.services.anim_pipeline.covers import _safe_title as _cover_safe
    from app.services.anim_pipeline.covers import book_cover_path
    from app.services.anim_pipeline.shots import _safe as _anim_safe
    from app.services.anim_pipeline.shots import anim_output_root as _anim_root
    from app.services.book_service.l0 import _l0_dir

    b = _book_or_404(db, book_id)
    title = body.title.strip().strip("《》").strip()
    old = b.book_title
    if not title:
        raise HTTPException(400, "书名不能为空")
    if title == old:
        return {"book_title": old, "moved": [], "note": "书名未变"}
    if _re.search(r'[\\/:*?"<>|]', title):
        raise HTTPException(400, "书名不能含 \\ / : * ? \" < > | 字符")
    dup = (db.query(BookProject)
             .filter(BookProject.book_title == title, BookProject.id != book_id).first())
    if dup:
        raise HTTPException(409, f"已有同名书《{title}》")

    def _bdir(t: str) -> str:  # 同留档写盘规则 episode_gen.py:1048 _book_dir
        return _re.sub(r'[\\/:*?"<>|\s]+', "_", t).strip("_") or "unnamed"

    chai_root = Path(__file__).resolve().parents[2] / "outputs" / "拆书"  # 项目根; 写盘在 episode_gen.py:1049 (parents[3] 同义)

    # ── ① 简单对: 整目录/文件 rename, 失败全回滚 ──
    simple = [(_l0_dir(old), _l0_dir(title), "L0蒸馏"),
              (chai_root / _bdir(old), chai_root / _bdir(title), "拆书留档"),
              (book_cover_path(old), book_cover_path(title), "封面缓存")]
    moved: list[str] = []
    undo: list[tuple[Path, Path]] = []
    try:
        for old_p, new_p, note in simple:
            if not old_p.exists() or old_p == new_p:  # 后者: 仅空格等差异致 safe 名相同
                continue
            if new_p.exists():
                raise FileExistsError(f"目标已存在: {new_p}")
            old_p.rename(new_p)
            undo.append((new_p, old_p))
            moved.append(note)
    except OSError as exc:
        for new_p, old_p in undo:  # 回滚已动目录, DB 不动
            try:
                new_p.rename(old_p)
            except OSError:
                logger.error("[book] 改名回滚失败, 需手工处理: %s → %s", new_p, old_p)
        logger.warning("[book] 改名目录迁移失败 %s → %s: %s", old, title, exc)
        raise HTTPException(500, f"目录迁移失败, 已回滚: {exc}") from exc

    # ── ② 动画侧 (1018b 补, 首版漏迁致 anim 页全空实锤): ep树合并 + _资产改名 + 文档字段重写 ──
    anim_moves: list[tuple[Path, Path]] = []

    def _merge_tree(src: Path, dst: Path) -> None:
        """旧ep树并入新ep树; 同名冲突留 mtime 新的一方, 旧方进回收站."""
        from send2trash import send2trash

        dst.mkdir(parents=True, exist_ok=True)
        for it in list(src.iterdir()):
            tgt = dst / it.name
            if it.is_dir():
                if tgt.exists():
                    _merge_tree(it, tgt)
                else:
                    it.rename(tgt)
                    anim_moves.append((it, tgt))
            elif tgt.exists() and tgt.stat().st_mtime >= it.stat().st_mtime:
                send2trash(str(it))
            else:
                if tgt.exists():
                    send2trash(str(tgt))
                it.rename(tgt)
                anim_moves.append((it, tgt))

    # 三种书名键 (raw / shots._safe / covers._safe_title): 同名文件名空间不同, 全覆盖
    repl: dict[str, str] = {}
    for _o, _n in ((old, title), (_anim_safe(old), _anim_safe(title)),
                   (_cover_safe(old), _cover_safe(title))):
        if _o and _o != _n:
            repl[_o] = _n

    def _rename_keyed(tree: Path) -> int:
        """树内文件名含旧键 → 新键 (still_mp4/wall_scroll 衍生件等, 1018d 实锤)."""
        keyed = 0
        if not tree.is_dir():
            return keyed
        for it in tree.rglob("*"):
            if not it.is_file():
                continue
            for o in sorted(repl, key=len, reverse=True):
                if o in it.name:
                    tgt = it.parent / it.name.replace(o, repl[o])
                    if not tgt.exists():
                        it.rename(tgt)
                        anim_moves.append((it, tgt))
                        keyed += 1
                    break
        return keyed

    def _rename_assets(root: Path) -> int:
        """/_资产(+book_covers/风格选型/角色设计) 子目录: 名字含旧书名键 → 新名."""
        hits = 0
        for base in (root / "_资产", root / "_资产" / "风格选型",
                     root / "_资产" / "角色设计", root / "_资产" / "book_covers"):
            if not base.is_dir():
                continue
            for it in list(base.iterdir()):
                for o in sorted(repl, key=len, reverse=True):
                    if o in it.name:
                        tgt = base / it.name.replace(o, repl[o])
                        if not tgt.exists():
                            it.rename(tgt)
                            anim_moves.append((it, tgt))
                            hits += 1
                        break
        return hits

    def _rewrite_doc_titles(book_dir: Path, root: Path) -> int:
        """shots.json / 风格board.json: 顶层 book_title + 路径值里的旧书名 → 新名.
        _book_title 历史戳不动; save 靠 book_title 回写路径, 品牌镜 image/video_file 是
        ../../_资产/ 相对路径 — 1018c 品牌镜空白实锤. 序列化对齐 shots.save (indent=2)."""
        files = sorted(book_dir.glob("ep*/shots.json"))
        board = root / "_资产" / "风格选型" / _anim_safe(title) / "board.json"
        if board.exists():
            files.append(board)

        def _fix(o, key: str = ""):
            if isinstance(o, dict):
                return {k: (v if k == "_book_title" else _fix(v, k)) for k, v in o.items()}
            if isinstance(o, list):
                return [_fix(v, key) for v in o]
            if isinstance(o, str) and old in o and ("/" in o or "\\" in o):
                return o.replace(old, title)
            return o

        n = 0
        for f in files:
            doc = json.loads(f.read_text(encoding="utf-8"))
            doc2 = _fix(doc)
            if doc.get("book_title") == old:
                doc2["book_title"] = title
            if doc2 != doc:
                f.write_text(json.dumps(doc2, ensure_ascii=False, indent=2), encoding="utf-8")
                n += 1
        return n

    anim_root = _anim_root()
    old_book_dir = anim_root / _anim_safe(old)
    new_book_dir = anim_root / _anim_safe(title)
    try:
        if old_book_dir.is_dir() and old_book_dir != new_book_dir:
            _merge_tree(old_book_dir, new_book_dir)
            moved.append("动画ep树")
            if not any(p.is_file() for p in old_book_dir.rglob('*')):  # 只剩空壳 → 整壳回收; 有文件(被占用)留下不阻断
                from send2trash import send2trash

                send2trash(str(old_book_dir))
        if new_book_dir.is_dir() and _rename_keyed(new_book_dir):
            moved.append("旧键文件")
        if _rename_assets(anim_root):
            moved.append("动画资产")
        if _rewrite_doc_titles(new_book_dir, anim_root):
            moved.append("文档字段")
    except Exception as exc:  # noqa: BLE001 — 逆移是尽力而为, 统一转 500
        for src_p, dst_p in reversed(anim_moves):
            try:
                if dst_p.exists() and not src_p.exists():
                    dst_p.rename(src_p)
            except OSError:
                logger.error("[book] 改名逆移失败, 需手工处理: %s → %s", dst_p, src_p)
        for new_p, old_p in undo:  # 简单对一并回滚
            try:
                new_p.rename(old_p)
            except OSError:
                logger.error("[book] 改名回滚失败, 需手工处理: %s → %s", new_p, old_p)
        logger.warning("[book] 改名动画侧迁移失败 %s → %s: %s", old, title, exc)
        raise HTTPException(500, f"动画产物迁移失败, 已尽力回滚: {exc}") from exc

    # ── ③ 剪映草稿 (1018d): 明文稿双键修路径+目录改名; 加密稿跳过报备 (需重装配) ──
    import os

    drafts: dict[str, list[str]] = {"patched": [], "encrypted": []}
    drafts_dir = (Path(os.environ.get("LOCALAPPDATA", "")) / "JianyingPro"
                  / "User Data" / "Projects" / "com.lveditor.draft")
    if drafts_dir.is_dir():
        try:
            for fold in list(drafts_dir.iterdir()):
                if not fold.is_dir() or old not in fold.name:
                    continue
                content = fold / "draft_content.json"
                if not (content.exists() and content.read_text(
                        encoding="utf-8", errors="ignore").lstrip().startswith("{")):
                    drafts["encrypted"].append(fold.name)  # 剪映加密稿改不了 → 重装配
                    continue
                for fname in ("draft_content.json", "draft_meta_info.json"):
                    fp = fold / fname
                    if not fp.exists():
                        continue
                    raw = fp.read_text(encoding="utf-8")
                    if any(o in raw for o in repl):
                        for o in sorted(repl, key=len, reverse=True):
                            raw = raw.replace(o, repl[o])
                        fp.write_text(raw, encoding="utf-8")
                tgt = drafts_dir / fold.name.replace(old, title)
                if not tgt.exists():
                    fold.rename(tgt)  # 剪映占用时炸 → 下面兜住, 内容已修
                drafts["patched"].append(tgt.name)
        except OSError as exc:  # 剪映开着握句柄等 — 草稿层尽力而为, 不阻断改名
            logger.warning("[book] 改名剪映草稿迁移未完 %s → %s: %s", old, title, exc)

    b.book_title = title
    db.commit()
    logger.info("[book] 改名 %r → %r, 迁移: %s, 草稿: 修%d/密%d",
                old, title, ", ".join(moved) or "无",
                len(drafts["patched"]), len(drafts["encrypted"]))
    return {"book_title": title, "moved": moved, "drafts": drafts}



@router.get("/books/{book_id}/prep")
def book_prep(book_id: str, db: Session = Depends(get_db)):
    """建书准备状态 (2026-08-23): preparing/l0/fill → ready|error; 前端轮询."""
    _book_or_404(db, book_id)
    return _PREP.get(book_id, {"status": "unknown", "step": None, "note": None, "ran": []})


@router.get("/books/{book_id}/cover")
def book_cover_img(book_id: str, db: Session = Depends(get_db)):
    """书皮 jpg (0925): <img> 直连; 幂等 — 存量书打开页面即按需补提."""
    from fastapi.responses import FileResponse

    from app.services.anim_pipeline import covers as _covers
    b = _book_or_404(db, book_id)
    p = _covers.extract_book_cover(b.book_title)
    if not p or not Path(p).exists():
        raise HTTPException(404, "书皮未提取到 (书库无 epub 或提取失败)")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/books/{book_id}/cover/reextract")
def book_cover_reextract(book_id: str, db: Session = Depends(get_db)):
    """重提书皮 (0925): 删幂等缓存 00_本书_*.jpg 再跑 — epub 晚到/提错重试."""
    from app.services.anim_pipeline import covers as _covers
    b = _book_or_404(db, book_id)
    cached = _covers.book_cover_path(b.book_title)
    if cached.exists():
        cached.unlink()  # 生成缓存产物, 红线例外可直删
    return _extract_cover(b.book_title)


@router.post("/books/{book_id}/auto-fill")
def auto_fill_l0(book_id: str, db: Session = Depends(get_db)):
    """蒸馏(facing)就绪后手动触发自动填充 (幂等, 已填字段跳过)."""
    b = _book_or_404(db, book_id)
    filled = orch.auto_fill_from_l0(db, b)
    return {"book_id": book_id, "auto_filled": filled}


# ── 风格选择板 (0922 用户令): 真内容试镜 → 库轮询+Kimi新配方 → 点选落绑定 ──

class StyleDirectionBody(BaseModel):
    direction: str = ""


@router.post("/books/{book_id}/style-board/direction")
def style_board_direction(book_id: str, body: StyleDirectionBody, db: Session = Depends(get_db)):
    """指定/清除大风格方向 (1009 小坑6): 如 "2.5D 立体感, 禁平面扁平" — 注入提案/重调."""
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    d = style_board.set_direction(b.book_title, body.direction)
    return {"book_title": b.book_title, "direction": d}


@router.post("/books/{book_id}/style-board")
def style_board_start(book_id: str, db: Session = Depends(get_db)):
    """启动风格选型 (后台 job): ep1 总纲切 3 试镜镜 → 库全员+Kimi提案 × K2 样张."""
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    ep1 = db.query(Episode).filter(Episode.book_id == b.id, Episode.ep_index == 1).first()
    if not ep1 or not (ep1.roadmap_json or {}).get("主题"):
        raise HTTPException(409, "先完成总纲 (ep1 主题空) 再做风格选型")
    ij = b.input_json if isinstance(b.input_json, dict) else {}
    material = {
        "书名": b.book_title, "核心主张": b.core_claim or "",
        "灵魂三问": ij.get("灵魂三问") or {},
        "第1集": {k: (ep1.roadmap_json or {}).get(k) for k in
                 ("主题", "黄金三秒钩子", "赌注层级", "贯穿人物", "对应书中内容", "核心任务")},
    }
    try:
        return style_board.start_board(b.id, b.book_title, material)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.get("/books/{book_id}/style-board")
def style_board_get(book_id: str, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    view = style_board.board_view(b.id, b.book_title)
    view["book_title"] = b.book_title
    view["bound"] = style_board.bound_style(b.book_title)
    view["excluded"] = style_board.exclusions(b.book_title)
    return view


class StyleSelectBody(BaseModel):
    style: str


@router.post("/books/{book_id}/style-board/select")
def style_board_select(book_id: str, body: StyleSelectBody, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    try:
        return style_board.select(b.book_title, body.style)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"选择板不存在, 先启动选型: {exc}")
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@router.get("/books/{book_id}/style-board/img/{name}")
def style_board_img(book_id: str, name: str, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse

    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    try:
        p = style_board.board_img(b.book_title, name)
    except FileNotFoundError:
        # 1009 小坑5: 库成员参考图 (预筛用, 存风格库目录不在板目录)
        try:
            p = style_board.lib_ref_img(b.book_title, name)
        except FileNotFoundError:
            raise HTTPException(404, f"图不存在: {name}")
    return FileResponse(p, headers={"Cache-Control": "no-store"})


class StyleExcludeBody(BaseModel):
    style: str
    on: bool = True  # True=排除 (下轮选型不入板, Kimi 补位); False=恢复


@router.post("/books/{book_id}/style-board/exclude")
def style_board_exclude(book_id: str, body: StyleExcludeBody, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    cur = style_board.set_exclusion(b.book_title, body.style, body.on)
    return {"book_title": b.book_title, "style": body.style,
            "on": body.on, "excluded": cur}


@router.post("/books/{book_id}/style-board/keep")
def style_board_keep(book_id: str, body: StyleSelectBody, db: Session = Depends(get_db)):
    """留存 (0922): 配方入库备用不绑本书."""
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    try:
        return style_board.keep(b.book_title, body.style)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"选择板不存在, 先启动选型: {exc}")
    except KeyError as exc:
        raise HTTPException(404, str(exc))


class StyleRetuneBody(BaseModel):
    style: str
    note: str = ""  # 用户判词, 默认 "画面粗糙, 请精修还原度"


@router.post("/books/{book_id}/style-board/retune")
def style_board_retune(book_id: str, body: StyleRetuneBody, db: Session = Depends(get_db)):
    """重调 (0922): 方向对但画面糙 — Kimi 修配方旋钮, 单列重渲 3 张 (后台 job, 页面轮询)."""
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    return style_board.start_retune(b.id, b.book_title, body.style, body.note)


# ── 艺术圣经页 (0922): 大纲→风格选型→圣经, 独立页 ──

@router.get("/books/{book_id}/style-bible")
def style_bible_get(book_id: str, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    return style_board.bible_view(b.id, b.book_title)


class BibleGenBody(BaseModel):
    force: bool = False  # True=无视缓存重掷


@router.post("/books/{book_id}/style-bible/generate")
def style_bible_generate(book_id: str, body: BibleGenBody, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    if not style_board.bound_style(b.book_title):
        raise HTTPException(409, "先完成风格选型 (圣经吃选定配方的风格家族)")
    try:
        return style_board.start_bible(b.id, b.book_title, force=body.force)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.post("/books/{book_id}/style-bible/save")
def style_bible_save(book_id: str, body: dict, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    try:
        return style_board.save_bible(b.book_title, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/books/{book_id}/style-bible/render-samples")
def style_bible_render_samples(book_id: str, body: BibleGenBody | None = None,
                               db: Session = Depends(get_db)):
    """圣经试渲 (0922): 色板/质感/角色策略烘进画面渲 3 张.
    force (1012): 穿透冷却+指纹全量重掷换种子 — 稳态沿用正确但用户要有强制入口."""
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    try:
        return style_board.render_bible_samples(b.id, b.book_title,
                                                force=bool(body and body.force))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.post("/books/{book_id}/style-bible/render-motion")
def style_bible_render_motion(book_id: str, db: Session = Depends(get_db)):
    """动画试渲 (1012): 圣经试渲首帧 × 书级动画律 → H3 5s 样片, 眼验运动气质."""
    from app.services.anim_pipeline import style_board
    b = _book_or_404(db, book_id)
    try:
        return style_board.render_motion_sample(b.id, b.book_title)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.post("/books/{book_id}/style-bible/draft-motion")
def style_bible_draft_motion(book_id: str, body: BibleGenBody, db: Session = Depends(get_db)):
    """起草动画律 (1012 用户令): LLM 按全书配方+圣经各字段直接起草 motion_style —
    存量经不必整经重掷; 已有非空律且非 force 不覆盖 (手调保护)。"""
    from app.services.anim_pipeline import director2
    b = _book_or_404(db, book_id)
    try:
        return director2.draft_motion_style(b.id, b.book_title, force=body.force)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


# ── 角色设计台 (0923 用户令): 圣经角色卡 → 定妆照沉淀, 独立功能 ──

@router.get("/books/{book_id}/style-characters")
def style_characters_get(book_id: str, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import character_sheets, style_board
    b = _book_or_404(db, book_id)
    bound = style_board.bound_style(b.book_title)
    recipe = None
    if bound:
        try:
            recipe = json.loads((style_board._assets() / "风格库" /
                                 f"{style_board._safe_name(bound)}.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    v = character_sheets.view(b.id, b.book_title, bound, recipe)
    v["book_title"] = b.book_title
    return v


class CharDesignBody(BaseModel):
    names: list[str] = []  # 空 = 全部角色
    looks: dict = {}  # {角色名: look} 文本框当前值随渲随存


@router.post("/books/{book_id}/style-characters/derive")
def style_characters_derive(book_id: str, db: Session = Depends(get_db)):
    """0928 角色生成归本页 (用户裁定): 各集贯穿人物+成稿+故事库 → 角色卡, 回写圣经."""
    from app.services.anim_pipeline import character_sheets
    b = _book_or_404(db, book_id)
    try:
        return character_sheets.derive_characters(b.id, b.book_title)
    except FileNotFoundError:
        raise HTTPException(404, "圣经不存在, 先在艺术圣经页生成")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc))


@router.post("/books/{book_id}/style-characters/ensure-author")
def style_characters_ensure_author(book_id: str, db: Session = Depends(get_db)):
    """0928 用户令: 作者卡每本书必含 (可以不用, 不能没有) — 存量书补卡, 不动已有卡."""
    from app.services.anim_pipeline import character_sheets
    b = _book_or_404(db, book_id)
    try:
        return character_sheets.ensure_author_card(b.id, b.book_title)
    except FileNotFoundError:
        raise HTTPException(404, "圣经不存在, 先在艺术圣经页生成")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc))


@router.post("/books/{book_id}/style-characters/design")
def style_characters_design(book_id: str, body: CharDesignBody, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import character_sheets
    b = _book_or_404(db, book_id)
    try:
        return character_sheets.start_design(b.id, b.book_title, body.names, body.looks or None)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


class CharSaveBody(BaseModel):
    characters: list[dict]


@router.post("/books/{book_id}/style-characters/save")
def style_characters_save(book_id: str, body: CharSaveBody, db: Session = Depends(get_db)):
    from app.services.anim_pipeline import character_sheets
    b = _book_or_404(db, book_id)
    try:
        return character_sheets.save_characters(b.book_title, body.characters)
    except FileNotFoundError:
        raise HTTPException(404, "圣经不存在, 先在艺术圣经页生成")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/books/{book_id}/style-characters/img/{name}")
def style_characters_img(book_id: str, name: str, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse

    from app.services.anim_pipeline import character_sheets
    b = _book_or_404(db, book_id)
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(404, name)
    p = character_sheets._design_dir(b.book_title) / name
    if not p.exists():
        raise HTTPException(404, f"定妆照不存在: {name}")
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@router.post("/books/{book_id}/rerun-outline")
def rerun_outline(book_id: str, db: Session = Depends(get_db)):
    """重跑总纲 (0912): 系列总编剧强制重排, 每次调用现读最新 laotan_series_director.txt.

    覆盖 灵魂三问+全系列 roadmap+集标题; 覆盖前自动备份到 data/l0/{书名}/roadmap_backup_{ts}.json.
    逐集稿不动 — 需要时另点级联重跑. 校验未过 (rejected) 不落库, 422 带问题清单.
    """
    from datetime import datetime

    b = _book_or_404(db, book_id)
    # 备份现总纲 (覆盖前)
    eps = db.query(Episode).filter(Episode.book_id == b.id).order_by(Episode.ep_index).all()
    backup_path = None
    if eps:
        from app.services.book_service.l0 import _l0_dir
        backup = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "灵魂三问": (b.input_json or {}).get("灵魂三问"),
            "roadmap": {e.ep_index: e.roadmap_json for e in eps},
            "titles": {e.ep_index: e.title for e in eps},
        }
        backup_path = _l0_dir(b.book_title) / f"roadmap_backup_{datetime.now():%m%d_%H%M%S}.json"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=1), encoding="utf-8")
    from app.services.book_service.series_outline import run_series_outline
    r = run_series_outline(db, b, replace=True)
    if r.get("status") == "rejected":
        raise HTTPException(422, "总编剧校验未过, 未落库: " + "; ".join(r.get("issues") or []))
    if r.get("status") != "ok":
        raise HTTPException(500, f"总编剧失败: {r.get('error') or r.get('status')}")
    # 联动 (0912 用户令: 总纲重排必须联动单元重切 — 否则单元级精料与总纲脱钩,
    # 逐集生成饿死单元料): units 按新总纲重切 + hooks 重产 + unit_id 回填。
    # 受众 (0913c): 书定读者优先 (目标读者以书定), 未锁定时继承旧指纹链
    recut = None
    try:
        from app.services.book_service.creation_steps import _book_reader_value
        from app.services.book_service.facing import recut_units_for_roadmap
        recut = recut_units_for_roadmap(
            b, db, audience=_book_reader_value(b.input_json or {}))
        logger.info("[rerun-outline] 单元联动重切: %s", recut)
    except Exception as exc:
        logger.warning("[rerun-outline] 单元重切失败 (不阻断, 可手动补跑): %s", exc)
    # 稿件 stale 标记 (0912 同类病 b: 总纲重排后旧稿无提示): 有稿的集打 _stale_outline,
    # 讲书页亮"旧总纲稿"徽标 — 与音频 stale 机制同构
    stale_eps = []
    for e in b.episodes:
        if e.script_text:
            rm = dict(e.roadmap_json or {})
            rm["_stale_outline"] = True
            e.roadmap_json = rm
            stale_eps.append(e.ep_index)
    if stale_eps:
        db.commit()
        logger.info("[rerun-outline] 旧总纲稿标记 stale: %s", stale_eps)
    return {"book_id": book_id, "status": "ok", "backup": str(backup_path) if backup_path else None,
            "recut": recut, "stale_eps": stale_eps,
            "soul": r.get("soul"),
            "episodes": [{"ep": e.get("ep"), "主题": e.get("主题")}
                         for e in r.get("episodes") or []]}


@router.get("/books")
def list_books(db: Session = Depends(get_db)):
    books = db.query(BookProject).order_by(BookProject.created_at.desc()).all()
    return {"books": [
        {"id": b.id, "book_title": b.book_title, "author": b.author,
         "status": b.status, "created_at": b.created_at.isoformat(),
         # 2026-08-22: 安全评级 tier (green/yellow/red), books.html 标题前显示颜色图标
         "tier": ((b.input_json or {}).get("risk_assessment") or {}).get("tier"),
         # 2026-08-22: L0 蒸馏状态 (data/l0/{书名}/l0-chapter-v1.json 存在)
         "l0_distilled": _l0_distilled(b.book_title),
         "progress": _book_progress(b),
         "episodes": [{"ep": e.ep_index, "title": e.title, "status": e.status}
                      for e in b.episodes]}
        for b in books]}


def _l0_distilled(book_title: str) -> bool:
    """L0 是否已蒸馏: data/l0/{书名}/l0-chapter-v1.json 存在."""
    try:
        from app.services.book_service.l0 import _l0_dir
        return (_l0_dir(book_title) / "l0-chapter-v1.json").exists()
    except Exception:
        return False


@router.get("/books/{book_id}/source-list")
def book_source_list(book_id: str, db: Session = Depends(get_db)):
    """书单体系 (2026-08-23): 本书引用的源头书/理论 → 粉丝书单/内容关联.

    同源头多章引用合并章节号; 书名带副标题/书名号差异时宽松匹配 L0 的 book 字段.
    """
    from app.services.book_service.reference_library import book_sources, load_library
    b = _book_or_404(db, book_id)
    sources = book_sources(b.book_title)
    if not sources:
        for bk in (load_library().get("books") or {}):
            if b.book_title in bk or bk in b.book_title:
                sources = book_sources(bk)
                if sources:
                    break
    merged: dict[str, dict] = {}
    for s in sources:
        nm = (s.get("name") or "").strip("《》 \t\r\n")  # L0 原始名带《》, 显示层剥掉
        if nm in merged:
            merged[nm]["chapters"] = sorted(set(merged[nm].get("chapters", [])) | set(s.get("chapters", [])))
        else:
            merged[nm] = dict(s, name=nm, chapters=list(s.get("chapters", [])))
    return {"book_title": b.book_title, "sources": list(merged.values())}


@router.get("/books/{book_id}/l0")
def book_l0(book_id: str, db: Session = Depends(get_db)):
    """L0 蒸馏 + 第二层 facing 产物摘要 (data/l0/{书名}/) — 供 UI 展示.

    返回: {distilled, meta, chapters, frontmatter, facing:{kernel,units,quotes,cases,compliance,readers}}
    """
    b = _book_or_404(db, book_id)
    from app.services.book_service.l0 import _l0_dir
    d = _l0_dir(b.book_title)
    l0_path = d / "l0-chapter-v1.json"
    result: dict = {"distilled": l0_path.exists(), "meta": {}, "chapters": 0,
                    "frontmatter": 0, "facing": {}}
    if l0_path.exists():
        try:
            l0 = json.loads(l0_path.read_text(encoding="utf-8"))
            result["meta"] = {k: (l0.get("meta") or {}).get(k)
                              for k in ("author", "publisher", "pub_date")}
            result["chapters"] = len(l0.get("chapters", []))
            result["frontmatter"] = len(l0.get("frontmatter") or [])
        except Exception:
            pass
    facing_map = {"kernel": "facing-kernel.json", "units": "facing-units.json",
                  "quotes": "facing-quotes.json", "cases": "facing-cases.json",
                  "compliance": "facing-compliance.json", "readers": "facing-readers.json"}
    for name, f in facing_map.items():
        p = d / "facing" / f
        if p.exists():
            try:
                result["facing"][name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return result


_STEP_LABELS = ["输入补全", "评论层+素材", "多集总纲", "逐集确认", "进产线"]


def _book_progress(b: BookProject) -> dict:
    """流程进度: 5 步布尔完成度 + 当前步骤 + 下一步动作提示.

    供前端渲染顶部流程条与"下一步入口", 单一事实源在后端状态机.
    """
    inp = b.input_json or {}
    eps = sorted(b.episodes, key=lambda e: e.ep_index)
    # 步骤完成判定 (状态机派生的保守布尔)
    step_done = [
        bool(inp.get("书籍类型") or inp.get("全书核心主张")),          # 1 输入补全
        bool(inp.get("comment_layer")) and bool(inp.get("materials")),  # 2 评论层+素材
        b.status in ("roadmap_review", "writing", "done") and bool(eps),  # 3 多集总纲
        bool(eps) and all(e.status == "confirmed" for e in eps),        # 4 逐集确认
        any(e.script_id for e in eps),                                  # 5 进产线
    ]
    current = 1
    for i, done in enumerate(step_done, 1):
        if not done:
            current = i
            break
    else:
        current = 5

    # 下一步动作: 当前步骤首个待办 → 对应按钮 + 提示
    next_action: dict[str, str] | None = None
    if current == 1:
        next_action = {"button": "complete", "label": "补全输入",
                       "hint": "补全书的核心字段 (类型/主张/读者画像)"}
    elif current == 2:
        next_action = {"button": "confirm-input", "label": "确认1 → 评论层+素材",
                       "hint": "生成读者反应清单 + 素材包"}
    elif current == 3:
        next_action = {"button": "roadmap", "label": "生成总纲 (pro)",
                       "hint": "生成 6 集拆解路线图, 约 1-3 分钟"}
    elif current == 4:
        nxt = next((e for e in eps if e.status != "confirmed"), None)
        if nxt:
            next_action = (
                {"button": f"episodes/{nxt.ep_index}/generate", "label": f"生成第 {nxt.ep_index} 集稿",
                 "hint": f"第 {nxt.ep_index} 集: {nxt.title or ''}"}
                if nxt.status in ("pending", "generating") else
                {"button": f"episodes/{nxt.ep_index}/confirm", "label": f"确认第 {nxt.ep_index} 集",
                 "hint": "稿子已生成, 人工过稿后确认定稿"})
    else:
        nxt = next((e for e in eps if e.script_id), None)
        next_action = {"button": "produce", "label": "进产线 →",
                       "hint": "已确认集 → 生成 Script/TTS/导演任务"} if nxt else None

    return {"steps": _STEP_LABELS, "done": step_done, "current": current,
            "next_action": next_action}


@router.get("/books/{book_id}")
def get_book(book_id: str, db: Session = Depends(get_db)):
    b = _book_or_404(db, book_id)
    # 1004 用户令: 动画全生成完的 draft 集自动过确认 (页面进度与产线实况同步)
    try:
        from app.services.book_service.episode_gen import auto_confirm_if_anim_done
        for _e in b.episodes:
            auto_confirm_if_anim_done(db, _e)
    except Exception:  # noqa: BLE001 — 同步失败不挡页面
        pass
    # 樊登全书稿状态 (0912): 页面按钮显示已有稿/字数
    _ff_info = {"exists": False, "chars": 0}
    try:
        from app.services.book_service.fandeng_full import load_fandeng_full
        _fft = load_fandeng_full(b.book_title)
        if _fft:
            _ff_info = {"exists": True, "chars": len(_fft)}
    except Exception:
        pass
    return {
        "id": b.id, "book_title": b.book_title, "author": b.author,
        "status": b.status, "input_json": b.input_json,
        "source_path": b.source_path, "cart_url": b.cart_url,
        "cover_url": b.cover_url, "selling_point": b.selling_point,
        "cover": _extract_cover(b.book_title),  # 0925: 书皮状态 (幂等, 存量书即看即补)
        "progress": _book_progress(b),
        "fandeng_full": _ff_info,
        "episodes": [
            {"ep": e.ep_index, "title": e.title, "status": e.status,
             "script_text": e.script_text, "coverage": e.coverage_json,
             "roadmap": e.roadmap_json, "target_duration_sec": e.target_duration_sec,
             "script_id": e.script_id, "director_job_id": e.director_job_id,
             # 成稿时间 (0912): sqlite 存 naive UTC, 补 tz 让前端 new Date() 正确转本地
             "script_generated_at": (e.script_generated_at.replace(tzinfo=timezone.utc).isoformat()
                                     if e.script_generated_at else None)}
            for e in b.episodes],
    }


@router.post("/books/{book_id}/complete")
def complete(book_id: str, db: Session = Depends(get_db)):
    """步骤1: 输入补全 (flash) →【确认1】."""
    b = orch.complete_input(db, _book_or_404(db, book_id))
    return {"status": b.status,
            "needs_supplement": (b.input_json or {}).get("needs_supplement", [])}


@router.post("/books/{book_id}/assess")
def assess(book_id: str, db: Session = Depends(get_db)):
    """Gate 0 预评估 (蒸馏前): 丢书名定档 🟢/🟡/🔴, 防蒸馏浪费."""
    b = _book_or_404(db, book_id)
    meta = (b.input_json or {}).get("book_meta")
    if not meta and getattr(b, "source_url", None):
        from app.services.book_service.reader import fetch_zhihailib_meta
        meta = fetch_zhihailib_meta(b.source_url)
    r = orch.assess_book_risk(b.book_title, b.author, meta)
    inp = dict(b.input_json or {})
    inp["risk_assessment"] = r
    b.input_json = inp
    db.commit()
    return r


@router.post("/books/{book_id}/confirm-input")
def confirm_input(book_id: str, db: Session = Depends(get_db)):
    """确认1 通过 → 步骤2∥3 评论层+素材 (轻确认)."""
    b = _book_or_404(db, book_id)
    orch.build_comment_layer(db, b)
    orch.build_materials(db, b)
    return {"status": b.status,
            "comment_layer": len((b.input_json or {}).get("comment_layer") or []),
            "materials": len((b.input_json or {}).get("materials") or [])}


@router.post("/books/{book_id}/roadmap")
def roadmap(book_id: str, db: Session = Depends(get_db)):
    """步骤4: 多集总纲 (pro) + 追溯自检 →【确认2】."""
    b, issues = orch.build_roadmap(db, _book_or_404(db, book_id))
    return {"status": b.status, "trace_issues": issues,
            "episodes": [{"ep": e.ep_index, "title": e.title} for e in b.episodes]}


@router.post("/books/{book_id}/confirm-roadmap")
def confirm_roadmap(book_id: str, db: Session = Depends(get_db)):
    b = _book_or_404(db, book_id)
    b.status = "writing"
    db.commit()
    return {"status": b.status}


@router.put("/books/{book_id}/roadmap")
def update_roadmap(book_id: str, body: RoadmapUpdate, db: Session = Depends(get_db)):
    """总纲批量编辑 (2026-08-20): 加入个人视角. 6 集全量提交, 校验 ep 完整性.

    roadmap_json 结构保持 {ep,主题,对应书中内容,核心任务,承上,启下,概念} 不变,
    兼容 generate_episode 的 prompt 注入与主题自检; 编辑后清 _issues/_checks 防误导.
    """
    b = _book_or_404(db, book_id)
    eps = {e.ep_index: e for e in b.episodes}
    if not eps:
        raise HTTPException(400, "还没有总纲, 先点【生成总纲】")
    if len(body.episodes) != len(eps):
        raise HTTPException(400, f"总纲须为 {len(eps)} 集全量提交, 实际 {len(body.episodes)}")
    valid_eps = set(eps)
    for row in body.episodes:
        if row.ep not in valid_eps:
            raise HTTPException(400, f"ep={row.ep} 不在本书记录的集数 {sorted(valid_eps)} 中")

    for row in body.episodes:
        ep = eps[row.ep]
        cur = dict(ep.roadmap_json or {})
        cur.update({
            "ep": row.ep,
            "主题": row.主题,
            "本集秘籍": row.本集秘籍,
            "对应书中内容": row.对应书中内容,
            "核心任务": row.核心任务,
            "卖点": row.卖点,
            "承上": row.承上,
            "启下": row.启下,
            "概念": row.概念 or [],
        })
        # 编辑后旧自检/校验结果失效, 清空防误导
        cur.pop("_issues", None)
        cur.pop("_checks", None)
        ep.roadmap_json = cur
        ep.title = row.主题 or ep.title  # 与 build_roadmap 的 title=主题 对齐
    db.commit()
    return {"updated": len(body.episodes), "status": b.status}


@router.put("/books/{book_id}/episodes/{n}")
def edit_episode(book_id: str, n: int, body: EpisodeEdit, db: Session = Depends(get_db)):
    """人工修稿 (确认节点内).

    1018 C4d (R8): 修稿保存后重跑终稿复检刷新 _issues/_checks — 旧版只重解析模块表,
    人编辑后的稿零校验, roadmap 问题单永远陈旧 (episode_gen "三点齐查"注释与实现不符)。
    不阻塞保存, 只刷新人闸视野。
    """
    ep = _ep_or_404(db, book_id, n)
    module_warning = None
    relint_issues: list[str] = []
    for f in ("script_text", "title", "target_duration_sec"):
        v = getattr(body, f)
        if v is not None:
            setattr(ep, f, v)
    if body.script_text is not None:
        # 六拍模块总线 (0917): 修稿是 ep1/ep2 裸稿事故元凶 — 新稿带标记则重解析落库;
        # 标记被编辑丢失时保留旧模块表 (tail 锚在新稿重定位, 定位不到的消费端会如实报),
        # 并亮灯提示, 不静默断总线。
        from app.services.book_service.module_map import parse_modules
        mods = parse_modules(body.script_text)
        if mods:
            ep.module_json = mods
        else:
            module_warning = ("稿件无六拍标记: 沿用历史模块表 (按末行锚定位); "
                              "若正文大改, 建议补标记或重新生成 — 否则 TTS 模块墙/切场将退化为旧结构")
        ep.script_generated_at = _now()  # 修稿也算新稿落定 (成稿时间刷新)
        # 终稿同规复检 (快照化): 与生成链同一把尺, 人闸看到的就是当前稿的真问题
        try:
            from app.models.roles import Persona
            from app.services.book_service import passes as _passes
            from app.services.book_service.episode_gen import (
                _coverage_dup_issues,
                _story_dup_issues,
            )
            _total = len(ep.book.episodes) or 6
            _pid = (ep.book.input_json or {}).get("persona_id")
            _p = db.query(Persona).filter(Persona.id == _pid).first() if _pid else None
            _laotan = bool(_p and "laotan" in (_p.prompt_template or ""))
            _hook_ref = str((ep.roadmap_json or {}).get("黄金三秒钩子") or "") if ep.ep_index > 1 else ""
            from app.services.book_service.creation_common import resolve_series_word as _rsw
            _snap = _passes.snapshot(
                ep.script_text, target=ep.target_duration_sec, series_word=_rsw(ep.book) or "",
                book_title=ep.book.book_title,
                require_cta=(_laotan and not (ep.ep_index >= _total and _total > 1)),
                ep_index=ep.ep_index, flow_check=(1 < ep.ep_index < _total),
                figure=str((ep.roadmap_json or {}).get("贯穿人物") or ""),
                hook_ref=_hook_ref,
                include=("validate", "flow", "fig", "hook"))
            relint_issues = _passes.snap_issues(_snap)
            # 1019 排重令③: 修稿复检同规补排重闸 — 否则人改稿触发的问题单刷新会
            # 洗掉阻断标记, 确认闸被绕过 (复检视野不含生成时闸, 须重算)
            relint_issues += _story_dup_issues(ep.book, ep, ep.script_text or "")
            relint_issues += _coverage_dup_issues(ep.book, ep)
            _rm = dict(ep.roadmap_json or {})
            _rm["_issues"] = relint_issues if relint_issues else []
            _rm.setdefault("_checks", {})
            ep.roadmap_json = _rm
        except Exception as _exc:  # noqa: BLE001 — 复检失败不阻塞保存
            module_warning = (module_warning or "") + f" | 修稿复检异常: {_exc}"
    db.commit()
    return {"ep": ep.ep_index, "status": ep.status, "module_warning": module_warning,
            "issues": relint_issues}


@router.post("/books/{book_id}/episodes/{n}/generate")
def generate(book_id: str, n: int, db: Session = Depends(get_db)):
    """步骤5: 逐集生成 (pro) + 硬校验 + 人设注入 →【确认3】."""
    try:
        ep = orch.generate_episode(db, _ep_or_404(db, book_id, n))
    except ValueError as e:
        # 1018 C4a: 目标时长平台带闸 (270,420)s — 人调 target, 不代改
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"ep": ep.ep_index, "status": ep.status,
            "issues": (ep.roadmap_json or {}).get("_issues", []),
            "checks": (ep.roadmap_json or {}).get("_checks", {})}


@router.post("/books/{book_id}/episodes/{n}/confirm")
def confirm_episode(book_id: str, n: int, db: Session = Depends(get_db)):
    try:
        ep = orch.confirm_episode(db, _ep_or_404(db, book_id, n))
    except ValueError as e:
        # 1019 排重令③: 案例跨集重复阻断 → 422 (同 generate 路由口径)
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"ep": ep.ep_index, "status": ep.status, "book_status": ep.book.status}


@router.post("/books/{book_id}/episodes/{n}/rerun")
def rerun(book_id: str, n: int, db: Session = Depends(get_db)):
    """级联重跑: 重跑 N 作废 N..6."""
    b = orch.rerun_cascade(db, _book_or_404(db, book_id), n)
    return {"status": b.status,
            "reset": [e.ep_index for e in b.episodes if e.status == "pending"]}


@router.post("/books/{book_id}/episodes/{n}/produce")
def produce(book_id: str, n: int, body: ProduceRequest | None = None,
            db: Session = Depends(get_db)):
    """进产线桥接 (2026-08-19): 确认集 → Article 占位 + Script + segments.

    复用新闻线后半段: 返回 script_id 后 UI 调
    POST /audio/scripts/{id}/generate-audio → POST /api/director/jobs → director.html。
    人设可选 (2026-08-20): body.persona_id 指定人物即账号; 缺省回退静读书.
    """
    from app.models import Article, Host, Persona, Script, Segment
    from app.services.book_service.persona import ensure_book_account
    from app.services.script_parser import parse_script

    b = _book_or_404(db, book_id)
    ep = _ep_or_404(db, book_id, n)
    preview = bool(body.preview) if body else False
    if ep.status != "confirmed" or not ep.script_text:
        # preview (TTS 先行): 草稿+有稿 即可预产音频 (人耳验收后再确认3)
        if not (preview and ep.script_text):
            raise HTTPException(409, f"第{n}集未确认或无稿 (先走 确认3)")
    force = bool(body.force) if body else False
    _existing = db.get(Script, ep.script_id) if ep.script_id else None
    if _existing and not force:
        if not preview:
            return {"script_id": ep.script_id, "already": True}
        # preview 复用判定: 稿没变直接复用现 Script (打 TTS), 变了才重建
        from app.services.script_parser import clean_episode_script as _clean
        _cleaned_now, _ = _clean(ep.script_text)
        if _existing.script_text == _cleaned_now:
            return {"script_id": ep.script_id, "already": True, "preview": True}
        force = True  # 稿变了 → 重建 (segments 同步新稿)
    if force and ep.script_id:
        # 重跑: 删旧 Script + 其 segments (级联), 再重建
        old = db.get(Script, ep.script_id)
        if old:
            logger.info("[book] 第%d集 重跑进产线, 删旧 script %s", n, old.id)
            db.delete(old)  # segments cascade
        ep.script_id = None

    inp = dict(b.input_json or {})
    # 人设解析 (人物即账号, 仿 articles._do_rewrite): 显式 body → 上次记住 → 回退静读书
    persona, host = None, None
    pid = (body.persona_id if body else None) or (inp.get("persona_id") or None)
    if pid:
        persona = db.query(Persona).filter(Persona.id == pid).first()
        if not persona:
            raise HTTPException(404, f"人物不存在: {pid}")
        if not persona.host_id:
            raise HTTPException(400, f"人物 {persona.name} 未绑定账号 (host)")
        host = db.query(Host).filter(Host.id == persona.host_id).first()
    if host is None:
        host = ensure_book_account(db)
        persona = db.query(Persona).filter(Persona.host_id == host.id).first()
    inp["persona_id"] = persona.id if persona else None  # 记住所选, 后续集默认复用
    b.input_json = inp

    article = db.get(Article, inp.get("article_id") or "") if inp.get("article_id") else None
    if not article:
        article = Article(
            title=f"[拆书]《{b.book_title}》",
            raw_text=(ep.script_text or b.book_title)[:20000],
            track="tech",
        )
        db.add(article)
        db.flush()
        inp["article_id"] = article.id
        b.input_json = inp

    # ── 拆书稿清洗 (2026-08-20): 剥离【A-B秒｜段名】时间标签 + 校验六段完整.
    # 标签是结构标记, 进音频会读出"零到二十二秒,钩子" → 必须剥离后再 parse_script.
    from app.services.script_parser import clean_episode_script
    cleaned_text, clean_issues = clean_episode_script(ep.script_text)
    if clean_issues:
        logger.warning("[book] 第%d集 清洗告警: %s", n, "; ".join(clean_issues))

    # 六拍模块总线 (0917): 结构真相源 = ep.module_json (tail 锚, 任意文本形态重定位).
    # 墙(模块末行号)不落库 — 派生值, 由 TTS 入口在当刻行序列 (Segment 拼接) 上现定位,
    # 免行号口径漂移。
    # 1019-B: 模块表是 script_text 的派生值 — 每次进产线重解析覆盖 (拆分锚升级后
    # 旧表自动自愈, 不冻结在旧规则墙位); 稿件无标记解析空 → 保留旧表兜底。
    from app.services.book_service import module_map
    _mods = module_map.parse_modules(ep.script_text or "")
    if _mods:
        ep.module_json = _mods
    elif not ep.module_json:
        clean_issues.append("无六拍模块表 (TTS 模块墙/切场将退化为字数贪心)")

    script = Script(
        article_id=article.id,
        host_id=host.id,
        script_text=cleaned_text,  # 清洗后文本 (无时间标签), TTS/导演用
        video_format="landscape",
        prompt_template=persona.prompt_template if persona else "jingshu-book",
    )
    db.add(script)
    db.flush()
    fixed_opening = persona.fixed_opening if persona else None
    fixed_ending = persona.fixed_ending if persona else None
    for seg_data in parse_script(cleaned_text, fixed_opening, fixed_ending):
        db.add(Segment(script_id=script.id, **seg_data))
    ep.script_id = script.id
    db.commit()
    seg_count = db.query(Segment).filter(Segment.script_id == script.id).count()
    return {"script_id": script.id, "article_id": article.id, "segments": seg_count,
            "persona_id": persona.id if persona else None,
            "clean_issues": clean_issues}


@router.get("/books/{book_id}/cart-checklist")
def cart_checklist(book_id: str, db: Session = Depends(get_db)):
    """发布挂车提醒清单 (挂车动作在抖音侧人工)."""
    b = _book_or_404(db, book_id)
    lines = [f"发布挂车清单 《{b.book_title}》 商品链接: {b.cart_url or '(未填)'}"]
    for e in b.episodes:
        if e.status == "confirmed":
            lines.append(f"第{e.ep_index}集 {e.title or ''} → 挂车 + 卖点口播: {b.selling_point or '(未填)'}")
    return {"checklist": "\n".join(lines)}


@router.delete("/books/{book_id}")
def delete_book(book_id: str, db: Session = Depends(get_db)):
    """删除拆书项目 (2026-08-20): 级联删 6 集 + 连带清理已进产线的 Script/Article.

    BookProject.episodes 有 cascade=all, delete-orphan; 若已 produce 进产线,
    关联的 Script 与 Article 一并删除, 避免孤儿记录。
    """
    from app.models import Article, Script

    b = _book_or_404(db, book_id)
    title = b.book_title

    # 收集产线产物: 各集 script_id + book 级 article_id
    script_ids = {e.script_id for e in b.episodes if e.script_id}
    article_id = (b.input_json or {}).get("article_id")

    if script_ids:
        db.query(Script).filter(Script.id.in_(script_ids)).delete(synchronize_session=False)
    if article_id:
        art = db.get(Article, article_id)
        if art:
            # 该 Article 名下的其他 Script (非本拆书集) 不受影响; 仅删本书建的那条
            db.delete(art)

    db.delete(b)  # episodes 级联删除
    db.commit()
    logger.info("[book] 已删除拆书项目: %s (%s)", title, book_id)
    return {"deleted": book_id, "book_title": title}


class PublishCardBody(BaseModel):
    action: str = "copy"  # copy=调取三件套; covers=模板封面(横+竖)
    sub: str | None = None    # covers: 封面红字钩子 (页面编辑框传回)


@router.get("/books/{book_id}/episodes/{n}/publish-card")
def publish_card(book_id: str, n: int, db: Session = Depends(get_db)):
    """发布卡数据 (0918 用户令): 本集封面位 + 发布三件套 — 数据全对应本集."""
    b = _book_or_404(db, book_id)
    ep = _ep_or_404(db, book_id, n)
    rm = ep.roadmap_json or {}
    inp = b.input_json or {}
    l1 = (inp.get("目标读者画像") or {})
    l1v = str(l1.get("value") or "") if l1.get("tier") != "persona" else ""
    # 立意模块 (若本集已动画规划)
    metaphor = ""
    try:
        from app.services.anim_pipeline import shots as _shots
        doc = _shots.load_doc(b.book_title, n)
        metaphor = "; ".join(str(a.get("metaphor_core") or a.get("visual_metaphor") or "")
                             for a in (doc.get("arcs") or [])[:2])
    except Exception:  # noqa: S110 — 未规划集无立意
        pass
    # 已生成的模板封面 (ep 产物 publish/ 目录, 经 anim 文件路由回服务)
    cover_v = cover_h = None
    try:
        from app.services.anim_pipeline import shots as _shots
        _pub = _shots.ep_dir(b.book_title, n) / "publish"
        _base = f"/api/anim/ep/{book_id}/{n}/file/publish/"

        def _cv(name: str) -> str:
            # ?v=mtime 破缓存 (0921 实锤: 重打样后浏览器一直吃旧图)
            return f"{_base}{name}?v={int((_pub / name).stat().st_mtime)}"

        if (_pub / "cover_v.png").exists():
            cover_v = _cv("cover_v.png")
        if (_pub / "cover_h.png").exists():
            cover_h = _cv("cover_h.png")
    except Exception:  # noqa: S110 — 未进动画线的集无 ep 目录
        pass
    material = (f"书: {b.book_title}\n本集主题: {rm.get('主题') or ep.title}\n"
                f"本集秘籍: {rm.get('本集秘籍') or ''}\n黄金三秒钩子: {rm.get('黄金三秒钩子') or ''}\n"
                f"目标读者: {l1v[:120]}\n核心隐喻: {metaphor[:160]}")
    copy = rm.get("_publish_copy") or None
    if copy:  # 存量 6-8 标签历史稿出卡即裁 5 (抖音上限, 0921 实测)
        copy = {**copy, "tags": (copy.get("tags") or [])[:5]}
    return {"book_title": b.book_title, "ep_title": ep.title, "theme": rm.get("主题") or "",
            "material": material, "copy": copy,
            "cover_v": cover_v, "cover_h": cover_h}


@router.post("/books/{book_id}/episodes/{n}/publish-card")
def publish_card_gen(book_id: str, n: int, body: PublishCardBody,
                     db: Session = Depends(get_db)):
    """发布物料: action=copy → 三件套 (0921 用户令: 纯程序调取零 LLM — 大纲
    主题/钩子/概念 + 讲稿开场块即素材, LLM"生成"再像也是改写, 直接取原文);
    action=covers → 模板封面 (可控渲染)."""
    b = _book_or_404(db, book_id)
    ep = _ep_or_404(db, book_id, n)
    rm = ep.roadmap_json or {}

    if body.action == "covers":
        # 模板封面 (0918): 复古书风主模板 (横+竖) + 大字海报风 (给了核心字才出).
        # 红字优先级: 页面编辑框 > 主题破折号后段子句收口 (与三件套同源, 不再
        # 裸用全题 — 主题含"——"时整串上封面=三行, ep6 实锤)
        import re as _re2

        from app.services.anim_pipeline import shots as _shots
        from app.services.book_service.cover_render import render_covers

        def _clause2(text: str, limit: int) -> str:
            t = str(text or "").strip()
            if len(t) <= limit:
                return t
            out = ""
            for cl in _re2.split(r"(?<=[，。；！？])", t):
                if cl and len(out) + len(cl) <= limit:
                    out += cl
            return out.rstrip("，。；、 ") or t[:limit]

        # 1018e: 与三件套同链 — 钩子后段 > 主题后段 (剥壳词); 页面编辑框仍最优先
        _fb = ""
        for _src in (str(rm.get("黄金三秒钩子") or ""), str(rm.get("主题") or "")):
            if "——" in _src:
                _t = _re2.sub(r"^(秘密是|答案是|其实是|其实就是|那就是)", "",
                              _src.split("——")[-1].strip()).strip(" \'‘’\"“”。.，").strip()
                if _t:
                    _fb = _clause2(_t, 20)
                    break
        if not _fb:
            _th = str(rm.get("主题") or "")
            _fb = _clause2(_th.split("——")[-1].strip() if "——" in _th else _th, 20) \
                or _clause2(str(rm.get("黄金三秒钩子") or ep.title), 20)
        subtitle = (body.sub or "").strip() or _fb
        out_dir = _shots.ep_dir(b.book_title, n) / "publish"
        render_covers({"ep_label": f"第{n}集", "main_title": b.book_title,
                       "subtitle": str(subtitle)}, out_dir)
        base = f"/api/anim/ep/{book_id}/{n}/file/publish/"

        def _cv2(name: str) -> str:  # 同 GET: mtime 破缓存
            return f"{base}{name}?v={int((out_dir / name).stat().st_mtime)}"

        out = {"cover_v": _cv2("cover_v.png"), "cover_h": _cv2("cover_h.png"),
               "slots": {"ep_label": f"第{n}集",
                         "main_title": b.book_title,
                         "subtitle": str(subtitle)[:26]}}
        return out

    # ── 三件套程序调取 (零 LLM): 全部字段来自大纲/讲稿原文 ──────────────
    import re as _re

    def _clause_fit(text: str, limit: int) -> str:
        """按子句 (，。；！？) 贪心装到 limit 内 — 永不切词截半句."""
        t = str(text or "").strip()
        if len(t) <= limit:
            return t
        clauses = _re.split(r"(?<=[，。；！？])", t)
        out = ""
        for cl in clauses:
            if not cl:
                continue
            if len(out) + len(cl) <= limit:
                out += cl
            else:
                break
        return out.rstrip("，。；、 ") or t[:limit]

    def _cover_sub_from(rm: dict, ep_title: str = "") -> str:
        """红字正源 (1018e 升级): 钩子破折号后段 > 主题破折号后段 > 主题子句收口.
        旧版只吃主题 = 说明性标签无钩力 (用户令); 后段剥「秘密是」类壳词与引号."""
        for src in (str(rm.get("黄金三秒钩子") or ""), str(rm.get("主题") or "")):
            if "——" in src:
                tail = _re.sub(r"^(秘密是|答案是|其实是|其实就是|那就是)", "",
                               src.split("——")[-1].strip())
                tail = tail.strip(" \'‘’\"“”。.，").strip()
                if tail:
                    return _clause_fit(tail, 20)
        theme = str(rm.get("主题") or "")
        return _clause_fit(theme, 20) or _clause_fit(ep_title, 20)

    def _first_sentence(text: str, limit: int = 24) -> str:
        t = _re.split(r"[。！？!?]", str(text or "").strip())[0].strip("，,；;、 ")
        return _clause_fit(t, limit)

    def _opening_desc(script_text: str, hook: str = "", title: str = "",
                      limit: int = 122) -> str:
        """讲稿开场块 → 简介: 剥【】标注/书名句/作者句/悬尾句, 按整行贪心装 +
        固定互动尾 — 行粒度拼装, 不截半句. 开场块=跳过头部标记行后收正文,
        遇下一个【】段标记即止 (不串场); 时间码标记独占成段 (1003 d2ef4c74
        ep4 实锤: 【0-28秒｜钩子】后接空行, 旧版只扫 paras[0] 全被过滤 →
        简介空 → 422)."""
        out_lines: list[str] = []
        started = False
        for raw in str(script_text or "").split("\n"):
            ln = raw.strip()
            if not ln:
                continue
            if ln.startswith("【"):
                if started:
                    break  # 已收正文, 下一段标记即开场块边界
                continue  # 头部标记行 (同段或独占段) 跳过
            if "《" in ln or "作者" in ln or "译者" in ln \
                    or ln.endswith(("——", "：", ":")):
                continue
            started = True
            out_lines.append(ln)
        # 1018e 去重 (一料一位): 钩子句已当标题, 红字吃后段 — 简介剔除与之
        # 重叠的行 (归一化前缀匹配) 且限 3 行, 三处不再互为复读机
        _tbl = str.maketrans("", "", " \t'‘’\"“”.,，。;；:：!！?？-—…·()（）[]【】")
        _ref = str(hook or "").translate(_tbl) + "|" + str(title or "").translate(_tbl)
        out_lines = [ln for ln in out_lines
                     if not (ln.translate(_tbl)[:10] and ln.translate(_tbl)[:10] in _ref)][:3]
        out = ""
        for ln in out_lines:
            if len(out) + len(ln) > limit - 6:  # 留互动尾空间
                break
            out += ln
        out = out.rstrip("，；、 ")
        return (out + "评论区聊聊。") if out else ""

    # 标题 = 黄金三秒钩子首句 (≤24字即成品; 超长按子句收口, 回退本集标题)
    title = _first_sentence(rm.get("黄金三秒钩子") or "") or \
        _first_sentence(ep.title)
    # 红字 = 主题破折号后段, 子句收口不截词 (covers 兜底同源 _cover_sub_from)
    cover_sub = _cover_sub_from(rm, ep.title)
    # 简介 = 讲稿开场块直出
    desc = _opening_desc(ep.script_text, hook=str(rm.get("黄金三秒钩子") or ""),
                         title=title)
    # 标签 = 固定五件套 (1005 用户令: 全集完全一致, 品牌 tag 常驻 — 语义纯度
    # 靠口播全文, tag 求零成本一致性): 书名短名 + 品类三常驻 + 老谭读书会
    def _short_title(t: str) -> str:
        t = _re.sub(r"[《》\s（）()].*?$|[_—-].*$", "", str(t or "").strip())
        return t[:8] or str(t)[:8]
    tags = [t for t in dict.fromkeys([_short_title(b.book_title), "拆书",
                                      "商业思维", "读书", "老谭读书会"]) if t][:5]
    # 评论引导 (1005 用户令: 发布卡新区域, 每集一条 — 程序调取零 LLM):
    # 正源 = 主题 + 钩子句原文, 模板只包壳; 置顶评/首评直接复制用
    # 1018e 升级 (一料一位): 主题/钩子已住标题+红字, 此处不再复读 —
    # 秘籍首次入位 (此前三件套无家) + 讲稿自带互动句 > 通用兜底
    _tool = _re.split("[（(]", str(rm.get("本集秘籍") or ""))[0].strip()
    # 互动句三级: ①问句(≥8字, 「你站哪边？」类短问弃用) ②自查句(可。结尾)
    # ③通用兜底 — 报到/续集向句子永不入位置顶评 (ep4/ep6 实锤)
    _lines = [ln.strip() for ln in str(ep.script_text or "").split("\n")
              if ln.strip() and not ln.strip().startswith("【")
              and ("评论区" in ln or "自查" in ln or "留言" in ln)]
    _q = ""
    for ln in _lines:  # ① 问句
        if "？" not in ln and "?" not in ln:
            continue
        _c = _re.sub(r"^(先?自查[：:]?\s*)", "",
                     _re.split("(?<=[？?])", ln)[0]).strip().rstrip("。.，；; ")
        if len(_c) >= 8:
            _q = _c if _c.endswith(("？", "?")) else _c + "？"
            break
    if not _q:  # ② 自查句 (。结尾也收, ep4 实锤)
        for ln in _lines:
            if "自查" not in ln:
                continue
            _c = _re.sub(r"^(先?自查[：:]?\s*)", "",
                         _re.split("[。！？!?]", ln)[0]).strip()
            if len(_c) >= 8:
                _q = _c
                break
    if not _q:  # ③ 通用兜底
        _q = "你遇到过这种情况吗？当时怎么处理的？"
    _cue = []
    if _tool:
        _cue.append(f"📌 本期工具：{_tool}")
    _cue.append(_q)
    _cue.append("工具细节看视频，收藏备用👇" if _tool else "评论区聊聊👇")
    comment_cue = "\n".join(_cue)
    if not title or not desc:
        raise HTTPException(422, "大纲/讲稿数据不全, 调取失败 — 检查本集大纲与讲稿")
    out = {"title": title, "desc": desc, "cover_sub": cover_sub, "tags": tags,
           "comment_cue": comment_cue}
    # 落 roadmap (0918 同模式): 重开页面不丢, 封面红字有稳定正源
    ep.roadmap_json = {**rm, "_publish_copy": out}
    db.commit()
    logger.info("[publish-card] ep%d 三件套程序调取 (零LLM): %s", n, title)
    return out
