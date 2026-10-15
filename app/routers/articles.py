"""Article router: CRUD + trigger rewrite."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from ..database import db_session, get_db, get_session_maker
from ..models import Article, Host, Persona, Script

logger = logging.getLogger(__name__)
from app.services.job_events import _publish

from ..schemas import (
    ArticleCreate,
    ArticleOut,
    ArticleUpdate,
    FetchUrlRequest,
    FetchUrlResponse,
    RewriteRequest,
)
from ..services.llm_service import LLMService, get_llm_service
from ..services.script_parser import parse_script
from ..services.url_fetcher import fetch_url

router = APIRouter(prefix="/api/articles", tags=["articles"])


# ---------------------------------------------------------------------------
# List available rewrite prompt templates
# ---------------------------------------------------------------------------
@router.get("/prompt-templates")
def list_prompt_templates():
    """返回 config/ 下可用的洗稿提示词模板列表."""
    config_dir = Path(__file__).resolve().parents[2] / "config"
    templates = []
    if config_dir.exists():
        for f in sorted(config_dir.glob("*.txt")):
            # 排除非洗稿模板（如视觉导演）
            if f.stem.startswith("visual_director"):
                continue
            templates.append({
                "id": f.stem,
                "name": f.stem,
                "size": f.stat().st_size,
            })
    return templates


# ---------------------------------------------------------------------------
# Upload a new prompt template
# ---------------------------------------------------------------------------
@router.post("/prompt-templates/upload")
async def upload_prompt_template(
    name: str = Form(..., min_length=1, max_length=64),
    file: UploadFile = File(...),
):
    """上传新的洗稿提示词模板 .txt 文件到 config/ 目录."""
    # Sanitize name: only allow alphanumeric, underscore, hyphen
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())
    if not safe_name:
        raise HTTPException(status_code=400, detail="模板名称无效")

    config_dir = Path(__file__).resolve().parents[2] / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / f"{safe_name}.txt"

    if target.exists():
        raise HTTPException(status_code=409, detail=f"模板 '{safe_name}' 已存在，请先删除旧版")

    content = await file.read()
    # Ensure it's text
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件必须是 UTF-8 文本")

    target.write_text(text, encoding="utf-8")
    return {"id": safe_name, "name": safe_name, "size": target.stat().st_size}


# ---------------------------------------------------------------------------
# Rename a prompt template
# ---------------------------------------------------------------------------
@router.put("/prompt-templates/{template_id}")
def rename_prompt_template(template_id: str, new_name: str):
    """重命名洗稿提示词模板."""
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", template_id)
    if not safe_id:
        raise HTTPException(status_code=400, detail="模板 ID 无效")

    safe_new = re.sub(r"[^a-zA-Z0-9_\-]", "_", new_name.strip())
    if not safe_new:
        raise HTTPException(status_code=400, detail="新名称无效")

    config_dir = Path(__file__).resolve().parents[2] / "config"
    old_path = config_dir / f"{safe_id}.txt"
    new_path = config_dir / f"{safe_new}.txt"

    if not old_path.exists():
        raise HTTPException(status_code=404, detail=f"模板 '{safe_id}' 不存在")
    if new_path.exists():
        raise HTTPException(status_code=409, detail=f"模板 '{safe_new}' 已存在")

    old_path.rename(new_path)
    return {"ok": True, "old": safe_id, "new": safe_new}


# ---------------------------------------------------------------------------
# Delete a prompt template
# ---------------------------------------------------------------------------
@router.delete("/prompt-templates/{template_id}")
def delete_prompt_template(template_id: str):
    """删除指定的洗稿提示词模板."""
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", template_id)
    if not safe_id:
        raise HTTPException(status_code=400, detail="模板 ID 无效")

    config_dir = Path(__file__).resolve().parents[2] / "config"
    target = config_dir / f"{safe_id}.txt"

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"模板 '{safe_id}' 不存在")

    # Don't allow deleting built-in templates accidentally
    if safe_id == "laochen_default":
        raise HTTPException(status_code=403, detail="不允许删除内置模板")

    target.unlink()
    return {"ok": True, "deleted": safe_id}


@router.post("/fetch-url", response_model=FetchUrlResponse)
def fetch_url_endpoint(payload: FetchUrlRequest):
    result = fetch_url(payload.url, with_images=payload.with_images)
    return result


def _project_dir_name(script_id: str, now: datetime | None = None) -> str:
    """Generate project directory name: YYYYMMDD_HHMMSS_{last6}."""
    now = now or datetime.now(timezone.utc)
    suffix = re.sub(r"[^a-zA-Z0-9]", "", script_id)[-6:]
    return now.strftime("%Y%m%d_%H%M%S_") + suffix


def _create_project_dirs(script_id: str, project_root: Path) -> Path:
    """Create project folder with audio/clips/materials subdirs."""
    projects_dir = project_root / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_dir = projects_dir / _project_dir_name(script_id)
    for sub in ("audio", "clips", "materials", "tmp"):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
    return project_dir


def get_llm() -> LLMService:
    # 2026-09-09 用户令: kimi 主力 + deepseek 兜底 (统一工厂)
    return get_llm_service()


@router.post("", response_model=ArticleOut)
def create_article(payload: ArticleCreate, db: Session = Depends(get_db)):
    article = Article(
        title=payload.title,
        source_url=payload.source_url,
        raw_text=payload.raw_text,
        track=payload.track or "tech",
        # 证据图管线①: 抓取时「搜图」开关抓到的本页图随稿入库 (非空=总闸开)
        images_json=[img for img in (payload.images or []) if isinstance(img, dict) and img.get("url")] or None,
        status="pending",
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


@router.get("", response_model=list[ArticleOut])
def list_articles(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    return db.query(Article).order_by(Article.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/{article_id}", response_model=ArticleOut)
def get_article(article_id: str, db: Session = Depends(get_db)):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@router.put("/{article_id}", response_model=ArticleOut)
def update_article(article_id: str, payload: ArticleUpdate, db: Session = Depends(get_db)):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if payload.title is not None:
        article.title = payload.title
    if payload.source_url is not None:
        article.source_url = payload.source_url
    if payload.raw_text is not None:
        article.raw_text = payload.raw_text
    db.commit()
    db.refresh(article)
    return article


@router.post("/{article_id}/deconstruct")
def run_deconstruct(article_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """评论层独立解构 (2026-08-15): 复刻观众反应 + 评论区人设, 落 article.deconstruct_json.

    洗稿时也会自动跑并覆盖; 此端点供新闻线索页在洗稿前单独触发 (选题洞察)。
    """
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    job_id = str(uuid.uuid4())

    def _do_deconstruct():
        if get_session_maker() is None:
            _publish(job_id, {"type": "deconstruct_error", "error": "Database not initialized"})
            return
        with db_session() as db2:
            try:
                _publish(job_id, {"type": "deconstruct_start", "msg": "解构观众反应…"})
                a = db2.query(Article).filter(Article.id == article_id).first()
                if not a:
                    _publish(job_id, {"type": "deconstruct_error", "error": "Article not found"})
                    return
                from ..services.boost_service import deconstruct_article

                result = deconstruct_article(a.raw_text, track=getattr(a, "track", "tech") or "tech")
                if not result:
                    _publish(job_id, {"type": "deconstruct_error", "error": "解构输出解析失败，请重试"})
                    return
                a.deconstruct_json = result
                db2.commit()
                _publish(job_id, {"type": "deconstruct_done", "msg": f"解构完成：{len(result.get('reactions') or [])} 条观众反应"})
            except Exception as exc:
                logger.exception("[deconstruct] standalone failed for %s: %s", article_id, exc)
                _publish(job_id, {"type": "deconstruct_error", "error": str(exc)})

    background_tasks.add_task(_do_deconstruct)
    return {"job_id": job_id, "status": "started"}


@router.post("/{article_id}/rewrite")
def rewrite_article(
    article_id: str,
    request: RewriteRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    llm: LLMService = Depends(get_llm),
):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    job_id = str(uuid.uuid4())
    model_alias = request.model
    video_format = request.video_format or "portrait"
    perspective = request.perspective

    def _do_rewrite():
        _tpl = request.prompt_template
        if get_session_maker() is None:
            _publish(job_id, {"type": "rewrite_error", "error": "Database not initialized"})
            return
        with db_session() as db2:
            try:
                article = db2.query(Article).filter(Article.id == article_id).first()
                if not article:
                    _publish(job_id, {"type": "rewrite_error", "error": "Article not found"})
                    return

                # 赛道默认模板 (2026-08-16): geo 稿未显式选模板时走 laotan-geo
                # (必须在 article 查询之后 — 此前放在函数开头引用未定义的 article,
                #  NameError 杀线程且无 rewrite_error 事件 → 前端永久等待卡死)
                # 0909: tech 缺省从 laochen_default (老陈/Fish TTS 遗产) 换 laotan-tech_7layer_v2
                if not _tpl:
                    _tpl = "laotan-geo" if (getattr(article, "track", "tech") == "geo") else "laotan-tech_7layer_v2"


                # 数字人绑定
                host = None
                persona: Persona | None = None
                if request.persona_id:
                    persona = db2.query(Persona).filter(Persona.id == request.persona_id).first()
                    if not persona:
                        _publish(job_id, {"type": "rewrite_error", "error": "Persona not found"})
                        return
                    host = db2.query(Host).filter(Host.id == persona.host_id).first() if persona.host_id else None
                    # 人物即账号: persona 自带提示词模板, 覆盖请求里的 prompt_template
                    if persona.prompt_template:
                        _tpl = persona.prompt_template
                        # 赛道校验 (2026-08-25): geo 稿被通用 persona(如"老谭"→laotan)接管会
                        # 整链偏航 — geo 模板的合规红线/收口四拍/结构技法全丢, 还会漏进
                        # "点赞给"类违禁句式。track 与模板族不匹配 → 强制换赛道模板。
                        _track = (getattr(article, "track", "") or "").lower()
                        _tpl_l = (_tpl or "").lower()
                        if _track == "geo" and "geo" not in _tpl_l:
                            logger.warning(
                                "[rewrite] persona 模板 %s 与赛道 geo 不匹配, 强制切 laotan-geo",
                                _tpl,
                            )
                            _publish(job_id, {"type": "rewrite_progress",
                                              "msg": f"人设模板「{_tpl}」与地缘赛道不匹配，已自动切换 laotan-geo"})
                            _tpl = "laotan-geo"
                        elif _track == "tech" and "tech" not in _tpl_l:
                            logger.warning(
                                "[rewrite] persona 模板 %s 与赛道 tech 不匹配, 强制切 laotan-tech_7layer_v2",
                                _tpl,
                            )
                            _tpl = "laotan-tech_7layer_v2"

                if host is None and request.host_id:
                    host = db2.query(Host).filter(Host.id == request.host_id).first()
                    if not host:
                        _publish(job_id, {"type": "rewrite_error", "error": "Host not found"})
                        return
                if host is None and request.persona_id:
                    # persona 已显式指定但未绑 host: 视为绑定失败, 不让老陈兜底冒名顶替
                    _publish(job_id, {"type": "rewrite_error", "error": "Persona has no bound host"})
                    return
                if host is None:
                    from ..config import get_config

                    try:
                        cfg = get_config()
                        default_key = cfg.defaults.host_id if cfg.defaults else "laochen"
                    except Exception:
                        default_key = "laochen"
                    host = (
                        db2.query(Host).filter(Host.persona_key == default_key).first()
                        or db2.query(Host).order_by(Host.created_at).first()
                    )
                host_id = host.id if host else None

                chunks: list[str] = []

                def _cb(chunk: str) -> None:
                    chunks.append(chunk)
                    _publish(job_id, {"type": "rewrite_chunk", "chunk": chunk})

                # 保存补充观点到 article
                if perspective and perspective.strip():
                    article.perspective_1 = perspective.strip()
                    db2.commit()

                # 解构层 (2026-08-11): 洗稿前先解构新闻, 产出"伪用户评论"注入,
                # 让 laotan 从观众疑问出发成稿 (而非裸洗原文). 失败回退普通洗稿.
                decon_result = None
                raw_for_rewrite = article.raw_text
                try:
                    from ..services.boost_service import deconstruct_article, format_pseudo_comments

                    # 缓存复用 (2026-08-25): /deconstruct 端点已落过库且原文未变
                    # (_raw_sha 匹配) → 直接复用, 免每次洗稿白烧一次解构 LLM;
                    # sha 不匹配或旧数据无键 → 照旧重跑并落新 sha (自愈)。
                    import hashlib as _hl
                    _cur_sha = _hl.sha1((article.raw_text or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:16]  # noqa: S324 — 缓存指纹非密码学, 与 boost_service._raw_sha 同算法
                    _cached = article.deconstruct_json or None
                    if (
                        isinstance(_cached, dict)
                        and _cached.get("_raw_sha") == _cur_sha
                        and _cached.get("reactions")
                    ):
                        decon_result = _cached
                        _publish(job_id, {"type": "deconstruct_done", "msg": "解构复用缓存（原文未变）"})
                    else:
                        _publish(job_id, {"type": "deconstruct_start", "msg": "解构层：复刻观众反应"})
                        decon_result = deconstruct_article(
                            article.raw_text,
                            track=getattr(article, "track", "tech") or "tech",
                        )
                    if decon_result:
                        pseudo = format_pseudo_comments(decon_result)
                        if pseudo:
                            # 伪用户评论追加到原文后, 作为 laotan 输入的一部分
                            raw_for_rewrite = article.raw_text + "\n\n" + pseudo
                            _publish(job_id, {"type": "deconstruct_done", "msg": f"解构完成：{len(decon_result['reactions'])} 条观众反应"})
                            # 评论层持久化到文章 (2026-08-15): 新闻线索页「评论层」面板展示
                            article.deconstruct_json = decon_result
                            db2.commit()
                except Exception as decon_exc:
                    logger.warning("[deconstruct] failed, fallback to normal rewrite: %s", decon_exc)

                # 素材聚合 (2026-08-15): material_package_id 存在时, 素材包按层
                # 分桶注入 raw_for_rewrite 尾部 (顺序: 原文 → 伪评论 → 素材包).
                # 无包路径零改动; llm_service 不感知素材包.
                material_package_id: str | None = None
                if request.material_package_id:
                    from ..models import MaterialPackage
                    from ..services.material_service import build_material_context_block

                    pkg = (
                        db2.query(MaterialPackage)
                        .filter(MaterialPackage.id == request.material_package_id)
                        .first()
                    )
                    if not pkg or pkg.article_id != article.id:
                        _publish(job_id, {"type": "rewrite_error", "error": "素材包不存在或不属于该稿件"})
                        return
                    ok_items = [it for it in pkg.items if it.fetch_ok and it.raw_text]
                    material_block = build_material_context_block(ok_items, pkg.audit_json)
                    if material_block:
                        raw_for_rewrite = raw_for_rewrite + "\n\n" + material_block
                    material_package_id = pkg.id

                # 0910 素材隔离: 原文+素材包 = 外部内容, 包隔离标签后进洗稿提示词
                from ..services.prompt_guard import wrap_source
                script_text = llm.rewrite_article(
                    wrap_source(raw_for_rewrite, label="新闻原文与素材"),
                    prompt_template=_tpl,
                    model=model_alias,
                    stream=True,
                    chunk_callback=_cb,
                    perspective=perspective,
                )

                script = Script(
                    article_id=article.id,
                    host_id=host_id,
                    version=1,
                    prompt_template=_tpl,
                    script_text=script_text,
                    video_format=video_format,
                    status="drafting",
                )
                # 解构层产物落库 (2026-08-12): 洗稿时存 decon_result, 供爆品改造
                # P1/P2 读取注入 (钩子戳痛点/争议对焦虑)。deconstruct 失败时为 None。
                if decon_result:
                    script.deconstruct_json = decon_result
                # 素材包回溯 (2026-08-15): 记录洗稿用了哪个素材包
                if material_package_id:
                    script.material_package_id = material_package_id
                db2.add(script)
                db2.commit()
                db2.refresh(script)

                project_root = Path(__file__).resolve().parents[2]
                project_dir = _create_project_dirs(script.id, project_root)
                script.project_dir = str(project_dir)
                db2.commit()
                db2.refresh(script)

                # 开结尾: 人物(persona)显式编辑优先, 其次 host 兼容老数据 (2026-08-08)
                fixed_opening = (persona.fixed_opening if persona else None) or (host.fixed_opening if host else None)
                fixed_ending = (persona.fixed_ending if persona else None) or (host.fixed_ending if host else None)
                segments_data = parse_script(script_text, fixed_opening, fixed_ending)
                for seg_data in segments_data:
                    from ..models import Segment

                    db2.add(Segment(script_id=script.id, **seg_data))
                db2.commit()

                # 爆品改造改为手动触发 (2026-08-11): 洗稿只产出底稿, 用户看稿/改观点后
                # 再点独立"爆品改造"端点 (POST /scripts/{id}/boost) 触发 P1-P3.
                # 不再洗稿后自动跑, 给人工控制点.
                _publish(job_id, {
                    "type": "boost_done",
                    "script_id": script.id,
                    "boosted": False,
                    "msg": "洗稿完成, 可手动触发爆品改造",
                })

                article.status = "rewritten"
                db2.commit()

                _publish(job_id, {"type": "rewrite_done", "script_id": script.id, "project_dir": str(project_dir)})
            except Exception as exc:
                try:
                    article = db2.query(Article).filter(Article.id == article_id).first()
                    if article:
                        article.status = "failed"
                        db2.commit()
                except Exception:
                    pass
                _publish(job_id, {"type": "rewrite_error", "error": str(exc)})

    background_tasks.add_task(_do_rewrite)
    return {"job_id": job_id, "status": "started", "model": model_alias or "default"}
