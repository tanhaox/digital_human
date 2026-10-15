"""本地素材语义匹配服务 — 基于 AI 打标数据按 keywords/tags 检索 VideoAsset.

ID-034 (2026-08-07) + 2026-08-12 门槛重构匹配契约:
  - 硬维度 orientation / people → 独立列硬过滤, 必须全中, 否则排除
  - location → C 折中 (relax_location=True): 先 strict 全中, 候选为空才放宽
    foreign 并标记 location_relaxed=True (有 domestic 绝不用 foreign)
  - 门槛维 scenes / shot_types / tone → 命中率 ≥75% (3 中 ≥2) 才算符合画面
  - 加分维 motion_level / content_density / time_of_day → 命中加分, 不排除
  - keywords 是词表包里的画面词, 提供额外精确打分 (不与命中率绑定)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import String, and_, or_
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import VideoAsset

logger = logging.getLogger(__name__)

# 硬维度 (独立列, 硬过滤) / 软维度 (extra JSON + scenes/shot_types 列, 命中率)
HARD_DIMENSIONS = ("location", "orientation", "people")
SOFT_DIMENSIONS = (
    "scenes", "shot_types", "tone",
    "motion_level", "content_density", "time_of_day",
)
# 软维度命中率下限: ≥75% (3 中 ≥2)。已确认 (2026-08-12)。
MIN_HIT_RATIO = 0.75

# 门槛维 (必须有): 画面主体 scenes/shots + 决定性情绪 tone。
# 门槛只算这三维 (3 中 ≥2) —— motion/content/time 属"氛围性、标签易错位"
# 维度, 降为加分不排除 (2026-08-12 实测: 4 维门槛把"暗+机房+氛围全对"的
# 素材因 motion=static≠slow 刷成 25%, 42 条候选全灭 → 误杀本该命中的画面)。
GATE_DIMENSIONS = ("scenes", "shot_types", "tone")
# 加分维 (不参与门槛): 命中加分, 不命中不排除。
BONUS_DIMENSIONS = ("motion_level", "content_density", "time_of_day")

# 地域 C 折中 (2026-08-12): 本地库 foreign 素材占 1215/1672, 而导演 90%
# 标 domestic。location 硬过滤一刀砍死国际素材 → 先 strict 全中, 候选为 0
# 时才允许放宽 foreign 并标记。宁可错过(有 domestic 就绝不用 foreign)，
# 但绝不因地域稀缺而摆烂下载。
LOCATION_RELAX_ACTIVE = True


def match_entity_bullseye(
    db: Session,
    entity_queries: list[str],
    *,
    min_duration_sec: float | None = None,
    orientation: str | None = None,
    exclude: list[str] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """实体靶心通道 (素材层 2.0, 2026-08-27): 实体英文 query × 素材英文指纹.

    特朗普/航母这类真实画面实体, 命中 raw_query/description_en/tags 即为
    正确素材 — 绕过 9 维门槛 (tone 不对也是对的画面)。命中即给 100 分基分,
    供上层直接采用或与常规通道结果合并排序。
    entity_queries: 实体的英文搜索词 (entity['queries'] 的并集, 小写)。
    同源视频惩罚 (2026-08-30): exclude 里已用文件的父目录 (源视频) 记为
    已耗 — 同源切片 -40 分, 防一部记者会视频的不同切镜头连中造成重复感。
    """
    from pathlib import Path as _P
    used_dirs = {str(_P(f).parent).lower() for f in (exclude or [])}
    if not entity_queries:
        return []
    q = db.query(VideoAsset)
    q = q.filter(VideoAsset.preference != "dislike")
    if exclude:
        q = q.filter(~VideoAsset.file_path.in_(exclude))
    if orientation:
        q = q.filter(VideoAsset.orientation == orientation)
    # 冷却调度 (2026-08-28): 靶心通道同守 — 官片也不能短时间内反复
    q = _cooldown_filter(q, 10)
    from sqlalchemy import String as _S, or_
    like = lambda col, term: col.cast(_S).ilike(f"%{term}%")  # noqa: E731
    conds = []
    match_terms: list[str] = []
    # 词级拆分 (2026-08-30 修复: 实体 query 是长短语 "Sanae Takaichi cabinet
    # reshuffle press conference", 整短语子串匹配打不中片名只含人名的资产 —
    # 实测 takaichi 直中 3 条而整句 0 条。拆成 ≥5 字母词参与匹配; 泛词
    # (press/conference/speech/president...) 不单独匹配, 只留给整短语)。
    _GENERIC = {"press", "conference", "speech", "president", "minister",
                "government", "official", "national", "address", "cabinet"}
    for term in entity_queries:
        term = term.strip().lower()
        if len(term) < 3:
            continue
        conds.append(like(VideoAsset.raw_query, term))
        conds.append(like(VideoAsset.description_en, term))
        conds.append(like(VideoAsset.description_zh, term))
        match_terms.append(term)
        import re as _re
        for w in _re.split(r"[^0-9a-z一-鿿]+", term):
            if len(w) >= 5 and w not in _GENERIC:
                for col in (VideoAsset.raw_query, VideoAsset.description_en,
                            VideoAsset.description_zh):
                    conds.append(like(col, w))
                match_terms.append(w)
    if not conds:
        return []
    q = q.filter(or_(*conds))
    rows = q.all()
    out = []
    for a in rows:
        if min_duration_sec and (a.duration_sec or 0) < min_duration_sec - 0.5:
            continue
        title_blob = (a.raw_query or "").lower()
        own_blob = " ".join(filter(None, [a.description_en, a.description_zh,
                                          " ".join(a.tags or [])])).lower()
        # 内容级 >> 标题级 (2026-08-30): 记者会视频含大量非本人切镜头, 只靠
        # 视频标题命中会把特朗普切镜头配给高市早苗 — 自身描述命中 +8/词,
        # 标题命中 +2/词, 逼内容对得上的切片排前。
        score = 100 + sum(8 for t in match_terms if t in own_blob) \
                     + sum(2 for t in match_terms if t in title_blob)
        try:
            if str(_P(a.file_path).parent).lower() in used_dirs:
                score -= 40  # 同源视频已耗 — 换别家的切片
        except Exception:
            pass
        out.append({"file_path": a.file_path, "score": score, "asset_no": a.asset_no,
                    "duration_sec": a.duration_sec, "entity_bullseye": True,
                    "used_count": a.used_count or 0})
    # 新鲜度优先 (2026-08-28): 未用过的排前, 同新鲜度按分, 再按 used_count —
    # 防同 job 内同素材反复 (用户复验实测 0003×3/0012×7 的另一半根因)。
    out.sort(key=lambda r: (r["used_count"] > 0, -r["score"], r["used_count"]))
    return out[:limit]


def match_local_assets(
    db: Session,
    keywords: list[str] | None = None,
    scenes: list[str] | None = None,
    shot_types: list[str] | None = None,
    tone: str | None = None,
    motion_level: str | None = None,
    content_density: str | None = None,
    time_of_day: str | None = None,
    orientation: str | None = None,
    location: str | None = None,
    people: str | None = None,
    min_duration_sec: float | None = None,
    limit: int = 5,
    relax_location: bool = False,
    exclude: list[str] | None = None,
) -> list[dict[str, Any]]:
    """根据语义条件搜索本地素材库，返回匹配结果含 file_path 和得分。

    匹配策略 (ID-034 + 2026-08-12 门槛重构):
    - 硬维度 orientation/people → 独立列硬过滤, 必须全中, 否则排除
    - location → C 折中 (relax_location=False): 先严格硬过滤; 该方式候选
      为空且 relax_location=True 时, 自动放宽 location 过滤并标记
      ``location_relaxed=True`` (命中该素材即已接受地域降级)。
    - 门槛维 scenes/shot_types/tone → 命中率 ≥75% (3 中 ≥2) 才算符合
    - 加分维 motion_level/content_density/time_of_day → 命中加分, 不排除
    - keywords → description_zh/tags 精确打分 (+1 每命中)
    - scenes/shot_types 精确匹配 → +2 每命中
    - exclude → 硬排除指定的 file_path 列表 (同 job 已用素材, 2026-08-12)

    按 score 降序 + ai_confidence 降序 + used_count 升序排列。
    """
    def _search(with_location: bool) -> list[dict[str, Any]]:
        """单次匹配查询 (with_location=False 时不做 location 硬过滤).

        返回与 match_local_assets 相同结构的列表 (location_relaxed 由调用方补)。
        """
        query = db.query(VideoAsset)
        if exclude:
            # 同 job 素材硬排除 (2026-08-12): 一素材一视频只用一次
            query = query.filter(~VideoAsset.file_path.in_(exclude))
        # 禁用素材过滤 (2026-08-12): 用户 dislike 的素材, 本地碰撞线也不选。
        # (Pexels 在线侧原本就在 exclude_pexels_ids 里排除; 本地线此前无此过滤,
        #  打了禁用标仍会被 match_local_assets 选中 → 补上)
        query = query.filter(VideoAsset.preference != "dislike")
        # 成片复用硬上限 (2026-08-15): used_count 只是同分排序时高分素材永远赢,
        # 同一批"万能素材"每个视频都被选中 → 硬过滤: 用满 N 个成片的素材出局。
        try:
            from app.config import get_config
            _max_uses = get_config().defaults.local_asset_max_uses
        except Exception:
            _max_uses = 2
        if _max_uses and _max_uses > 0:
            query = query.filter(
                VideoAsset.used_count.is_(None) | (VideoAsset.used_count < _max_uses)
            )
        # 冷却调度 (2026-08-28): 最近 N 次选用内被用过的素材出局
        try:
            _cooldown = get_config().defaults.material_reuse_cooldown
        except Exception:
            _cooldown = 10
        query = _cooldown_filter(query, int(_cooldown or 0))
        if orientation:
            query = query.filter(VideoAsset.orientation == orientation)
        # 质量硬底线 (2026-08-16 用户反馈烂素材泛滥): 低清素材直接出局。
        # 横版宽 <1280 / 竖版高 <1280 的不进候选 — 830/1796 个素材低清是
        # "烂"的主因; 宁可候选变少走 Pexels, 不上低清货。
        query = query.filter(or_(
            and_(VideoAsset.orientation == "portrait", VideoAsset.height >= 1280),
            and_(VideoAsset.orientation != "portrait", VideoAsset.width >= 1280),
        ))
        if people:
            query = query.filter(VideoAsset.people == people)
        if location and with_location:
            query = query.filter(VideoAsset.location == location)
        # 内容质量硬底线 (2026-08-16 烂素材治理③): VLM 质量分 ≤3 的出局;
        # 未打分(None)暂放行 — asset_quality_scan 全量跑完后逐步收紧。
        try:
            _min_q = get_config().defaults.local_asset_min_quality
        except Exception:
            _min_q = 4
        if _min_q and _min_q > 0:
            query = query.filter(
                VideoAsset.quality_score.is_(None)
                | (VideoAsset.quality_score >= _min_q)
            )
        # 只查本地已有文件的素材
        query = query.filter(VideoAsset.file_path.isnot(None))
        if min_duration_sec:
            # 精确时长防护 (2026-08-09): render_scale_pad 无 loop, 素材短于
            # slot 时长会黑尾截断, 因此要求素材时长 >= slot 精确时长。
            query = query.filter(VideoAsset.duration_sec >= min_duration_sec)

        # ── 软维度候选: 需满足命中率 ≥75% ──
        # scenes/shot_types 值不参与 SQL 过滤 —— SQLite JSON 数组列把中文存成
        # \\uXXXX 字面量 (如 ["城市"] → ["\\u57ce\\u5e02"]), cast(String) + ilike
        # 永远匹配不到明文 (2026-08-09 根因, 曾致本地碰撞 0 命中)。这两维交给
        # 下方 Python 层精确匹配 (asset_scenes/asset_shots), SQL 只按 keywords
        # (明文 desc_zh/en, tags 英文不转义) 与四维英文值 (extra JSON 不转义) 收窄。
        four_val = [(dim, val) for dim, val in (
            ("tone", tone), ("motion_level", motion_level),
            ("content_density", content_density), ("time_of_day", time_of_day),
        ) if val]
        kw_terms = [k for k in (keywords or []) if k]
        has_soft = bool([s for s in (scenes or []) if s]) or bool(
            [s for s in (shot_types or []) if s]) or bool(four_val) or bool(kw_terms)
        if has_soft:
            term_filters = []
            # keywords: 明文列 (tags/desc_zh/desc_en) 模糊匹配
            for k in kw_terms:
                t = k.lower()
                term_filters.append(or_(
                    VideoAsset.tags.cast(String).ilike(f"%{t}%"),
                    VideoAsset.description_zh.ilike(f"%{t}%"),
                    VideoAsset.description_en.ilike(f"%{t}%"),
                ))
            # 四维英文值: extra JSON 键值对 (值不转义, 带引号匹配)
            for _dim, val in four_val:
                term_filters.append(
                    VideoAsset.ai_tags_extra.cast(String).ilike(f'%"{val}"%')
                )
            if term_filters:
                query = query.filter(or_(*term_filters))

        candidates: list[VideoAsset] = query.order_by(
            VideoAsset.ai_tagged_at.desc().nullslast(),
            VideoAsset.used_count.asc(),
        ).limit(min(max(limit * 3, 60), 200)).all()  # 多取一些做精确打分

        # ── 精确打分 + 命中率校验 ──
        scored = []
        for asset in candidates:
            # 门槛维 (scenes/shots/tone) 命中率: 需 ≥75% (3 中 ≥2)。
            # 加分维 (motion/content/time) 不参与门槛, 只加分不排除 (2026-08-12)。
            gate_requested = 0
            gate_hit = 0
            bonus_hit = 0
            asset_scenes = [s.lower() for s in (asset.scenes or [])]
            asset_shots = [s.lower() for s in (asset.shot_types or [])]
            extra = asset.ai_tags_extra or {}

            # 先收集所有软维度请求 (门槛维 + 加分维)
            soft_requests: list[tuple[str, list[str], str]] = [
                ("scenes", scenes or [], "gate"),
                ("shot_types", shot_types or [], "gate"),
                ("tone", [tone] if tone else [], "gate"),
                ("motion_level", [motion_level] if motion_level else [], "bonus"),
                ("content_density", [content_density] if content_density else [], "bonus"),
                ("time_of_day", [time_of_day] if time_of_day else [], "bonus"),
            ]
            for dim, vals, kind in soft_requests:
                if not vals:
                    continue
                # 导演给的多个值是备选 (如 shot_types=["建筑","特写"] 任一即可)。
                # 任一请求值命中素材该维 → 本维命中 (2026-08-12 修复)。
                if dim in ("scenes", "shot_types"):
                    asset_vals = asset_scenes if dim == "scenes" else asset_shots
                    if not asset_vals:
                        # 素材未标该维 → 无信息, 不算失分也不参与门槛 (防误杀)
                        continue
                    hit = any(v.lower() in asset_vals for v in vals)
                    if kind == "gate":
                        gate_requested += 1
                        if hit:
                            gate_hit += 1
                    else:
                        if hit:
                            bonus_hit += 1
                    continue
                # 单值维度 tone/motion/content/time
                v = vals[0].lower()
                # tone/motion/content/time: 兼容 ai_tags_extra 值为 list
                # (如 time_of_day=["day","night"]) 的脏数据
                ev = extra.get(dim)
                if isinstance(ev, list):
                    ev = ev[0] if ev else ""
                has_val = bool((ev or "").strip())
                if not has_val:
                    # 素材未标该维 → 无信息, 既不命中也不算失分 (2026-08-12:
                    # 空值不参与门槛, 防误杀 —— tech 素材 41/42 tone 空, 若算
                    # 失分则 3 维门槛恒 67%<75%, 全被刷掉)
                    continue
                hit = (ev or "").lower() == v
                if hit:
                    if kind == "gate":
                        gate_hit += 1
                    else:
                        bonus_hit += 1
                if kind == "gate":
                    gate_requested += 1

            if gate_requested:
                ratio = gate_hit / gate_requested
                if ratio < MIN_HIT_RATIO:
                    continue  # 门槛维命中率不达标 → 排除 (宁可错过, 不摆烂)
                gate_ratio = ratio
            else:
                # 素材门槛维全空 → 无法证明符合画面, 排除 (宁可错过, 不摆烂)
                continue

            score = 0
            # 官片源加权 (2026-08-28 用户令"yt切片优先级太低"): youtube 官方素材
            # 同场竞争时 +3 — 门槛分过了就是正确画面, 官方画质压图库
            if (asset.source or "") == "youtube":
                score += 3
            # keywords 精确打分 (词表包画面词, 不与命中率绑定)
            if keywords:
                zh = (asset.description_zh or "").lower()
                en = (asset.description_en or "").lower()
                tag_str = " ".join(asset.tags or []).lower()
                for kw in keywords:
                    kw_lower = kw.lower()
                    if kw_lower in zh or kw_lower in en:
                        score += 1
                    if kw_lower in tag_str:
                        score += 1
            # scenes/shot_types 精确匹配加分
            if scenes:
                for s in scenes:
                    if s.lower() in asset_scenes:
                        score += 2
            if shot_types:
                for st in shot_types:
                    if st.lower() in asset_shots:
                        score += 2
            # 加分维命中加分 (motion/content/time, 不参与门槛)
            score += bonus_hit * 1
            # 已 AI 打标加分 (标签更可信)
            if asset.ai_tagged_at is not None:
                score += 1

            # 检查文件实际存在
            fp = Path(asset.file_path)
            if not fp.exists():
                continue

            scored.append((score, gate_ratio if gate_ratio is not None else 0.0, asset))

        scored.sort(key=lambda x: x[0], reverse=True)

        out = []
        for score, _ratio, asset in scored[:limit]:
            out.append({
                # 2026-09-02 修: 原版 fp 为打分循环残留变量, 所有条目的 file 都是
                # 最后一个候选的文件名 (file_path 正确, file 全错 — 潜伏雷)
                "file": Path(asset.file_path).name,
                "file_path": asset.file_path,
                "score": score,
                # 门槛维命中率; 未传门槛维时返回 None (无命中率约束)
                "hit_ratio": _ratio if _ratio else None,
                "description_zh": asset.description_zh,
                "scenes": asset.scenes,
                "shot_types": asset.shot_types,
                "tags": asset.tags,
                "tone": (asset.ai_tags_extra or {}).get("tone"),
                "motion_level": (asset.ai_tags_extra or {}).get("motion_level"),
                "content_density": (asset.ai_tags_extra or {}).get("content_density"),
                "time_of_day": (asset.ai_tags_extra or {}).get("time_of_day"),
                "orientation": asset.orientation,
                "location": asset.location,
                "people": asset.people,
                "duration_sec": asset.duration_sec,
            })
        return out

    # ── 地域 C 折中 (2026-08-12): 先 strict (location 硬过滤), 候选为空才放宽 ──
    # 判定基于完整匹配结果 (含 keywords/软维度), 保证"有 domestic 绝不用 foreign"。
    if location and relax_location:
        strict = _search(with_location=True)
        if strict:
            return strict  # 本地有 domestic 命中 → 直接用, 不降级
        # strict 为空 → 放宽 location, 命中即接受地域降级 (不静默, 标记)
        relaxed = _search(with_location=False)
        for item in relaxed:
            item["location_relaxed"] = True
        return relaxed

    results = _search(with_location=bool(location))
    for item in results:
        item["location_relaxed"] = False
    return results


def register_asset_usage(db: Session, file_path: str) -> None:
    """登记一次本地素材使用 (素材不复用).

    递增 ``used_count`` (updated_at 由 onupdate 自动刷新)。只增不减;
    不做同 job 硬排除 —— 本地库规模 (1500+) 远不足以支撑 107 slot 全去重,
    由 match_local_assets 的 ``used_count.asc()`` 排序自然把高频素材降优先级。

    冷却调度 (2026-08-28 用户令"每10次才能复用一次"): 全局选用序号存
    data/material_stats.json (免建表), 登记 = 序号+1 + 写 asset.last_used_seq;
    检索侧 _cooldown_filter 排除 (当前序号 - last_used_seq) < cooldown 的素材。
    """
    import json as _json

    asset = (
        db.query(VideoAsset)
        .filter(VideoAsset.file_path == file_path)
        .first()
    )
    if asset is None:
        return
    stats_path = Path(__file__).resolve().parents[2] / "data" / "material_stats.json"
    try:
        stats = _json.loads(stats_path.read_text(encoding="utf-8"))
    except Exception:
        stats = {"selection_seq": 0}
    stats["selection_seq"] = int(stats.get("selection_seq", 0)) + 1
    stats_path.write_text(_json.dumps(stats), encoding="utf-8")

    asset.used_count = (asset.used_count or 0) + 1
    asset.last_used_seq = stats["selection_seq"]
    db.commit()


def _current_selection_seq() -> int:
    """全局选用序号 (冷却判定的"现在")."""
    import json as _json

    try:
        stats = _json.loads(
            (Path(__file__).resolve().parents[2] / "data" / "material_stats.json")
            .read_text(encoding="utf-8"))
        return int(stats.get("selection_seq", 0))
    except Exception:
        return 0


def _cooldown_filter(query, cooldown: int):
    """冷却排除: 最近 cooldown 次选用内被用过的素材出局 (每 N 次才能复用一次)."""
    if cooldown <= 0:
        return query
    now_seq = _current_selection_seq()
    return query.filter(
        VideoAsset.last_used_seq.is_(None)
        | (VideoAsset.last_used_seq <= now_seq - cooldown)
    )


def pick_local_fallback() -> Path | None:
    """从 materials_dir 随机选一个可用素材文件（兜底），不依赖 DB."""
    import random

    cfg = get_config().defaults
    materials_dir = Path(cfg.materials_dir)
    if not materials_dir.exists():
        return None

    videos = sorted(
        p for p in materials_dir.rglob("*")
        if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm") and p.is_file()
    )
    if not videos:
        return None
    return random.choice(videos)
