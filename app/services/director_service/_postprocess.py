"""Director Agent 2.0 — 规划后处理 (clamp / references 卡 / 落库).

slot 时长钳制、references 来源卡追加、plan 落库 + slots 持久化。
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import DirectorJob, DirectorSlot
from app.services.director_service._trace import append_trace

logger = logging.getLogger(__name__)

__all__ = ["_clamp_slot_durations", "_append_source_slot", "_persist_plan"]

# 片尾来源声明卡时长 (2026-08-17 用户口径: 5 秒无语音免责声明)
_SOURCE_CARD_DURATION = 5.0


# 质量钳制参数 (2026-08-15): 实测 53 slots/5min、broll 0.9s 闪切、21 张 hf_title → 硬兜底
_MIN_DUR = {
    "broll_pexels": 2.5,
    "broll_local": 2.5,
    "hf_title": 3.0,
    "hf_chart": 3.0,
    "hf_quote": 3.0,
    "evidence_image": 3.0,  # 证据图要给观众读数字的时间 (2026-09-04 管线③)
    "hf_identity": 4.0,     # 身份卡 (2026-09-05): 自介句要读完+印章落定
    "hf_follow": 3.0,       # 收尾互动卡 (2026-09-05)
}
_HF_TITLE_CAP = 5  # 含尾部参考卡(clamp 后追加, 不占此额度)
_PROTECTED = {"host", "mixed_host_broll", "hf_opening"}

# 证据图数量上限 (2026-09-04 管线④): 用户口径"有测试/比较的都要图",
# 上限仅防 LLM 病态滥用; 超限者降级 broll_pexels。
_EVIDENCE_CAP = 10

# ── broll_pexels 缺词兜底 (2026-09-04) ──
# LLM 规划偶发漏 keywords (job 33b2b922: 8/23 槽缺词, 兜底拿整段口播当 query
# 必然 no usable material)。此处按"主体继承 + 概念映射"确定性补词:
#   - 主体 = 同 plan 其他 pexels 槽 keywords[0] 的多数 (保持全片主题一致)
#   - 场景词 = 口播/shot_contract 命中下表概念 → 具象英文词
# 词典蒸馏自 visual_director_v2.txt 具象化铁律/视觉符号映射表 — 只用表内
# 验证过的词, 禁自造隐喻 (提示词铁律同款约束)。
_PEXELS_CONCEPT_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("数学", "考卷", "卷子", "满分", "分数", "成绩", "考试", "测试", "榜单", "排名"), "math exam paper"),
    (("编程", "代码", "终端", "程序"), "programming terminal"),
    (("电路", "铜线", "电路板", "PCB", "元器件", "走线", "打样"), "circuit board macro"),
    (("价格", "标价", "美元", "成本", "单价", "工资", "账"), "stock trading floor"),
    (("老板", "公司", "工位", "员工", "办公室", "上班"), "office workers desk"),
    (("漏洞", "黑客", "攻击", "网络安", "破解"), "hacker dark room"),
    (("服务器", "数据中心", "算力", "GPU", "训练"), "server racks corridor"),
    (("电脑", "软件", "键盘", "屏幕", "操作"), "person using computer"),
    (("发布会", "官方", "声明", "发布"), "tech product launch stage"),
    (("普通人", "饭碗", "生计", "失业", "流水线"), "crowded bus stop"),
    (("点赞", "关注", "评论区", "观众"), "smartphone notification screen"),
    (("报道", "媒体", "新闻", "记者"), "newspaper printing press"),
)
_PEXELS_FALLBACK_SUBJECT = "city skyline timelapse"  # 全 plan 无词时的中性主体
_PEXELS_GENERIC_DETAIL = "person using computer"      # 概念全未命中时的保底场景词

# HF 线最小时间间隔 (2026-09-02 用户令): 连续/近距离 HF 文字卡观感疲劳且
# 挤占实拍画面 — 两张 HF 卡(除片头 hf_opening)间隔不足此值的, 后者降级
# broll_pexels 走实拍/下载, 画面不断档。
_HF_MIN_GAP_SEC = 20.0

# HF 卡时长上限 (2026-09-02 用户令): HF 模板动画设计轴 ~3.6s (S 缩放封顶
# 1.6x ≈ 5-8s), 超时长卡必然动画循环重播/长静置 → 超限部分拆 broll_pexels
# 补画面 (口播时间轴不动, 只换画面源)。
# 开篇 (首个 slot) 更紧: 纯文字卡是跳出率杀手, 6s 内必须让位实拍/出镜。
_HF_MAX_DUR = 8.0
_HF_OPENING_MAX_DUR = 6.0


def _clamp_slot_durations(plan: Any, total_duration: float) -> None:
    """质量钳制: 重叠去重 + 最小时长 + hf_title 数量上限 + 时序重排.

    LLM 规划实测三种劣化 (2026-08-15): broll 碎片闪切 (0.9~1.8s)、
    hf_title 滥用 (单片 21 张)、同秒重叠 slot。此处为不可协商的硬底线。
    """
    slots = sorted(plan.slots, key=lambda s: (s.start_sec, s.slot_index))
    kept: list[Any] = []
    hf_title_seen = 0
    last_hf_end = -9999.0  # 上一张 HF 卡结束时刻 (2026-09-02 间隔约束)
    hf_gap_demoted = 0
    cursor = 0.0
    dropped = 0
    for slot in slots:
        dur = slot.end_sec - slot.start_sec
        if dur <= 0.05:
            dropped += 1
            continue  # 空 slot
        # hf_title 数量上限: 超限丢弃 (尾部参考卡在 clamp 之后追加, 不受影响)
        if slot.workflow == "hf_title":
            hf_title_seen += 1
            if hf_title_seen > _HF_TITLE_CAP:
                logger.info("[director] drop excess hf_title (cap %d)", _HF_TITLE_CAP)
                dropped += 1
                continue
        # HF 最小间隔 (2026-09-02): 距上一张 HF 卡 < 20s 的非片头 HF 卡
        # 降级 broll_pexels (真实画面优先; params 的 9 维/keywords 对 broll 兼容)
        # 豁免 hf_follow (2026-09-05): 固定收尾卡全篇 ≤1 且位置在尾, 与收尾
        # 金句 quote 的间隔天然 <20s (中间只隔"下期见"句), 不豁免必被误杀。
        if (slot.workflow.startswith("hf")
                and slot.workflow not in ("hf_opening", "hf_follow")):
            if slot.start_sec - last_hf_end < _HF_MIN_GAP_SEC:
                logger.info("[director] hf gap demote %s@%.1fs (距上张 HF %.1fs < %.0fs)",
                            slot.workflow, slot.start_sec,
                            slot.start_sec - last_hf_end, _HF_MIN_GAP_SEC)
                slot.workflow = "broll_pexels"
                hf_gap_demoted += 1
        min_dur = _MIN_DUR.get(slot.workflow, 0.0)
        if dur < min_dur:
            if slot.workflow in _PROTECTED or slot.workflow.startswith("hf"):
                dur = min_dur  # hf 卡/出镜拉长到下限
            elif kept and kept[-1].workflow == slot.workflow:
                # broll 碎片并入前一相邻同类型 slot (顺延其 end)
                kept[-1].end_sec = round(kept[-1].end_sec + dur, 3)
                kept[-1].duration_sec = round(kept[-1].end_sec - kept[-1].start_sec, 3)
                cursor = kept[-1].end_sec
                continue
            else:
                dur = min_dur
        # 重叠去重: 起点 = max(原起点, 上一 slot 结束)
        start = max(slot.start_sec, cursor)
        end = start + dur
        if total_duration:
            end = min(end, total_duration)
        slot.start_sec = round(start, 3)
        slot.end_sec = round(end, 3)
        slot.duration_sec = round(slot.end_sec - slot.start_sec, 3)
        if slot.duration_sec <= 0.05 and slot.workflow not in _PROTECTED:
            dropped += 1
            continue
        cursor = slot.end_sec
        if slot.workflow.startswith("hf"):
            last_hf_end = slot.end_sec  # 钳后时刻为准, 供后续间隔判定
        kept.append(slot)
    for i, slot in enumerate(kept):
        slot.slot_index = i
    # 时间轴满铺 (2026-08-15): LLM 规划偶发漏铺中段 (实测 323s 音频在 223s 处
    # 有 8.7s 空洞 → 成片音轨错位+截断, 观众听感"整段消失/念一半没了")。
    # 任何 >0.5s 的 slot 间隙, 一律用前一个 slot 延伸填满; 片尾同理。
    filled = 0
    for cur, nxt in zip(kept, kept[1:]):
        gap = nxt.start_sec - cur.end_sec
        if gap > 0.5:
            cur.end_sec = round(nxt.start_sec, 3)
            cur.duration_sec = round(cur.end_sec - cur.start_sec, 3)
            filled += 1
    if kept and total_duration and kept[-1].end_sec < total_duration - 0.5:
        gap = total_duration - kept[-1].end_sec
        kept[-1].end_sec = round(total_duration, 3)
        kept[-1].duration_sec = round(kept[-1].end_sec - kept[-1].start_sec, 3)
        filled += 1
        logger.info("[director] tail coverage: extended last slot by %.1fs to audio end (%.1fs)",
                    gap, total_duration)
    if filled:
        logger.info("[director] timeline tiling: filled %d gap(s), slots now tile audio fully", filled)

    # ── HF 卡时长上限 (2026-09-02): 超限拆 broll 补画面 ──
    # 动画 ~5-8s, 超长卡循环重播; 开篇首 slot 更紧 (跳出率)。截短后剩余时段
    # 用 broll_pexels slot 补 (继承 params 的 keywords/9 维, 时间轴不动)。
    import copy as _copy
    hf_capped = 0
    split_out: list[Any] = []
    first_slot = kept[0] if kept else None
    for slot in kept:
        dur = slot.end_sec - slot.start_sec
        is_first = (slot is first_slot)
        cap = _HF_OPENING_MAX_DUR if is_first else _HF_MAX_DUR
        if slot.workflow.startswith("hf") and dur > cap + 0.05:
            old_end = slot.end_sec
            slot.end_sec = round(slot.start_sec + cap, 3)
            slot.duration_sec = cap
            if old_end - slot.end_sec > 1.0:  # 余段够 1s 才补 broll
                tail = _copy.deepcopy(slot)
                tail.workflow = "broll_pexels"
                tail.start_sec = slot.end_sec
                tail.end_sec = round(old_end, 3)
                tail.duration_sec = round(tail.end_sec - tail.start_sec, 3)
                # HF 的 params (keywords/scenes/9 维) 对 broll_pexels 兼容
                split_out.append(tail)
            hf_capped += 1
            logger.info("[director] hf cap: %s@%.1fs %.1fs→%.1fs%s",
                        slot.workflow, slot.start_sec, dur, cap,
                        " +broll 补段" if old_end - slot.end_sec > 1.0 else "")
    if split_out:
        kept = sorted(kept + split_out, key=lambda s: (s.start_sec, s.slot_index))
        for i, slot in enumerate(kept):
            slot.slot_index = i  # 拆分插入后全局重排
    if hf_capped:
        logger.info("[director] hf max-dur: %d 张超长 HF 卡截断 (上限 开篇%.0fs/其余%.0fs)",
                    hf_capped, _HF_OPENING_MAX_DUR, _HF_MAX_DUR)

    plan.slots = kept
    if hf_gap_demoted:
        logger.info("[director] hf min-gap: %d 张过近 HF 卡降级 broll (间隔 <%ss)",
                    hf_gap_demoted, _HF_MIN_GAP_SEC)
    if dropped:
        logger.info("[director] quality clamp: kept %d slots, dropped %d (碎片/超限/重叠)",
                    len(kept), dropped)


def _domain_of(url: str) -> str:
    """URL → 可读媒体名 (netloc 去 www./m./wap. 前缀). 失败返回空串."""
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    for prefix in ("www.", "m.", "wap."):
        if host.startswith(prefix):
            return host[len(prefix):]
    return host


def _clean_source_title(title: str) -> str:
    """清洗来源标题: 截掉智谱搜索的"（发布时间：…"尾巴, 去空白, 限 40 字."""
    t = (title or "").strip()
    for marker in ("（发布时间", "(发布时间", "发布时间"):
        i = t.find(marker)
        if i > 0:
            t = t[:i]
            break
    return t.strip()[:40]


def _collect_news_sources(db: Session, script: Any) -> list[dict[str, str]]:
    """聚合真实来源: 素材包(真 http URL → 域名作媒体名 + 清洗后标题) + 主稿 article.

    来源数据在 material_items (洗稿时喂 LLM 参考), 产出稿不保留来源列表 →
    尾卡从这里取 (2026-08-17 修复: 原机制依赖脚本 references 段, 全库从未出现).
    URL 去重, 上限 8 条 (hf-source-v2 容量).
    """
    from app.models import MaterialPackage

    srcs: list[dict[str, str]] = []
    seen: set[str] = set()

    if script.material_package_id:
        pkg = db.get(MaterialPackage, script.material_package_id)
        if pkg:
            for it in pkg.items:
                url = (it.source_url or "").strip()
                if not url.lower().startswith("http") or url in seen:
                    continue
                seen.add(url)
                media = _domain_of(url)  # media 字段历史上有日期脏值, 域名更可靠
                srcs.append({"media": media or "网络", "title": _clean_source_title(it.title)})
                if len(srcs) >= 8:  # hf-source-v2 容量 8 (2026-09-04, 原 5)
                    break

    if len(srcs) < 8 and script.article and script.article.source_url:
        url = (script.article.source_url or "").strip()
        if url.lower().startswith("http") and url not in seen:
            seen.add(url)
            srcs.append({"media": _domain_of(url) or "新闻源",
                         "title": _clean_source_title(script.article.title)})
    return srcs


def _append_source_slot(db: Session, plan: Any, script: Any, total_duration: float) -> None:
    """片尾来源声明卡 (来源驱动, 无语音) — 修复 2026-08-17.

    原 _append_references_slot 依赖洗稿文本含"参考来源"段 (script_parser 据此建
    references 段), 但 7 层洗稿 prompt 输出纯口播稿从不含该段 → 全库 references
    段=0, 尾卡从未触发. 改为从 material_package + article 聚合真实来源, 有来源
    即恒定追加 5s hf_title 卡 (媒体名·短标题, no_voiceover).
    """
    from app.schemas import DirectorSlotPlan

    sources = _collect_news_sources(db, script)
    if not sources:
        # 回退: 洗稿文本的 references 段 (历史数据兜底)
        ref_segments = [seg for seg in script.segments if seg.segment_type == "references"]
        ref_text = "\n".join(seg.text for seg in ref_segments[:6])[:200].strip()
        if ref_text:
            sources = [{"media": "参考来源", "title": ref_text}]
    if not sources:
        sources = [{"media": "公开报道", "title": "内容综合自公开报道，仅供参考"}]

    subtitle = "  ·  ".join(
        f"{s['media']}·{s['title']}" if s["title"] else s["media"] for s in sources
    )[:200]

    ref_slot = DirectorSlotPlan(
        slot_index=len(plan.slots),
        start_sec=round(total_duration, 3),
        end_sec=round(total_duration + _SOURCE_CARD_DURATION, 3),
        duration_sec=_SOURCE_CARD_DURATION,
        text_context=subtitle,
        segment_id=None,
        visual_type="hf_title",
        workflow="hf_title",
        params={
            "render_config": {
                "title": "内容来源声明",
                "subtitle": subtitle,
                "style": "references",
                # 结构化来源列表 (hf-source-v1 专用; 长 subtitle 走 hf-title-v2
                # 的 kicker 会炸 32 字校验 → 2026-08-18 产线降级黑屏事故)
                "sources": sources,
            },
            "intensity": "low",
            "emotion": "closing",
            "no_voiceover": True,
        },
    )
    plan.slots.append(ref_slot)
    logger.info("[director] appended source-declaration hf_title slot (%.0fs): %s",
                _SOURCE_CARD_DURATION, subtitle[:60])


def _enforce_track_gates(db: Session, job: DirectorJob, plan: Any) -> None:
    """赛道闸门 (2026-09-05): hf_identity/hf_follow 财经线(tech)专用。

    提示词写了"地缘线禁选"但 geo 稿照规划 (job 2d4ebcf8 实锤) — LLM 提示词
    是软约束; 执行层 _require_tech_track 虽会拒绝, 但 slot 先红一次 (用户
    手点重试还会再红一次)。这里规划期确定性降级 broll_pexels, evidence 闸门
    同款 (params.fallback_reason 记因)。track 口径与 hf.py _slot_track 一致
    (script.article.track, 缺省 tech)。
    """
    gated = [s for s in plan.slots if s.workflow in ("hf_identity", "hf_follow")]
    if not gated:
        return
    script = getattr(job, "script", None)
    track = ((script.article.track if script and script.article else None) or "tech").strip().lower()
    if track == "tech":
        return
    demoted: list[str] = []
    for s in gated:
        wf = s.workflow
        s.workflow = "broll_pexels"
        s.visual_type = "broll_pexels"
        s.params = {**(s.params or {}),
                    "fallback_reason": f"track_gate: {wf} 仅财经线可用 (当前 track={track}), 降级"}
        demoted.append(f"#{s.slot_index}({wf})")
    logger.info("[director] track gate: track=%s, demoted %d -> %s",
                track, len(demoted), "; ".join(demoted))
    append_trace(db, job, "track_gate", "done",
                 f"赛道闸门: track={track}, 降级 {len(demoted)} 个 tech 专用卡\n"
                 + "\n".join(demoted[:8]))


def _enforce_evidence_gates(db: Session, job: DirectorJob, plan: Any) -> None:
    """证据图三道闸门 (2026-09-04 管线④, 用户硬条件的代码防线).

    LLM 提示词是第一层防线, 这里是第二层 (P线 ID-050 双层防线先例):
      1. 可用性: script 无素材包或池空 → 全部降级 (防必败 slot)
      2. 语义:   text_context+claim 不构成测试/比较论断 → 降级
                 (保证"只有测试/比较段才上证据图")
      3. 数量:   超过 _EVIDENCE_CAP → 多余降级 (防病态滥用)
    降级 = workflow/visual_type 改 broll_pexels + params.fallback_reason 记因。
    """
    ev_slots = [s for s in plan.slots if s.workflow == "evidence_image"]
    if not ev_slots:
        return
    pool: list = []
    try:
        from ..evidence_service import collect_evidence_pool
        script = getattr(job, "script", None)
        pool = collect_evidence_pool(db, script) if script is not None else []
    except Exception:  # noqa: BLE001 — 池查询失败按空池处理 (全降级, 不挡规划)
        logger.warning("[director] evidence pool collect failed", exc_info=True)
        pool = []
    from ..evidence_service import is_evidence_claim

    kept = 0
    demoted: list[str] = []
    for s in ev_slots:
        claim = (s.params or {}).get("claim") or ""
        reason = ""
        if not pool:
            reason = "证据图池为空(素材包无合格图)"
        elif not is_evidence_claim(f"{s.text_context or ''} {claim}".strip()):
            reason = "段落非测试/比较论断(无数字或无比较语义)"
        elif kept >= _EVIDENCE_CAP:
            reason = f"超数量上限({_EVIDENCE_CAP})"
        if reason:
            s.workflow = "broll_pexels"
            s.visual_type = "broll_pexels"
            s.params = {**(s.params or {}), "fallback_reason": f"evidence_gate: {reason}"}
            demoted.append(f"#{s.slot_index}({reason})")
        else:
            kept += 1
    if demoted:
        logger.info("[director] evidence gates: kept %d, demoted %d -> %s",
                    kept, len(demoted), "; ".join(demoted[:5]))
        append_trace(db, job, "evidence_gate", "done",
                     f"证据图闸门: 保留 {kept} / 降级 {len(demoted)}\n" + "\n".join(demoted[:8]))


def _backfill_pexels_keywords(db: Session, job: DirectorJob, plan: Any) -> None:
    """broll_pexels 缺 keywords 兜底 (2026-09-04, 双层防线第二层).

    提示词是第一层; LLM 偶发漏词时无词槽会拿整段口播原文当搜索 query,
    必然 no usable material (job 33b2b922 实证)。此处确定性补词:
    主体继承同 plan 多数 keywords[0] + 概念词典映射场景词, 只用
    visual_director_v2.txt 表内已验证的词。补词记 params.fallback_keywords
    供观测; 命中主体继承时降维搜索主词永不被丢 (主体优先铁律同款语义)。
    """
    from collections import Counter

    pexels_slots = [s for s in plan.slots if s.workflow == "broll_pexels"]
    if not pexels_slots:
        return

    subj_counts: Counter[str] = Counter()
    for s in pexels_slots:
        kws = (s.params or {}).get("keywords")
        if isinstance(kws, list) and kws and kws[0]:
            subj_counts[str(kws[0])] += 1
    subject = subj_counts.most_common(1)[0][0] if subj_counts else _PEXELS_FALLBACK_SUBJECT

    filled: list[str] = []
    for s in pexels_slots:
        params = dict(s.params or {})
        kws = params.get("keywords")
        if isinstance(kws, list) and kws:
            continue
        sc = params.get("shot_contract")
        goal = sc.get("visual_goal", "") if isinstance(sc, dict) else ""
        text = f"{s.text_context or ''} {goal}"
        details: list[str] = []
        for keys, phrase in _PEXELS_CONCEPT_MAP:
            if any(k in text for k in keys) and phrase != subject and phrase not in details:
                details.append(phrase)
            if len(details) >= 3:
                break
        if not details:
            details = [_PEXELS_GENERIC_DETAIL]
        params["keywords"] = [subject, *details]
        params["fallback_keywords"] = True
        s.params = params
        filled.append(f"#{s.slot_index}→{'/'.join(params['keywords'][:2])}")

    if filled:
        logger.info("[director] pexels keywords backfill: %d slot(s), subject=%s",
                    len(filled), subject)
        append_trace(db, job, "pexels_keywords_backfill", "done",
                     f"缺词兜底 {len(filled)} 槽, 主体={subject}\n" + "\n".join(filled[:10]))


def _persist_plan(
    db: Session,
    job: DirectorJob,
    plan: Any,
    script_title: str | None,
) -> None:
    """plan 落库 (保留既有 trace) + slots 持久化."""
    # 赛道闸门 (2026-09-05): tech 专用卡 (hf_identity/hf_follow) geo 稿规划期降级
    _enforce_track_gates(db, job, plan)
    # 证据图闸门 (2026-09-04 管线④): 落库前降级不合格 evidence slot
    _enforce_evidence_gates(db, job, plan)
    # pexels 缺词兜底 (2026-09-04): 在闸门后跑 — evidence 降级来的 broll 槽一并覆盖
    _backfill_pexels_keywords(db, job, plan)
    # 保留既有 trace (alignment/plan_llm 已写入), 再覆盖 plan 主体, 避免 trace 被 model_dump 清空
    prev_trace = list((job.plan_json or {}).get("trace", []) or [])
    job.plan_json = plan.model_dump()
    if prev_trace:
        job.plan_json["trace"] = prev_trace
    job.title = plan.title or script_title
    job.status = "reviewing"
    append_trace(db, job, "plan", "done", f"规划完成, {len(plan.slots)} slots 进入 reviewing")
    db.commit()

    # Persist slots
    for slot_plan in plan.slots:
        slot = DirectorSlot(
            director_job_id=job.id,
            slot_index=slot_plan.slot_index,
            start_sec=slot_plan.start_sec,
            end_sec=slot_plan.end_sec,
            duration_sec=slot_plan.duration_sec,
            text_context=slot_plan.text_context,
            segment_id=slot_plan.segment_id,
            visual_type=slot_plan.visual_type,
            workflow=slot_plan.workflow,
            params_json=slot_plan.params,
            camera_angle=slot_plan.camera_angle,
            view_group_index=job.view_group_index or 0,
            status="queued",
        )
        db.add(slot)
    db.commit()
    db.refresh(job)

    # ── 镜头契约层 (2026-08-27, J3 前置): 独立 flash 二段跑, 不碰规划提示词 ──
    # 契约+J2 effect_recipe 存 params_json; 增强层失败静默, 不阻塞 reviewing。
    try:
        from ..shot_contract import generate_shot_contracts
        stats = generate_shot_contracts(db, job.id)
        append_trace(db, job, "shot_contract", "done",
                     f"镜头契约 {stats.get('contracts', 0)} 条 / 配方 {stats.get('recipes', 0)} 个"
                     + (f" (失败: {stats['failed']})" if stats.get("failed") else ""))
    except Exception as exc:  # noqa: BLE001 — 增强层任何失败不挡主流程
        append_trace(db, job, "shot_contract", "failed", str(exc)[:120])

    # ── 素材实体层 (2026-08-27, 素材层 2.0 第 1 期): 口播稿 → 实体需求单 ──
    # 特朗普演讲/航母这类真实画面 Pexels 图库根本没有 — 实体按类型路由
    # (person/military/event→油管/DVIDS 第2期接线, concept→Pexels 兜底)。
    # 实体入 plan_json + slot params.entities (本地碰撞实体维度, 第3期)。
    try:
        from ..entity_extractor import (ENTITY_LAYER_ENABLED, extract_material_entities,
                                        match_slot_entities, requirement_sheet)
        if not ENTITY_LAYER_ENABLED:
            raise RuntimeError("实体层实验中(ENTITY_LAYER_ENABLED=False), 跳过")
        script = getattr(job, "script", None)
        full_text = ((getattr(script, "boosted_text", None) or "")
                     or (getattr(script, "script_text", None) or "")) if script else ""
        entities = extract_material_entities(full_text)
        if entities:
            pj = dict(job.plan_json or {})
            pj["material_entities"] = entities
            job.plan_json = pj
            for s in job.slots:
                hits = match_slot_entities(s.text_context or "", entities)
                if hits:
                    params = dict(s.params_json or {})
                    params["entities"] = hits
                    s.params_json = params
            db.commit()
        # 人物 slot 提升 (2026-08-30): 高市早苗根因 — 规划不知道库里有她的素材,
        # 人物 slot 被排成 hf 文字卡。人物实体有库藏 → hf 转 broll 让真画面上片。
        from ..entity_extractor import promote_person_slots
        n_promo = promote_person_slots(db, job, entities)
        if n_promo:
            append_trace(db, job, "material_entities", "done",
                         f"人物slot提升 {n_promo} 个 hf→broll (库藏人物)")
        append_trace(db, job, "material_entities", "done",
                     f"素材实体 {len(entities)} 个\n{requirement_sheet(entities)}")
    except Exception as exc:  # noqa: BLE001 — 增强层失败不挡主流程
        append_trace(db, job, "material_entities", "failed", str(exc)[:120])
