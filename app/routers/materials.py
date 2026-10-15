# -*- coding: utf-8 -*-
"""素材包 router (2026-08-15): 多源聚合 / 七层覆盖审计 / 智谱定向补搜.

后台 job 照抄 scripts._run_boost_background daemon-thread 模式:
抓取(可选) → 补搜(可选) → 审计 → 落库 + SSE (material_* 事件).
"""
from __future__ import annotations

import logging
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.services.job_events import _publish

from ..database import db_session, get_db
from ..models import Article, MaterialIngestJob, MaterialItem, MaterialPackage
from ..schemas.materials import (
    MaterialItemAddRequest,
    MaterialItemOut,
    MaterialPackageBriefOut,
    MaterialPackageOut,
    PackageCreateRequest,
    SupplementSearchRequest,
)
from ..services import material_service
from ..services.url_fetcher import fetch_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/materials", tags=["materials"])


def _brief(pkg: MaterialPackage) -> MaterialPackageBriefOut:
    return MaterialPackageBriefOut(
        id=pkg.id,
        article_id=pkg.article_id,
        title=pkg.title,
        status=pkg.status,
        search_rounds=pkg.search_rounds,
        error_message=pkg.error_message,
        created_at=pkg.created_at,
        updated_at=pkg.updated_at,
        item_count=len(pkg.items),
    )


def _detail(pkg: MaterialPackage, article: Article | None = None) -> MaterialPackageOut:
    # 解构层 research 资料清单并入补搜候选池 (2026-08-16: 此前只躺在评论层面板)
    research_hints = []
    if article is not None and article.deconstruct_json:
        research_hints = [str(r) for r in (article.deconstruct_json.get("research") or []) if r]
    return MaterialPackageOut(
        id=pkg.id,
        article_id=pkg.article_id,
        title=pkg.title,
        status=pkg.status,
        audit_json=pkg.audit_json,
        search_rounds=pkg.search_rounds,
        error_message=pkg.error_message,
        created_at=pkg.created_at,
        updated_at=pkg.updated_at,
        items=[MaterialItemOut.model_validate(it) for it in pkg.items],
        gap_queries=material_service.collect_gap_queries(pkg.audit_json, research_hints),
    )


def _scan_with_events(db2, pkg: MaterialPackage, job_id: str) -> None:
    """证据图扫图 + SSE 事件 (证据图管线①/②, 2026-09-04).

    best-effort: 任何失败只发 material_scan_error, 不动包状态。
    """
    from ..services import evidence_service

    def _cb(stage: str, payload: dict) -> None:
        if stage == "download_start":
            _publish(job_id, {"type": "material_scan_start",
                              "package_id": pkg.id, "total": payload.get("total", 0)})
        elif stage == "tag":
            _publish(job_id, {"type": "material_scan_progress",
                              "package_id": pkg.id,
                              "done": payload.get("done", 0), "total": payload.get("total", 0)})

    try:
        stats = evidence_service.scan_package_images(db2, pkg, progress_cb=_cb)
        _publish(job_id, {"type": "material_scan_done", "package_id": pkg.id, **stats})
    except Exception as exc:  # noqa: BLE001 — 扫图失败不拖垮素材包主流程
        logger.warning("[material] scan-images failed for %s: %s", pkg.id, exc, exc_info=True)
        _publish(job_id, {"type": "material_scan_error", "package_id": pkg.id, "error": str(exc)[:200]})


def _run_scan_job(package_id: str, job_id: str) -> None:
    """手动补扫后台线程 (不受总闸限制, 老包回填用)."""
    with db_session() as db2:
        pkg = db2.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
        if not pkg:
            _publish(job_id, {"type": "material_error", "package_id": package_id, "error": "素材包不存在"})
            return
        _scan_with_events(db2, pkg, job_id)


def _run_package_job(
    package_id: str,
    job_id: str,
    *,
    urls: list[str] | None = None,
    supplement_queries: list[str] | None = None,
) -> None:
    """素材包后台线程: 抓取(可选) → 补搜(可选) → 审计 → 扫图(可选) → 落库 + SSE."""
    with db_session() as db2:
        try:
            pkg = db2.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
            if not pkg:
                _publish(job_id, {"type": "material_error", "package_id": package_id, "error": "素材包不存在"})
                return
            article = db2.query(Article).filter(Article.id == pkg.article_id).first()
            if not article:
                _publish(job_id, {"type": "material_error", "package_id": package_id, "error": "主稿不存在"})
                return
            pkg.status = "collecting"
            pkg.error_message = None
            db2.commit()

            # 1) 批量抓取 (建包时)
            if urls:
                results = material_service.batch_fetch_urls(urls)
                for i, res in enumerate(results, start=1):
                    db2.add(MaterialItem(
                        package_id=pkg.id,
                        source_type="url",
                        title=res["title"] or None,
                        source_url=res["url"],
                        raw_text=res["raw_text"],
                        fetch_ok=res["ok"],
                        char_count=len(res["raw_text"]),
                    ))
                    if i % 2 == 0 or i == len(results):
                        _publish(job_id, {
                            "type": "material_fetch_progress",
                            "package_id": pkg.id, "done": i, "total": len(results),
                        })
                db2.commit()
                db2.refresh(pkg)

            # 2) 智谱定向补搜 (缺口搜索词)
            if supplement_queries:
                _publish(job_id, {
                    "type": "material_search_start",
                    "package_id": pkg.id, "queries": supplement_queries,
                })
                material_service.search_and_ingest(db2, pkg, supplement_queries)
                pkg.search_rounds += 1
                db2.commit()
                db2.refresh(pkg)

            # 3) 七层覆盖审计 (传上一轮结果: applicable 锁定, 防 LLM 判定翻转)
            _publish(job_id, {"type": "material_audit_start", "package_id": pkg.id})
            ok_items = [it for it in pkg.items if it.fetch_ok and it.raw_text]
            audit = material_service.audit_package(
                article.raw_text, ok_items, prev_audit=pkg.audit_json,
                track=getattr(article, "track", "tech") or "tech",
            )
            if audit is None:
                pkg.status = "failed"
                pkg.error_message = "审计 LLM 输出解析失败，请重试（重新审计）"
                db2.commit()
                _publish(job_id, {"type": "material_error", "package_id": pkg.id, "error": pkg.error_message})
                return
            pkg.audit_json = audit
            # 审计 item_tags 回填到条目 layer_tags;
            # item_numbers 映射 (2026-08-25): 精确按 item_id 查编号 — 修复旧版按
            # ok_items 位置反推在 fetch_ok 翻转时错位; 旧包无映射退化位置反推。
            _num_map = audit.get("item_numbers") or {}
            for i, item in enumerate(ok_items, start=1):
                n = _num_map.get(str(item.id), i)
                item.layer_tags = (audit.get("item_tags") or {}).get(str(n)) or None
            covered = sum(1 for l in audit["layers"].values() if l["covered"])
            pkg.status = "audited"
            db2.commit()

            # 4) 证据图扫图 (证据图管线①, 2026-09-04): 总闸 = 抓取时开了「搜图」
            # (article.images_json 非空)。失败 best-effort, 不置包 failed。
            if article.images_json:
                _scan_with_events(db2, pkg, job_id)

            _publish(job_id, {
                "type": "material_done",
                "package_id": pkg.id, "covered": covered, "total": 7,
            })
        except Exception as exc:
            logger.exception("[material] background job failed for %s: %s", package_id, exc)
            try:
                pkg = db2.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
                if pkg:
                    pkg.status = "failed"
                    pkg.error_message = str(exc)
                    db2.commit()
            except Exception:
                pass
            try:
                _publish(job_id, {"type": "material_error", "package_id": package_id, "error": str(exc)})
            except Exception:
                pass


def _spawn(package_id: str, *, urls: list[str] | None = None,
           supplement_queries: list[str] | None = None) -> str:
    """生成 job_id 并起 daemon 线程, 返回 job_id."""
    job_id = uuid.uuid4().hex[:12]
    threading.Thread(
        target=_run_package_job,
        kwargs={
            "package_id": package_id,
            "job_id": job_id,
            "urls": urls,
            "supplement_queries": supplement_queries,
        },
        daemon=True,
    ).start()
    return job_id


@router.post("/packages")
def create_package(payload: PackageCreateRequest, db: Session = Depends(get_db)):
    article = db.query(Article).filter(Article.id == payload.article_id).first()
    if not article:
        raise HTTPException(404, "Article not found")
    pkg = MaterialPackage(article_id=article.id, title=payload.title or article.title)
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    job_id = _spawn(pkg.id, urls=payload.urls)
    return {"id": pkg.id, "job_id": job_id, "status": pkg.status}


@router.get("/packages")
def list_packages(article_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(MaterialPackage)
    if article_id:
        q = q.filter(MaterialPackage.article_id == article_id)
    pkgs = q.order_by(MaterialPackage.created_at.desc()).all()
    return [_brief(p) for p in pkgs]


@router.get("/packages/{package_id}")
def get_package(package_id: str, db: Session = Depends(get_db)):
    pkg = db.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
    if not pkg:
        raise HTTPException(404, "Material package not found")
    article = db.query(Article).filter(Article.id == pkg.article_id).first()
    return _detail(pkg, article)


@router.post("/packages/{package_id}/items", response_model=MaterialItemOut)
def add_item(package_id: str, payload: MaterialItemAddRequest, db: Session = Depends(get_db)):
    pkg = db.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
    if not pkg:
        raise HTTPException(404, "Material package not found")
    if payload.url:
        # 证据图管线①: 手动加 url 条目也顺手提图 (进包后由手动补扫下载/打标)
        res = fetch_url(payload.url, with_images=True)
        item = MaterialItem(
            package_id=pkg.id,
            source_type="url",
            title=res.get("title") or payload.title,
            source_url=payload.url,
            raw_text=res.get("raw_text") or "",
            fetch_ok=bool(res.get("ok")),
            char_count=len(res.get("raw_text") or ""),
            images_json=(res.get("images") or None),
        )
    elif payload.text:
        item = MaterialItem(
            package_id=pkg.id,
            source_type="manual",
            title=payload.title,
            raw_text=payload.text,
            fetch_ok=True,
            char_count=len(payload.text),
        )
    else:
        raise HTTPException(422, "url 或 text 至少提供一项")
    pkg.status = "collecting"
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/packages/{package_id}/items/{item_id}")
def delete_item(package_id: str, item_id: str, db: Session = Depends(get_db)):
    item = (
        db.query(MaterialItem)
        .filter(MaterialItem.id == item_id, MaterialItem.package_id == package_id)
        .first()
    )
    if not item:
        raise HTTPException(404, "Material item not found")
    pkg = db.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
    if pkg:
        pkg.status = "collecting"
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.post("/packages/{package_id}/audit")
def run_audit(package_id: str, db: Session = Depends(get_db)):
    pkg = db.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
    if not pkg:
        raise HTTPException(404, "Material package not found")
    job_id = _spawn(pkg.id)
    return {"job_id": job_id}


@router.post("/packages/{package_id}/supplement-search")
def supplement_search(package_id: str, payload: SupplementSearchRequest, db: Session = Depends(get_db)):
    pkg = db.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
    if not pkg:
        raise HTTPException(404, "Material package not found")
    queries = [q.strip()[:70] for q in (payload.queries or []) if q.strip()]
    if not queries:
        queries = material_service.collect_gap_queries(pkg.audit_json)
    if not queries:
        raise HTTPException(400, "无可补搜的查询词（先跑审计或勾选搜索词）")
    job_id = _spawn(pkg.id, supplement_queries=queries)
    return {"job_id": job_id, "queries": queries}


@router.post("/packages/{package_id}/scan-images")
def scan_images(package_id: str, db: Session = Depends(get_db)):
    """手动补扫证据图 (证据图管线①, 2026-09-04): 不受「搜图」总闸限制, 老包回填用."""
    pkg = db.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
    if not pkg:
        raise HTTPException(404, "Material package not found")
    job_id = uuid.uuid4().hex[:12]
    threading.Thread(target=_run_scan_job, kwargs={
        "package_id": pkg.id, "job_id": job_id}, daemon=True).start()
    return {"job_id": job_id}


@router.delete("/packages/{package_id}")
def delete_package(package_id: str, db: Session = Depends(get_db)):
    pkg = db.query(MaterialPackage).filter(MaterialPackage.id == package_id).first()
    if not pkg:
        raise HTTPException(404, "Material package not found")
    db.delete(pkg)
    db.commit()
    return {"ok": True}


# ══ 素材摄入产线 (2026-08-30): 链接/实体批 → 下载→切片→OCR→LLM→入库 ════════

from pydantic import BaseModel as _BM  # noqa: E402


class IngestRequest(_BM):
    mode: str = "url"                # url / entity
    url: str | None = None           # mode=url
    entity: str | None = None        # mode=entity (须在 config/geo_entities.yaml)
    custom_search: str | None = None # 临时搜索词 (实体不在字典时用, entity 仍作标签)


@router.get("/ingest/vpn")
def ingest_vpn_status():
    """VPN 探针 (UI 徽章轮询)."""
    from ..services.material_ingest_service import vpn_state
    return vpn_state()


@router.get("/ingest/entities")
def ingest_entities(db: Session = Depends(get_db)):
    """实体字典 + 库存水位 (低于 min_stock 的标 need_refill)."""
    from ..services.material_ingest_service import entity_entry, load_entities, stock_count
    out = []
    for e in load_entities():
        out.append({**e, "stock": stock_count(db, e["name"]),
                    "need_refill": stock_count(db, e["name"]) < int(e.get("min_stock", 10))})
    return out


@router.post("/ingest")
def ingest_create(req: IngestRequest, db: Session = Depends(get_db)):
    from ..services import material_ingest_service as svc

    if req.mode == "url":
        if not req.url:
            raise HTTPException(400, "mode=url 需要 url")
        job = MaterialIngestJob(mode="url", source_url=req.url,
                                entity=req.entity or None, stage="queued")
    else:
        name = req.entity
        if not name:
            raise HTTPException(400, "mode=entity 需要 entity")
        ent = svc.entity_entry(name)
        if not ent and not req.custom_search:
            raise HTTPException(404, f"实体字典无「{name}」, 且未提供 custom_search")
        if not ent:  # 临时实体: 字典补一行内存态 (不改文件), 批完后即逝
            svc.load_entities.cache_clear() if hasattr(svc.load_entities, "cache_clear") else None
        job = MaterialIngestJob(mode="entity", entity=name, stage="queued",
                                stats_json={"custom_search": req.custom_search})
    db.add(job)
    db.commit()
    svc.start_job(job.id, job.mode)
    return {"id": job.id, "stage": job.stage}


@router.get("/ingest/jobs")
def ingest_jobs(limit: int = 50, parent: str | None = None, db: Session = Depends(get_db)):
    q = db.query(MaterialIngestJob)
    if parent:
        q = q.filter(MaterialIngestJob.parent_id == parent)
    rows = q.order_by(MaterialIngestJob.created_at.desc()).limit(limit).all()
    return [{"id": j.id, "mode": j.mode, "entity": j.entity, "title": j.title,
             "video_id": j.video_id, "stage": j.stage, "error": j.error,
             "stats": j.stats_json or {}, "parent_id": j.parent_id,
             "created_at": str(j.created_at)[:19]} for j in rows]


@router.post("/ingest/{job_id}/resume")
def ingest_resume(job_id: str, db: Session = Depends(get_db)):
    from ..services import material_ingest_service as svc
    job = db.query(MaterialIngestJob).filter(MaterialIngestJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "job not found")
    if job.stage not in ("paused_vpn_on", "paused_vpn_off", "failed",
                         "waiting_vpn_on", "waiting_vpn_off"):
        raise HTTPException(409, f"状态 {job.stage} 无需 resume")
    # waiting_* 孤儿 (服务重启杀掉等待线程) → queued 重入流水;
    # paused_* → 重新走门控 (满足则直通, 不满足重新等)
    job.stage = "queued"
    job.error = None
    db.commit()
    svc.start_job(job.id, "url")
    return {"ok": True, "stage": "queued"}
