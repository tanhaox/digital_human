"""Broll Pexels 线: 本地优先碰撞 + 降维搜索 + 同片去重 + 写回 pexels_id.

对应 _EXECUTION_PHASES 的 P 线 (broll_pexels / mixed_host_broll 背景)。
本地素材匹配见 broll.py; black 兜底见 fallback.py。

本地优先碰撞 (2026-08-09): P 线不再无脑下载 —— 用导演的 9 维度 + orientation
+ keywords 先碰撞本地库 (match_local_assets, 同 broll_local 标准), 命中即用
本地, 未命中才走 Pexels 在线降维搜索。chosen_pexels_id=None 表示本地命中,
不写回 params, 不污染 Pexels 同片去重。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_config
from app.infrastructure import render_scale_pad
from app.models import DirectorSlot
from app.schemas import get_video_format_spec
from app.services.asset_matcher import MIN_HIT_RATIO, match_local_assets, register_asset_usage
from app.services.pexels_service import pexels_service
from app.services.slot_workflows.common import ensure_slot_dir

logger = logging.getLogger(__name__)

__all__ = [
    "execute_broll_pexels_slot",
    "_collect_used_pexels_ids",
    "_resolve_pexels",
    "_persist_chosen_pexels_id",
    "_try_local_collision",
]


def _collect_used_pexels_ids(db: Session, slot: DirectorSlot) -> set[int]:
    """Collect Pexels video ids already used by earlier completed broll slots.

    同片不重复素材约束 (2026-08-01): 遍历同 job 已完成且已持久化
    ``params_json["pexels_id"]`` 的 broll 类 slot, 收集已用过的 id。
    仅收集本 slot 之前 (slot_index 更小) 的 slot, 保证时间顺序、避免自引用。
    """
    job_id = slot.director_job_id
    rows = (
        db.query(DirectorSlot)
        .filter(
            DirectorSlot.director_job_id == job_id,
            DirectorSlot.slot_index < slot.slot_index,
            DirectorSlot.workflow.in_(("broll_pexels", "mixed_host_broll")),
            DirectorSlot.status == "completed",
        )
        .all()
    )
    used: set[int] = set()
    for r in rows:
        pid = (r.params_json or {}).get("pexels_id")
        if pid is not None:
            try:
                used.add(int(pid))
            except (TypeError, ValueError):
                pass
    return used


def _collect_used_local_files(db: Session, slot: DirectorSlot) -> list[str]:
    """Collect local material file_paths already used in this job.

    同 job 本地素材硬排除 (2026-08-12): 遍历同 job 已完成且已写回
    ``params_json["local_file"]`` 的 broll 类 slot, 收集已用过的本地素材
    file_path。**只按 status=completed 过滤, 不按 slot_index 收紧** ——
    mixed_host_broll 在 phase 0、broll_pexels 在 phase 1, 跨 phase 时
    slot_index 递增会漏掉已用素材; 按 completed + 排除自身 (当前 slot 是
    running 自然排除) 即可全覆盖。
    """
    job_id = slot.director_job_id
    rows = (
        db.query(DirectorSlot)
        .filter(
            DirectorSlot.director_job_id == job_id,
            DirectorSlot.status == "completed",
            DirectorSlot.workflow.in_(("broll_pexels", "broll_local", "mixed_host_broll")),
        )
        .all()
    )
    used: list[str] = []
    for r in rows:
        if r.id == slot.id:
            continue  # 排除自身 (防御: 当前 slot 不应是 completed)
        lf = (r.params_json or {}).get("local_file")
        if lf:
            used.append(lf)
    return used


def _try_local_collision(
    db: Session, slot: DirectorSlot, min_dur: int, orientation: str,
) -> tuple[Path, str] | None:
    """本地优先碰撞 (2026-08-09): 用导演的 9 维度碰撞本地素材库.

    与 broll_local 同标准 (ID-034): 硬维度硬过滤 + 软维度命中率 ≥75%。
    关键差异 —— 精确时长防护: render_scale_pad 无 loop, 素材短于 slot 时长
    会黑尾截断, 因此额外要求素材 duration_sec >= slot 时长 (min_dur)。
    命中返回 (本地路径, 描述); 未命中返回 None 走 Pexels 在线降维搜索。
    """
    params = slot.params_json or {}
    # ── 实体靶心通道 (2026-08-28 开闸验证): 实体命中 > 一切 9 维碰撞策略 ──
    # 官片实体素材(英伟达/小米官频切片)是"正确画面"本身, 绕过门槛/strict 档位;
    # 未命中照旧走原碰撞/在线下载。
    ent_names = params.get("entities") or []
    if ent_names:
        try:
            from app.models import DirectorJob
            from app.services.asset_matcher import match_entity_bullseye
            _job = db.query(DirectorJob).filter(
                DirectorJob.id == slot.director_job_id).first()
            pool = ((_job.plan_json or {}).get("material_entities")) if _job else []
            # 人物实体优先纯打靶 (2026-08-30 用户反馈: 混合查询里 "japan" 泛词在
            # 特朗普切片描述+8 分, 压过她本人切片 → 3 个镜头全是会见特朗普) —
            # slot 含 person 类实体时只用该人物自己的 query+中文名, 不混机构/国家泛词;
            # 无 person 实体才用全实体合并查询。
            slot_pool = [e for e in (pool or []) if e.get("name") in ent_names]
            persons = [e for e in slot_pool if e.get("type") == "person"]
            source_ents = persons or slot_pool
            _queries = [q.lower() for e in source_ents
                        for q in (e.get("queries") or {}).values() if q]
            _queries += [e["name"].lower() for e in source_ents]
            if _queries:
                hits = match_entity_bullseye(
                    db, _queries, min_duration_sec=float(min_dur),
                    orientation=orientation,
                    exclude=_collect_used_local_files(db, slot), limit=3)
                if hits:
                    best = hits[0]
                    # 使用登记三件套 (2026-08-28 修复: 同一素材反复用的根因):
                    # ① register_asset_usage → 全局 used_count+1 (轮换排序依据)
                    # ② 写回 params.local_file → 同 job 硬排除列表才会长
                    # ③ 未写回时 _collect_used_local_files 永远为空 → 每 slot
                    #    都拿同一最高分素材 (用户复验实测 0003×3/0012×7)
                    register_asset_usage(db, best["file_path"])
                    _np = dict(slot.params_json or {})
                    _np["local_file"] = best["file_path"]
                    slot.params_json = _np
                    logger.info("[broll_pexels] slot %s 实体靶心命中官片: %s (实体 %s)",
                                slot.slot_index, best["asset_no"], ent_names)
                    return Path(best["file_path"]), f"实体官片:{ent_names[0]}"
        except Exception as exc:  # noqa: BLE001 — 靶心失败静默回退常规碰撞
            logger.warning("[broll_pexels] 实体靶心异常(回退): %s", exc)

    # P 线本地碰撞策略 (2026-08-16 用户反馈: 本地权重太高, 烂素材反复用):
    #   off    = 完全跳过本地碰撞, 全走 Pexels 新下载
    #   strict = 仅强命中才用本地 (hit_ratio ≥ 0.85, 远高于默认 75%)
    #   normal = 原行为 (≥ MIN_HIT_RATIO)
    cfg = get_config()
    collision_mode = getattr(cfg.defaults, "p_line_local_collision", "strict")
    if collision_mode == "off":
        return None
    # 无门槛维约束 (scenes/shot_types/tone) → 碰撞标准不成立, 直接跳过。
    # 仅 keywords/加分维(motion/content/time) 不足以支撑精准碰撞, 宁可直接下载
    # (宁可错过, 不摆烂)。 (2026-08-12 门槛重构)
    if not any([
        params.get("scenes"), params.get("shot_types"),
        params.get("tone"),
    ]):
        return None
    results = match_local_assets(
        db,
        keywords=params.get("keywords"),
        scenes=params.get("scenes"),
        shot_types=params.get("shot_types"),
        tone=params.get("tone"),
        motion_level=params.get("motion_level"),
        content_density=params.get("content_density"),
        time_of_day=params.get("time_of_day"),
        orientation=orientation,
        location=params.get("location"),
        people=params.get("people"),
        min_duration_sec=float(min_dur),
        limit=1,
        relax_location=True,  # 地域 C 折中 (2026-08-12): strict 为空才放宽 foreign
        exclude=_collect_used_local_files(db, slot),  # 同 job 硬排除 (2026-08-12)
    )
    if not results:
        return None
    best = results[0]
    hit_ratio = best.get("hit_ratio")
    min_ratio = MIN_HIT_RATIO
    if collision_mode == "strict":
        min_ratio = max(MIN_HIT_RATIO, 0.85)
    if hit_ratio is not None and hit_ratio < min_ratio:
        logger.info(
            "[broll_pexels] slot %d: local collision hit_ratio=%.2f < %.0f%% (%s), fall through to pexels",
            slot.slot_index, hit_ratio, min_ratio * 100, collision_mode,
        )
        return None
    src = Path(best["file_path"])
    if not src.exists():
        return None
    register_asset_usage(db, best["file_path"])
    # 同 job 素材硬排除: 写回本次命中的本地素材, 供后续 slot 收集排除 (2026-08-12)
    # params_json 是普通 JSON 列, 必须新建 dict 整体赋值才能触发落库。
    if best["file_path"]:
        new_params = dict(slot.params_json or {})
        new_params["local_file"] = best["file_path"]
        slot.params_json = new_params
    # 地域 C 折中: 命中放宽的 foreign 素材不静默 —— 显式标记 (2026-08-12)
    relaxed = bool(best.get("location_relaxed"))
    logger.info(
        "[broll_pexels] slot %d: LOCAL COLLISION HIT %s (score=%d, hit_ratio=%s, dur=%.1fs >= %ds%s)",
        slot.slot_index, src.name, best.get("score", 0), hit_ratio,
        best.get("duration_sec") or 0.0, min_dur,
        ", LOCATION_RELAXED(foreign)" if relaxed else "",
    )
    desc = f"local_collision:{src.name}"
    if relaxed:
        desc += "?relaxed=location"
    return src, desc


# 方位词剥离 (2026-08-15): 旧版导演提示词教 LLM 把 vertical/portrait 写进 keywords,
# 与 job 画幅矛盾时 Pexels 全文搜索返回反方向视频 → 方向过滤器全拒 → 素材报错。
# 方向已由 API orientation 参数传达, 关键词里的方位词只帮倒忙, 一律剥离。
_ORIENTATION_WORDS = {"vertical", "portrait", "horizontal", "landscape"}


def _strip_orientation_words(keywords: list[str]) -> list[str]:
    cleaned = [k for k in keywords if str(k).strip().lower() not in _ORIENTATION_WORDS]
    return cleaned or keywords  # 全被滤掉(如只有方位词)时退回原词保底


# 抽象概念词剥离 (2026-08-15, ID-050): LLM 仍会输出 abstract/泛科技词, 在 Pexels
# 返回的是烂大街 Neural-Network 特效图。整词命中的直接滤掉(降维重试会依次缩词)。
_ABSTRACT_WORDS = {
    "artificial intelligence", "intelligence", "technology", "digital",
    "digital graph", "data", "big data", "innovation", "future",
    "internet", "ai", "network", "science technology", "data center",
}


def _strip_abstract_words(keywords: list[str]) -> list[str]:
    cleaned = [k for k in keywords if str(k).strip().lower() not in _ABSTRACT_WORDS]
    return cleaned or keywords  # 全滤光时退回原词保底(交给 Pexels 自身兜底)


def _resolve_pexels(
    db: Session, slot: DirectorSlot, used_ids: set[int],
    min_dur: int, orientation: str,
) -> tuple[Path, str, object]:
    """Resolve a single Pexels source clip via keyword-descending or legacy query.

    Returns (local_path, desc, chosen_pexels_id). desc is used in error/log
    messages; chosen_pexels_id is persisted for the same-job dedup constraint.
    """
    # 本地优先碰撞 (2026-08-09): 命中即用本地, 未命中才走 Pexels 在线搜索。
    # chosen_pexels_id=None → 调用方不写回 params, 不污染 Pexels 同片去重。
    # force_pexels (2026-08-12): 强制 P 线下载, 跳过本地碰撞 (用于本地/替换走死时)。
    if not (slot.params_json or {}).get("force_pexels"):
        local = _try_local_collision(db, slot, min_dur, orientation)
        if local is not None:
            src, desc = local
            return src, desc, None

    params = slot.params_json or {}
    keywords = params.get("keywords")
    used_query: str | None = None
    if isinstance(keywords, list) and keywords:
        clean_keywords = _strip_abstract_words(
            _strip_orientation_words([str(k) for k in keywords])
        )
        items, used_query = pexels_service.resolve_descending(
            clean_keywords, max_results=1,
            min_duration_sec=min_dur, orientation=orientation,
            exclude_pexels_ids=used_ids,
        )
        desc = f"keywords={clean_keywords} hit_query={used_query}"
    else:
        # 兼容旧 params: category / text_context 单次搜索
        category = params.get("category") or slot.text_context or "business"
        items = pexels_service.resolve(
            category, max_results=5, min_duration_sec=min_dur,
            orientation=orientation, exclude_pexels_ids=used_ids,
        )
        used_query = category
        desc = f"query={category}"

    usable = [i for i in items if i.local_path and Path(i.local_path).exists()]
    if not usable:
        raise RuntimeError(f"no usable Pexels material for {desc}")
    chosen = usable[0]
    return Path(chosen.local_path), desc, (chosen.pexels_id or chosen.id)


def _persist_chosen_pexels_id(db: Session, slot: DirectorSlot, chosen_id) -> None:
    """Write the selected pexels_id back to slot.params_json (唯一素材约束)."""
    new_params = dict(slot.params_json or {})
    new_params["pexels_id"] = chosen_id
    slot.params_json = new_params
    db.commit()
    logger.info("[broll_pexels] slot %d -> pexels_id=%s", slot.slot_index, chosen_id)


def execute_broll_pexels_slot(db: Session, slot: DirectorSlot) -> str:
    """Resolve Pexels video (降维搜索优先), trim to slot duration, return mp4 path.

    本地优先碰撞 (2026-08-09): 先用 9 维度碰撞本地库 (match_local_assets),
    命中即渲染本地素材 (不写回 pexels_id, 不占用 Pexels 同片去重额度);
    未命中才走降维搜索。

    降维搜索 (2026-08-01): LLM 给出按重要性排序的关键词数组
    ``params.keywords``(≤6, 第 1 个是全局主体关键词)。API 侧每次去掉末尾
    1 个词重搜, 命中即停, 主关键词永远保留在查询里 → 保证全片地域/主题一致性。
    兼容旧 params: 无 keywords 时退化为 category/query 单次搜索。

    同片不重复素材 (2026-08-01): 一个 Pexels 视频 (pexels_id) 在同一 DirectorJob
    中只允许用一次。已用过的 id 会传给 resolve 排除, 选中后写回
    slot.params_json["pexels_id"] 持久化, 供后续 slot 排除。
    """
    root = ensure_slot_dir(slot)
    spec = get_video_format_spec(slot.director_job.video_format)
    duration = round(slot.end_sec - slot.start_sec, 3)
    min_dur = int(duration) or 5
    orientation = spec["pexels_orientation"]

    # 同一 job 内已用过的 Pexels 素材 id (从已完成 broll slot 的 params 收集)
    used_ids: set[int] = set()
    try:
        used_ids = _collect_used_pexels_ids(db, slot)
    except Exception:  # noqa: BLE001
        logger.warning("collect used pexels ids failed, fallback empty set", exc_info=True)
    if used_ids:
        logger.info("[broll_pexels] job already used %d material(s), excluding them", len(used_ids))

    src, desc, chosen_pexels_id = _resolve_pexels(db, slot, used_ids, min_dur, orientation)
    if chosen_pexels_id is not None:
        _persist_chosen_pexels_id(db, slot, chosen_pexels_id)

    out_path = root / f"broll_pexels_{slot.slot_index:03d}.mp4"
    render_scale_pad(
        src, out_path,
        width=spec["width"], height=spec["height"], duration=duration,
    )
    return str(out_path)
