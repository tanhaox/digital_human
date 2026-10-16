# -*- coding: utf-8 -*-
"""LLM 规划器 (两步法, 0911 用户架构令) — 导演步 + 转译步.

Step1 导演步 (director_prompt.txt): 身份=动画导演; 口播稿 → 语义分段(≤8s/段)
  → 每段画面描述(visual_desc) + 动画节拍(beats)。管内容与画面感。
Step2 转译步 (translator_prompt.txt): 画面描述 → K2 提示词, 风格锁(人设逐字/
  表面空白化/禁悬浮/禁风格词——画风由 k2.py 代码尾统一锁定)。管生成语法。
两步各自独立重跑零 GPU; LLM 只填内容不发明结构, 结构校验拦截。
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import llm as llm_mod
from . import shots as shots_mod
from .config import load

logger = logging.getLogger(__name__)

# 稿件段落标签: 【44-111秒｜核心概念拆解（一）】
_LABEL_RE = re.compile(r"【(\d+)-(\d+)秒｜([^】]+)】")


def _segments(script_text: str) -> list[dict[str, Any]]:
    """稿件 → [{t_start, t_end, label, text}]."""
    marks = list(_LABEL_RE.finditer(script_text))
    segs = []
    for i, m in enumerate(marks):
        end_pos = marks[i + 1].start() if i + 1 < len(marks) else len(script_text)
        segs.append({
            "t_start": int(m.group(1)), "t_end": int(m.group(2)),
            "label": m.group(3),
            "text": script_text[m.end():end_pos].strip(),
        })
    return segs


def _label_at(segs: list[dict[str, Any]], t: int) -> str:
    """秒数 → 所在【】段落标签 (作 label 上下文)."""
    for s in segs:
        if s["t_start"] <= t < s["t_end"]:
            return f"{s['t_start']}-{s['t_end']}秒｜{s['label']}"
    return ""


def resolve_book(db_path: str, book_ref: str) -> tuple[str, str]:
    """--book 支持 UUID 前缀或书名子串, 返回 (book_id, book_title)."""
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT id, book_title FROM book_projects").fetchall()
    finally:
        con.close()
    ref = book_ref.strip().lower()
    for r in rows:
        if r["id"].lower().startswith(ref):
            return r["id"], r["book_title"]
    for r in rows:
        if ref in (r["book_title"] or "").lower():
            return r["id"], r["book_title"]
    raise SystemExit(f"找不到书: {book_ref} (已有 {[r['book_title'] for r in rows]})")


def fetch_episode(db_path: str, book_id: str, ep: int) -> dict[str, Any]:
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT ep_index, title, script_text, status FROM book_episodes "
            "WHERE book_id = ? AND ep_index = ?",
            (book_id, ep),
        ).fetchone()
    finally:
        con.close()
    if not row or not (row["script_text"] or "").strip():
        raise SystemExit(f"第 {ep} 集不存在或稿件为空 (book_id={book_id})")
    return dict(row)


# ---------- Step1: 导演步 ----------

def _call_director(cfg, book_title: str, ep_title: str, user_body: str) -> dict[str, Any]:
    system = Path(cfg.director_prompt_file).read_text(encoding="utf-8")
    user = f"# 书名\n{book_title}\n\n# 集标题\n{ep_title}\n\n{user_body}"
    return llm_mod.chat_json(cfg.llm, system, user)


# ---------- Step2: 转译步 ----------

def _call_translator(cfg, items: list[dict[str, str]]) -> dict[str, str]:
    """[{shot_id, visual_desc}] → {shot_id: k2_prompt}."""
    if not items:
        return {}
    system = Path(cfg.translator_prompt_file).read_text(encoding="utf-8")
    user = "# 画面描述列表\n" + "\n".join(f"- {it['shot_id']}: {it['visual_desc']}" for it in items)
    raw = llm_mod.chat_json(cfg.llm, system, user)
    return {p.get("shot_id"): (p.get("k2_prompt") or "").strip() for p in (raw.get("prompts") or [])}


def _as_str(v: Any) -> str:
    """LLM 偶发把 str 字段给成 list (qc_notes 实锤) → 统一压成字符串."""
    if isinstance(v, list):
        return "；".join(str(x) for x in v if str(x).strip())
    return str(v) if v is not None else ""


_ENDING_PREFIX_RE = re.compile(r"^\s*[BCbc]\s*[:：]\s*")
_CALM_HOLD = "the scene settles into a calm, stable hold"


def _clean_ending(text: str) -> str:
    """去 'B:'/'C:' 字面前缀 (schema 示例被照抄实锤)."""
    return _ENDING_PREFIX_RE.sub("", _as_str(text)).strip()


def _normalize_shot(raw: dict[str, Any], idx: int, label: str = "") -> dict[str, Any]:
    sid = raw.get("shot_id") or f"s{idx + 1:02d}"
    anim = dict(raw.get("anim") or {})
    # anim 字段一律压平成 str (opening_desc 偶发 list, s38-s45 秒败实锤)
    for k in ("opening_desc", "physical_lock", "screen_exception", "ending"):
        anim[k] = _as_str(anim.get(k))
    if isinstance(anim.get("beats"), list):
        for b in anim["beats"]:
            if isinstance(b, dict):
                b["motion"] = _as_str(b.get("motion"))
    anim.setdefault("duration_s", 5)
    anim.setdefault("opening_desc", "")
    anim.setdefault("physical_lock", "")
    anim.setdefault("screen_exception", "nothing")
    anim.setdefault("beats", [])
    anim.setdefault("ending", "")
    return {
        "shot_id": sid,
        "label": _as_str(raw.get("label")) or label,
        "page_type": raw.get("page_type", "B"),
        "t_start": raw.get("t_start", 0),
        "t_end": raw.get("t_end", 0),
        "narration": _as_str(raw.get("narration")),
        "image_prompt_zh": _as_str(raw.get("k2_prompt") or raw.get("image_prompt_zh")),
        "anim": anim,
        "text_layer": raw.get("text_layer") or [],
        "qc_notes": _as_str(raw.get("qc_notes")),
        "extra_loras": raw.get("extra_loras") or [],
        "seed": shots_mod.new_seed(),
        "h3_seed": shots_mod.new_seed(),
        "status": "planned",
        "brand_card": False,
        "image_file": None,
        "video_file": None,
        "attempts": {"k2": 0, "h3": 0},
        "error": None,
        "reject_note": "",
    }


def _build_doc(book_id: str, book_title: str, ep: int, ep_title: str,
               director_raw: dict[str, Any], segs: list[dict[str, Any]], cfg) -> dict[str, Any]:
    """导演 segments → 新镜 (待转译) → 转译填 image_prompt_zh → doc."""
    pending: list[tuple[dict[str, Any], str]] = []
    for i, seg in enumerate(director_raw.get("segments") or []):
        label = _label_at(segs, int(seg.get("t_start", 0)))
        pending.append((_normalize_shot(seg, i, label), str(seg.get("visual_desc", ""))))

    mapping = _call_translator(cfg, [{"shot_id": s["shot_id"], "visual_desc": v} for s, v in pending])
    missed = [s["shot_id"] for s, _ in pending if not mapping.get(s["shot_id"])]
    for s, _ in pending:
        s["image_prompt_zh"] = mapping.get(s["shot_id"], "")
    if missed:
        logger.warning("[plan] 转译漏 %d 镜: %s", len(missed), missed)

    return {
        "version": shots_mod.SCHEMA_VERSION,
        "book_id": book_id,
        "book_title": book_title,
        "ep": ep,
        "ep_title": ep_title,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "planner_model": cfg.llm.model,
        "planner_flow": "director+translator v2",
        "ep_summary": director_raw.get("ep_summary", ""),
        "a_layer_notes": director_raw.get("a_layer_notes", ""),
        "shots": [s for s, _ in pending],
    }


_SPEECH_RATE = 4.6  # 字/秒 (0912 实测改: 2.5 老谭读书产线 ep1 368s/ep5 422s ≈4.5-4.6;
                    #  旧 5.5 是 IndexTTS2 时代锚, 系统性低估时长 → 漂移审计全批报警)


def narration_seconds(text: str) -> float:
    """口播 → 实讲秒数 (确定性, 0911 晚用户实锤: LLM 拍脑袋分时长必跑偏)."""
    return round(len(text.strip()) / _SPEECH_RATE, 2)


def _auto_fix(doc: dict[str, Any]) -> list[str]:
    """确定性结构修正 (LLM 只填内容, 代码管结构). 返回修正清单."""
    fixes: list[str] = []
    shots = doc["shots"]
    if not shots:
        return fixes

    # 0) 时长收归口播: duration_s = 字数/5.5 截到 [2,8]; 拍伸缩对齐 (0911 实锤 s27 超3.8s/s56 缺12s)
    for s in shots:
        anim = s["anim"]
        target = min(max(narration_seconds(s.get("narration", "")), 2.0), 8.0)
        if abs(float(anim.get("duration_s", 0)) - target) > 0.3:
            old = anim.get("duration_s")
            anim["duration_s"] = target
            fixes.append(f"{s['shot_id']} duration {old}→{target:.1f}s (口播 {len(s.get('narration',''))} 字)")
    # 时间轴按动画时长重铺 (口播节奏即成片节奏)
    t = 0.0
    for s in shots:
        s["t_start"] = round(t, 2)
        s["t_end"] = round(t + float(s["anim"]["duration_s"]), 2)
        t = s["t_end"]

    # 1) C 页分配: 分块导演会各自标 C (15个实锤) → 只留全集首镜+末镜;
    #    ending 只清 B:/C: 字面前缀 (白闪硬切已全镜放开, 不再换定格 — 用户令"快+炫")
    for s in shots:
        if s["page_type"] == "C":
            s["page_type"] = "B"
    shots[0]["page_type"] = "C"
    shots[-1]["page_type"] = "C"
    for s in shots:
        anim = s["anim"]
        anim["ending"] = _clean_ending(anim.get("ending", ""))
    if sum(1 for s in shots if s["page_type"] == "C") > 1:
        fixes.append("C 页收敛为首镜+末镜")

    # 2) 丢弃 <2s 段 (动画撑不起来, 口播由 A 层脊柱扛)
    keep = []
    for s in shots:
        seg_len = float(s["t_end"]) - float(s["t_start"])
        if seg_len < 2:
            fixes.append(f"{s['shot_id']} 段长 {seg_len:.1f}s <2s, 丢弃")
            continue
        keep.append(s)
    doc["shots"] = shots = keep
    if not shots:
        return fixes

    # 3) >8s 截断 (语义分段上限); 留下的小断口 ≤3s 是良性的 (底床时间近似, A 层定格兜), 不拉平
    for s in shots:
        seg_len = float(s["t_end"]) - float(s["t_start"])
        if seg_len > 8:
            s["t_end"] = float(s["t_start"]) + 8
            fixes.append(f"{s['shot_id']} 段长 {seg_len:.1f}s 截为 8s")

    # 4) anim 时长 = 段长; beats 伸缩对齐 (拍 motion 不动, 时间轴对齐)
    for s in shots:
        seg_len = round(float(s["t_end"]) - float(s["t_start"]), 2)
        if not (s["anim"].get("beats") or []) or abs(float(s["anim"].get("duration_s", 0)) - seg_len) > 0.01:
            before = float(s["anim"].get("duration_s", 0)) if s["anim"].get("beats") else None
            shots_mod.stretch_beats(s, seg_len)
            if before is None:
                fixes.append(f"{s['shot_id']} 无 beats, 补单一 hold 拍")
            else:
                fixes.append(f"{s['shot_id']} beats 总长 {before:.1f}s 伸缩对齐 {seg_len:.1f}s")
    return fixes


def _fill_gaps(cfg, doc: dict[str, Any], segs: list[dict[str, Any]]) -> None:
    """大洞 (>3s) 定向补镜: 导演分块偶发漏尾 (拆解二 29s 洞实锤), 不动已有镜."""
    for _round in range(2):
        ordered = sorted(doc["shots"], key=lambda s: float(s["t_start"]))
        gaps = [
            (float(a["t_end"]), float(b["t_start"]))
            for a, b in zip(ordered, ordered[1:])
            if float(b["t_start"]) - float(a["t_end"]) > 3
        ]
        if not gaps:
            return
        for hole_start, hole_end in gaps:
            seg = next((s for s in segs if s["t_start"] <= hole_start < s["t_end"]), None)
            if not seg:
                continue
            next_idx = max((int(s["shot_id"][1:]) for s in doc["shots"] if s["shot_id"][1:].isdigit()), default=0)
            body = (
                f"# 任务: 该稿件段落已有镜头, 但 {hole_start:.0f}-{hole_end:.0f} 秒没有画面。只为这个时间洞补镜。\n\n"
                f"# 口播稿（所在段落全文, 重点看 {hole_start:.0f} 秒之后讲什么）\n"
                f"【{seg['t_start']}-{seg['t_end']}秒｜{seg['label']}】\n{seg['text']}\n\n"
                f"# 要求\n- 切 ≤8s 语义段, 首尾相接恰好覆盖 [{hole_start:.0f}, {hole_end:.0f}]。\n"
                f"- page_type 全部 B。shot_id 依次 s{next_idx + 1:02d} 起。\n"
                f"- 只输出 segments 数组 JSON。"
            )
            logger.info("[plan] 补洞 %.0f-%.0fs ...", hole_start, hole_end)
            raw = _call_director(cfg, doc["book_title"], doc["ep_title"], body)
            got = (raw.get("segments") or [])[:6]
            if not got:
                logger.warning("[plan] 补洞返回空: %.0f-%.0fs", hole_start, hole_end)
                continue
            # 强制对齐洞边界
            got[0]["t_start"] = hole_start
            got[-1]["t_end"] = hole_end
            new = []
            for i, s in enumerate(got):
                s.setdefault("shot_id", f"s{next_idx + 1 + i:02d}")
                new.append((_normalize_shot(s, next_idx + i, f"{seg['t_start']}-{seg['t_end']}秒｜{seg['label']}"), str(s.get("visual_desc", ""))))
            mapping = _call_translator(cfg, [{"shot_id": s["shot_id"], "visual_desc": v} for s, v in new])
            for s, _ in new:
                s["image_prompt_zh"] = mapping.get(s["shot_id"], "")
                doc["shots"].append(s)
            doc["shots"].sort(key=lambda s: (float(s["t_start"]), s["shot_id"]))
            logger.info("[plan] 补洞 + %d 镜", len(new))
        # 下一轮复查 (补洞自身可能留小口, 由 _auto_fix 兜)


def plan(book_ref: str, ep: int, teardown_file: str | None = None, force: bool = False,
         scorched: bool = False, concept_only: bool = False) -> Path:
    """导演规划 v2 (0914 系统化切换): 叙事弧 + 运动设计 + 镜头组.

    v2 = director2 (scripts/anim_director.py 正身): 时间轴直出 TTS manifest
    (音频先行原生), 弧结构显性, 一组一段长镜 (5-24s), 美术圣经书级缓存.
    老 v1 两步法 (导演步+转译步 按【】段分块) 保留在下方 _v1_plan, 不再走.

    force 语义 (0915 用户令 三级清障):
      scorched=True  → 全清重来: 备份+删 shots.json → 整集全新规划 (页面 🧭 按钮)
      scorched=False → 逐场流水: 有产物的场保留, 其余场走单场管线 (保底能力)
    concept_only (0917 立意人闸): scorched 下只跑第一级 (切场+立意确认轮),
      落盘骨架+立意.md 即停 — 人确认/改判后续跑 resume_scenes (▶️ 按钮)。
    """
    cfg = load()
    book_id, book_title = resolve_book(cfg.db_path, book_ref)
    ep_row = fetch_episode(cfg.db_path, book_id, ep)

    existing = shots_mod.shots_path(book_title, ep)
    if existing.exists() and not force:
        raise SystemExit(f"已存在 {existing} (--force 覆盖; 已有产物状态会丢失)")

    from app.services.anim_pipeline import director2
    bible = director2.load_or_derive_bible(book_id, book_title)

    from app.services import (
        anim_draft,  # 延迟导入 (包外 app 侧); 音频门在 service 层已挡
    )
    audio = anim_draft.fetch_episode_audio(book_id, ep)
    # 0917 refs 协议: 句级表 (包实测+句切+静音吸附) 随 manifest 下发 — 规划镜 refs
    # 引句号, 系统查表定镜界, LLM 零绝对时间输出 (结构上不可能时间漂移)
    manifest = _audio_manifest(audio)

    if existing.exists() and force:
        if scorched:
            import shutil
            bak = existing.with_name(
                f"shots.json.bak_scorched_{time.strftime('%Y%m%d_%H%M%S')}")
            shutil.copy2(existing, bak)
            existing.unlink()
            logger.info("[plan] 🧭 全清重来 (0917 两级化): 旧版备份 %s 后删除, 切场+逐场分镜", bak.name)
            return _scorched_flow(book_id, book_title, ep, ep_row, manifest, bible,
                                  concept_only=concept_only)
        else:
            return _replan_flow(book_id, book_title, ep, manifest, bible)

    # 全新集 (无既有 doc) → 两级化同款 (0917: 两级化=规划正身, 单窗退役 —
    # ep2 实测单窗 25K 大包两连毙: 边界断裂+终点失配, 581s 长集必摊薄)
    if not existing.exists():
        return _scorched_flow(book_id, book_title, ep, ep_row, manifest, bible)

    doc = None
    problems: list[str] = []
    for attempt in (1, 2):  # LLM 偶发违反禁令/解析失败 — 自动重试一次, 零 GPU
        try:
            doc = director2.plan(book_id, book_title, ep, ep_row["title"],
                                 ep_row["script_text"], manifest, bible)
            problems = director2.validate_doc(doc)  # 硬校验: 结构问题才拦截
        except (SystemExit, ValueError) as exc:  # ValueError 含 JSON 解析失败
            problems = [str(exc)[:200]]
        if not problems:
            break
        for p in problems:
            logger.error("[plan] 校验不过 (第%d次): %s", attempt, p)
    if problems:
        raise SystemExit(f"规划产物校验失败 {len(problems)} 处 — 重跑 plan 即可 (零 GPU)")
    return _finalize_doc(doc, _manifest_segs(manifest), "plan")


def _manifest_segs(manifest: dict) -> list[dict[str, Any]]:
    """manifest 包级 segs → 审计用 {t_start, t_end} 列表 (音频轴 0 起)."""
    segs, cur = [], 0.0
    for s in manifest.get("segments") or []:
        dur = float(s.get("duration") or 0)
        segs.append({"t_start": round(cur, 2), "t_end": round(cur + dur, 2)})
        cur += dur
    return segs


def _finalize_doc(doc: dict[str, Any], segs: list[dict[str, Any]], tag: str) -> Path:
    """收尾四件套 (0917 三件 + 1006 prop_lock 确定性审计): 软警告 + 时长核验 + 媒体失效 + 落盘."""
    from app.services.anim_pipeline import director2
    # 1006 修 (卓有ep1 审阅实锤): prop_lock 颜色/尺度漂移确定性改写 — 落盘前最后一闸
    _pl = director2.prop_lock_audit(doc)
    if _pl:
        logger.warning("[%s] prop_lock 审计改写 %d 处: %s", tag, len(_pl), "; ".join(_pl[:6]))
    _tl = director2.text_layer_lint(doc)
    if _tl:
        logger.warning("[%s] text_layer 去污 %d 处: %s", tag, len(_tl), "; ".join(_tl[:6]))
    # 0919 60s 特区窗 (页面横幅/统一口径): 正文 [0,60)s = 全集 [tc, tc+60]
    _tc = float(load().brand_card.opening_sec)
    doc.setdefault("hook_zone", {"t_start": round(_tc, 2),
                                 "t_end": round(_tc + 60.0, 2)})
    warns = director2.collect_warnings(doc)
    warns += [str(x) for x in (doc.pop("_concept_warnings", None) or [])]
    # 1013 夜修: 装配后人物终审结果并入软警告 (页面 plan_warnings 可见)
    warns += [str(x) for x in (doc.pop("_post_asm_warnings", None) or [])]
    try:
        audit = director2.duration_audit(doc, segs)
        director2.write_duration_sheet(doc, audit)
        if audit["problems"]:
            logger.warning("[%s] 时长核验 %d 问题 (详见 时长核验.md): %s",
                           tag, len(audit["problems"]), audit["problems"][:3])
        warns += audit["problems"]
    except Exception as exc:  # noqa: BLE001 — 核验失败不挡落盘
        logger.warning("[%s] 时长核验失败 (不挡落盘): %s", tag, exc)
    if warns:
        doc["plan_warnings"] = warns
        for w in warns[:8]:
            logger.warning("[%s] 软警告: %s", tag, w)
    else:
        doc.pop("plan_warnings", None)
    _old = shots_mod.load_doc(doc.get("book_title"), doc.get("ep")) if (doc.get("book_title") and shots_mod.shots_path(doc["book_title"], doc["ep"]).exists()) else None
    _st = shots_mod.strip_media_on_narr_change(_old, doc)
    if _st:
        logger.warning("[narr-change] 文案变→媒体失效 %d 镜: %s", len(_st), _st)
    path = shots_mod.save(doc)
    logger.info("[%s] %s → %s", tag, shots_mod.summary(doc), path)
    return path


def _scorched_flow(book_id: str, book_title: str, ep: int, ep_row: dict[str, Any],
                   manifest: dict, bible: dict, concept_only: bool = False) -> Path:
    """🧭 全清两级化 (0917 用户令): 切场一次 + 逐场分镜 — 根治整集单窗预算摊薄.

    一级 plan_arcs 小输出切场 (含每场 visual_metaphor), 二级逐场 plan_scene
    (单窗预算足, 技术合规已证明显著更好); 每场成功即落盘 (断点续), 全败抛错
    盘上停在骨架/已完成场; 收尾全集归真 + 拆镜分身重设计。
    concept_only=True: 一级即停 (立意人闸) — 立意.md 落盘待人工确认, 续跑走 resume_scenes。
    """
    from app.services.anim_pipeline import director2

    doc: dict[str, Any] | None = None
    problems: list[str] = []
    for attempt in (1, 2):
        try:
            doc = director2.plan_arcs(book_id, book_title, ep, ep_row["title"],
                                      ep_row["script_text"], manifest, bible)
            problems = director2.validate_doc(doc)
        except (SystemExit, ValueError) as exc:
            problems = [str(exc)[:200]]
        if not problems:
            break
        for p in problems:
            logger.error("[scorched] 切场校验不过 (第%d次): %s", attempt, p)
    if problems or doc is None:
        raise SystemExit(f"切场失败 ×2: {problems[0][:120]} — 重跑 plan 即可 (零 GPU)")
    shots_mod.save(doc)  # 骨架落盘: plan_scene 从盘读
    arc_ids = [str(a["arc_id"]) for a in doc.get("arcs") or []]

    # 1007 P2: 法层拍摄脚本生成 (切场后、立意闸前) — 镜头序/光影/连续性
    # 1013 修 (卓有ep1 实锤): 法层是术/器链的根依赖, 瞬时 LLM 故障一把定死
    # = 整集分镜裸奔 — 同产线 LLM 调用惯例补重试 ×2 (零 GPU, 只费一次调用)
    from app.services.anim_pipeline.shooting_script import generate as gen_shooting
    from app.services.anim_pipeline.shooting_script import validate
    _ss_ok = False
    for _attempt in (1, 2):
        try:
            ss = gen_shooting(book_id, book_title, ep)
            issues = validate(ss)
            if issues:
                logger.warning("[scorched] 法层校验 %d 处 (不阻断, 供人检): %s",
                               len(issues), issues[:3])
            else:
                logger.info("[scorched] 法层拍摄脚本 ✓ %d 场 (校验全通过)",
                            len(ss.get("scenes") or []))
            _ss_ok = True
            break
        except Exception as exc:  # noqa: BLE001 — 法层失败不阻断产线
            logger.warning("[scorched] 法层生成失败 (第%d次, 不阻断分镜): %s",
                           _attempt, exc)
    if not _ss_ok:
        logger.warning("[scorched] ⚠️ 法层两把均败 — 后续术/器链将离线 "
                       "(分镜退规层裸奔), 立意确认后重跑 🧭 或手动重生法层")

    if concept_only:
        logger.info("[scorched] 🛑 立意人闸: 一级切场 ✓ %d 场 → 立意.md 待人工确认, "
                    "确认后续跑 ▶️分镜续跑 (arc_ids=%s)", len(arc_ids), "/".join(arc_ids))
        shots_mod.save(doc)
        return shots_mod.shots_path(book_title, ep)
    return _scenes_flow(book_id, book_title, ep, manifest, bible, doc)


def resume_scenes(book_ref: str, ep: int) -> Path:
    """▶️ 分镜续跑 (0917 立意人闸第二段): 从盘上骨架/已完成场续跑至全集."""
    cfg = load()
    book_id, book_title = resolve_book(cfg.db_path, book_ref)
    ep_row = fetch_episode(cfg.db_path, book_id, ep)
    from app.services import anim_draft
    audio = anim_draft.fetch_episode_audio(book_id, ep)
    manifest = _audio_manifest(audio)
    from app.services.anim_pipeline import director2
    bible = director2.load_or_derive_bible(book_id, book_title)
    doc = shots_mod.load_doc(book_title, ep)
    if not (doc.get("arcs") or []):
        raise SystemExit("盘上无场结构 — 先跑 🧭 规划 (立意闸) 再续跑")
    return _scenes_flow(book_id, book_title, ep, manifest, bible, doc)


def _scenes_flow(book_id: str, book_title: str, ep: int,
                 manifest: dict, bible: dict, doc: dict[str, Any]) -> Path:
    """两级化第二段: 逐场分镜 (跳过已过闸的场) + 收尾归真/重设计/巡检/核验.

    1014 双根治 (ep4 A6 死锁实锤):
    - done 判定一把尺: 旧版「盘上有镜即 done」让带病落盘的场永远够不着,
      收口闸又每次必验它 → 续跑无限同错。现为: 有镜且过本场切片闸才算 done,
      带病存量场重回待跑队列 (存量违规作首轮 fix_hints 回喂)。
    - in-loop 闸按场切片: 收口闸的 A6 违规不再标到正在跑的 A7 头上;
      全集级校验收尾一次过, 失败按归因点名带病场 (下轮续跑自动重开)。"""
    from app.services.anim_pipeline import director2
    arc_order = [str(a["arc_id"]) for a in doc.get("arcs") or []]
    has_shots = {str(s.get("arc_id") or "") for s in doc.get("shots") or []}
    # 磨合成果保护 (同 _replan_flow 教义): 有生成产物的场不自动重开 — 病走
    # 人检 (plan_warnings 可见); 自动重开只针对纯规划态的带病场
    protected = {str(s.get("arc_id") or "") for s in doc.get("shots") or []
                 if s.get("status") in _FLOW_PROTECTED}
    sick: dict[str, list[str]] = {}
    for arc_id in arc_order:
        if arc_id not in has_shots or arc_id in protected:
            continue
        # 与 in-loop 闸同一把尺: 风格类违规不阻断 (人检修), 不触发重开
        _iss = [p for p in _arc_hard_issues(doc, bible, arc_id) if not _is_style_issue(p)]
        if _iss:
            sick[arc_id] = _iss
    if sick:
        logger.warning("[scorched] 带病存量场 %d 个重回队列 (存量违规作首轮回喂): %s",
                       len(sick), "; ".join(f"{a}: {v[0][:70]}" for a, v in sick.items()))
    arc_ids = [a for a in arc_order if a not in has_shots or a in sick]
    if not arc_ids:
        raise SystemExit("全部场已有分镜且过闸 — 无可续跑 (要重做某场走场头 🔁)")
    logger.info("[scorched] 逐场分镜: 待跑 %d 场 (%s) — 每场 1-2 分钟",
                len(arc_ids), "/".join(arc_ids))
    final_doc = doc
    for i, arc_id in enumerate(arc_ids):
        problems: list[str] = []
        seeded = list(sick.get(arc_id) or [])
        for attempt in (1, 2):
            try:
                hints = problems if attempt > 1 else (seeded or None)
                # 1014 ep4 A6 实锤: 景深配额失败时 writer 整场重掷打不进 (点名hint
                # 映射不到新稿) — 第二把强制 fallback (法层景别节奏兜底, ep3 实证)
                _ff = attempt > 1 and any("景深" in str(p) for p in (problems or []))
                final_doc = _dispatch_scene(book_id, book_title, ep, arc_id, manifest, bible,
                                                 fix_hints=hints, force_fallback=_ff)
                problems = _arc_hard_issues(final_doc, bible, arc_id)
            except (SystemExit, ValueError) as exc:
                problems = [str(exc)[:200]]
            except Exception as exc:  # noqa: BLE001 — LLM 网络/瞬时故障同享重试网 (0323 实况)
                problems = [f"{type(exc).__name__}: {exc}"[:200]]
            if not problems:
                break
            # 0924 修: 花字/scene_tag 类风格问题不阻断场级规划 — 结构问题才拦
            _structural = [p for p in problems if not _is_style_issue(p)]
            if not _structural and problems:
                logger.warning("[scorched] 场 %s 风格问题 (不阻断, 人检修): %s",
                               arc_id, "; ".join(problems[:3]))
                problems = []  # 0925 修: 放行必须清 problems — 旧版带风格问题出循环,
                               # 被下方 if problems 误判"分镜失败×2"炸 SystemExit
                               # (HBO ep9 A2 两连死实锤, 分镜停在 A1)
                break  # 风格问题放行, 结构问题才重试
            for p in _structural or problems:
                logger.error("[scorched] 场 %s 校验不过 (第%d次): %s", arc_id, attempt, p[:120])
        if problems:
            raise SystemExit(f"场 {arc_id} 分镜失败 ×2: {problems[0][:120]} — "
                             f"盘上停在第 {i} 场成功点, ▶️分镜续跑 接着跑")
        shots_mod.save(final_doc)
        logger.info("[scorched] %d/%d 场 %s ✓", i + 1, len(arc_ids), arc_id)

    # 1007 P5: 术层创意槽填充 (法层模板 → LLM 填 action/detail/atmosphere)
    try:
        from app.services.anim_pipeline.creative_slots import fill_creative_slots
        from app.services.anim_pipeline.registers import assign_registers
        from app.services.anim_pipeline.shooting_script import load as load_ss
        ss = load_ss(book_title, ep)
        if ss:
            # 1009 拆书导演模型: 规层先钉主体寄存器 (句型→寄存器, 零 LLM),
            # 术层按寄存器分支填槽, 器层按寄存器选组装模板
            assign_registers(final_doc, bible)
            n = fill_creative_slots(final_doc, ss, bible=bible)
            logger.info("[scorched] 术层创意槽: %d 镜填充 ✓", n)
            # 1009 P1 三级降级链 L1 (设计 §3.3): STRUCT 宪法下 plan_scene 不再产
            # keyframe — 未填镜必须有人顶。补填重试 ×2 (幂等 only), 零进展即停,
            # L2=器层默认槽直装 (assemble_episode 内打标 slots_source=default)
            for _retry in range(2):
                _un = {str(s.get("shot_id")) for s in final_doc.get("shots") or []
                       if str(s.get("narration") or "").strip()
                       and not s.get("creative_slots")}
                if not _un:
                    break
                _n2 = fill_creative_slots(final_doc, ss, only=_un)
                if not _n2:
                    break
            # 1008 P6: 器层确定性组装 (法+术+道 → K2 画面句 + H3 骨架, 零 LLM)
            from app.services.anim_pipeline.assembler import assemble_episode
            n_asm = assemble_episode(final_doc, ss, bible)
            logger.info("[scorched] 器层组装: %d 镜 ✓ (确定性, 零 LLM)", n_asm)
            # 1013 夜修: 装配后人物终审 — plan_scene 阶段配额让开 _kf_missing 占位镜
            # (STRUCT 禁 LLM 写逐镜 keyframe), 真画面句由器层重建后在此一把尺复量。
            # 器层是确定性输出, 重掷 LLM 无解 → 软警告进 plan_warnings (人眼决策:
            # 修寄存器/立意层), 不炸产线。
            _pq = director2.person_quota_issues(final_doc, skip_placeholders=False)
            for _w in _pq:
                logger.warning("[scorched] 装配后人物终审: %s", _w)
            final_doc.setdefault("_post_asm_warnings", []).extend(_pq)
        else:
            # 1013 修 (卓有ep1 A1 全场空镜实锤): 法层在盘缺失 → 寄存器/创意槽/
            # 器层组装整链离线, 分镜在无导演模型护栏下落盘 — 旧版此处静默跳过,
            # 产物照常落盘, 人只在生图后才发现。降级必须喊出来。
            logger.warning("[scorched] ⚠️ 法层 shooting_script.json 缺失 — 术/器链"
                           " (寄存器+创意槽+器层组装+观众替身) 整链离线, 本轮分镜"
                           "为规层裸奔产物, 概念场易全空镜; 先重生法层再重规划")
    except Exception as exc:  # noqa: BLE001 — 术层失败不阻断 (器层默认槽兜底)
        logger.warning("[scorched] 术层创意槽失败 (不阻断, 器层默认槽兜底): %s", exc)

    # 收尾: 句级归真 + 拆镜分身重设计 (与单窗 plan 同款, 失败均降级不炸)
    try:
        from app.services import anim_draft as _ad
        _sseg = _ad._sentence_segs(_ad.fetch_episode_audio(book_id, ep)["files"])
        if _sseg:
            director2._realign_narrations(final_doc, _sseg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scorched] 句级归真失败 (对齐可补): %s", exc)
    try:
        director2.redesign_split_clones(final_doc, bible)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scorched] 拆镜分身重设计失败 (保持克隆, 软警告亮灯): %s", exc)
    # 全片巡检轮 (0917 调优令): 逐场渐进=局部视角 — 一轮跨场一致性修正, 零 patch=不落刀
    try:
        _rv = director2.consistency_review(final_doc, bible)
        if _rv.get("patched"):
            logger.info("[scorched] 巡检修正 %d 镜 → 回 planned 待重生", _rv["patched"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scorched] 全片巡检轮失败 (跳过, 不炸规划): %s", exc)
    # 1014 收口闸 (全集级一次过): in-loop 已按场切片, 跨场/全集问题在此收口 —
    # 失败按归因点名带病场, 下轮 ▶️续跑 会自动重开它们 (闭环, 不再无限同错)
    _final_hard = [i for i in _final_gate_issues(final_doc, bible) if not str(i).startswith("⚠")]
    if _final_hard:
        _hit = sorted(_attr_arcs(final_doc, _final_hard))
        _who = "/".join(_hit) if _hit else "全集级 (无法归因单一场, 人工介入)"
        raise SystemExit(f"终产闸 {len(_final_hard)} 处未过 — 带病场: {_who}: "
                         f"{_final_hard[0][:120]} — ▶️分镜续跑 会自动重做带病场")
    return _finalize_doc(final_doc, _manifest_segs(manifest), "scorched")



def _final_gate_issues(doc, bible):
    """1013 P0 阶段3: 主路终产闸 (final_gate.validate_final, 软⚠项不阻断重试网)。"""
    try:
        from app.services.anim_pipeline import final_gate
        return final_gate.validate_final(doc, bible)
    except Exception as exc:  # noqa: BLE001 — 闸故障不炸产线, 退老闸
        logger.warning("[final-gate] 终产闸异常退 validate_doc: %s", exc)
        return director2.validate_doc(doc)


# 1014 归因 (ep4 A6 死锁根治配套): 全 doc 闸的违规 → 涉事场 id 集。
# 闸消息是机器生成的稳定格式 (场A6[mid] / s74 / 场窗断裂 A5→A6), token 反查
# 即可归因; 归因不到 (全集级: 末组终点/人物密度/p90) 返回空集, 只在收尾闸拦。
# 1015 修 (ep4 A3 实锤): \b 把 CJK 当词字符 — 「场A3」「把s20」无词边界, token 全瞎
# → 带病场误报"全集级"、续跑不重开 = 新死锁形态。边界改 ASCII 环视 (s20 也不误吞 s202)。
_ARC_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([Aa]\d+|s\d+[a-z]?)(?![A-Za-z0-9])")


def _attr_arcs(doc: dict[str, Any], issues: list[str]) -> set[str]:
    sid2arc = {str(s.get("shot_id")): str(s.get("arc_id") or "") for s in doc.get("shots") or []}
    arc_ids = {str(a.get("arc_id") or "") for a in doc.get("arcs") or []}
    out: set[str] = set()
    for issue in issues:
        for m in _ARC_TOKEN_RE.finditer(str(issue)):
            tok = m.group(1)
            if tok in arc_ids:
                out.add(tok)
            elif tok in sid2arc:
                out.add(sid2arc[tok])
    return out - {""}


def _arc_hard_issues(doc: dict[str, Any], bible: dict[str, Any], arc_id: str) -> list[str]:
    """终产闸硬违规按场切片: 归因到本场的才拦本场 (1014 错标头根治)。

    旧版 in-loop 闸量全 doc, A6 的存量违规标到正在跑的 A7 头上 — 标头与病根
    错场, 且重掷 A7 永远治不了 A6。全集级校验移到收尾一次过。"""
    hard = [i for i in _final_gate_issues(doc, bible) if not str(i).startswith("⚠")]
    if not hard:
        return []
    return [i for i in hard if arc_id in _attr_arcs(doc, [i])]


def _dispatch_scene(book_id: str, book_title: str, ep: int, arc_id: str,
                    manifest: dict, bible: dict,
                    fix_hints: list[str] | None = None,
                    force_fallback: bool = False) -> dict:
    """1013 P0 阶段2: 场规划统一调度 — 单写者主路 (宪法 writer_enabled) /
    现行结构链原路直通; writer 两败场内退 fallback (scene_writer 内闭环)。"""
    from app.services.anim_pipeline import scene_writer
    return scene_writer.dispatch_scene(book_id, book_title, ep, arc_id, manifest, bible,
                                       fix_hints=fix_hints, force_fallback=force_fallback)


# 逐场流水保护态: 场内任一镜有生成产物 = 该场锁定 (磨合成果)
_FLOW_PROTECTED = {"img_done", "approved", "anim_done"}

# 风格类违规 (花字/scene_tag 族): 三闸共用一把尺 — 不阻断场级规划, 人检修
_STYLE_KW = ('花字', '单字', '裸数字', 'scene_tag')


def _is_style_issue(p: str) -> bool:
    return any(k in p for k in _STYLE_KW)


def _replan_flow(book_id: str, book_title: str, ep: int,
                 manifest: dict, bible: dict) -> Path:
    """逐场流水重规划: 有产物的场保留, 其余场逐个走单场重规划管线 (0915 用户令).

    行为与场头 🔁 按钮完全同管线 (plan_scene 钉窗拼接), 磨合成果永不冲掉;
    每场成功即落盘 (断点续在下一场), 全败不动盘 (开场整体备份一次)。
    """
    import shutil


    doc = shots_mod.load_doc(book_title, ep)
    arcs = doc.get("arcs") or []
    if not arcs:
        raise SystemExit("旧 doc 无场结构 (v1 数据?) — 不可逐场流水")

    protected: list[str] = []
    todo: list[str] = []
    for a in arcs:
        key = str(a.get("arc_id", "")).lower()
        scene = [s for s in doc["shots"] if str(s.get("arc_id", "")).lower() == key]
        (protected if any(s.get("status") in _FLOW_PROTECTED for s in scene) else todo).append(a["arc_id"])
    if not todo:
        raise SystemExit("全部场已锁定 (有生成产物) — 无可重规划场。"
                         "要重做某场: 先在该场卡片打回/重 roll 使其回 planned, 或用单场 🔁")
    logger.info("[replan-flow] 逐场流水: 锁定 %s | 重规划 %s (%d 场 × LLM, 每场 1-2 分钟)",
                "/".join(protected) or "无", "/".join(todo), len(todo))

    path = shots_mod.shots_path(book_title, ep)
    bak = path.with_name(f"shots.json.bak_flow_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(path, bak)
    logger.info("[replan-flow] 旧版备份: %s", bak.name)

    final_doc = doc
    for i, arc_id in enumerate(todo):
        problems: list[str] = []
        for attempt in (1, 2):
            try:
                final_doc = _dispatch_scene(book_id, book_title, ep, arc_id, manifest, bible,
                                                 fix_hints=problems if attempt > 1 else None)
                # 1014 修: 只量本场切片 (同 _scenes_flow/replan_scene — 全 doc 闸
                # 的他场存量债不拦本场, 锁定场的病留给续跑带病场重开机制)
                problems = _arc_hard_issues(final_doc, bible, arc_id)
            except (SystemExit, ValueError) as exc:
                problems = [str(exc)[:200]]
            if not problems:
                break
            # 0926 修: 风格问题不阻断 (同 replan 单场闸, 他场存量债不挡本场)
            _structural = [p for p in problems if not _is_style_issue(p)]
            if not _structural:
                logger.warning("[replan-flow] 场 %s 风格问题不阻断 (人检修): %s",
                               arc_id, "; ".join(problems[:3]))
                problems = []
                break
            for p in problems:
                logger.error("[replan-flow] 场 %s 校验不过 (第%d次): %s", arc_id, attempt, p[:120])
        if problems:
            raise SystemExit(f"场 {arc_id} 重规划失败 ×2: {problems[0][:120]} — "
                             f"盘上数据停在第 {i} 场成功点, 重跑 plan 续流")
        shots_mod.save(final_doc)  # 每场落盘: 下一场的 id 编号/窗口基于最新
        logger.info("[replan-flow] %d/%d 场 %s ✓ (锁 %d 场未动)",
                    i + 1, len(todo), arc_id, len(protected))

    return _finalize_doc(final_doc, _manifest_segs(manifest), "replan-flow")


def _audio_manifest(audio: dict) -> dict:
    """音频 → 规划 manifest (0918 收敛): 包级时长+module_id+句级表一次到位.

    refs 协议三入口 (plan/resume_scenes/replan_scene) 共用 — 单场重规划漏带
    sent_table 会让 refs 失效退回 LLM 自报时间 (ep2 A1 21.5s 巨镜实锤)。"""
    from app.services import anim_draft
    return {"segments": [{"text": f["text"], "duration": f["dur_s"],
                          "module_id": f.get("module_id")}
                         for f in audio["files"]],
            "sent_table": anim_draft.sentence_table(audio["files"])}


def replan_scene(book_ref: str, ep: int, arc_id: str) -> Path:
    """单场重规划 (逐场磨合: 本场 规划→K2→H3 反复测, 其余场不动).

    失败不动盘上数据 (校验网内重试, 全败即抛); 成功先备份 shots.json 再落盘.
    """
    import shutil
    cfg = load()
    book_id, book_title = resolve_book(cfg.db_path, book_ref)
    fetch_episode(cfg.db_path, book_id, ep)

    from app.services.anim_pipeline import director2
    bible = director2.load_or_derive_bible(book_id, book_title)
    from app.services import anim_draft  # 音频先行: 单场窗也吃真实 manifest
    audio = anim_draft.fetch_episode_audio(book_id, ep)
    manifest = _audio_manifest(audio)

    doc = None
    problems: list[str] = []
    for attempt in (1, 2):
        try:
            # 0926 修: 二次尝试带违规清单回喂 (盲掷连败实锤)
            doc = _dispatch_scene(book_id, book_title, ep, arc_id, manifest, bible,
                                       fix_hints=problems if attempt > 1 else None)
            # 1014 修: 只量本场切片 — 他场存量债不再拦本场 (A6 病标 A7 头实锤)
            problems = _arc_hard_issues(doc, bible, arc_id)
        except (SystemExit, ValueError) as exc:
            problems = [str(exc)[:200]]
        if not problems:
            break
        # 0926 修: 与 scorched 同闸 — 花字/scene_tag 风格问题不阻断 (validate_doc 验的是
        # 合并全doc, 他场存量风格债把本场重规划全挡实锤); 结构问题才拦
        _structural = [p for p in problems if not _is_style_issue(p)]
        if not _structural:
            logger.warning("[replan] 风格问题不阻断 (人检修): %s", "; ".join(problems[:3]))
            problems = []
            break
        for p in problems:
            logger.error("[replan] 校验不过 (第%d次): %s", attempt, p)
    if problems:
        raise SystemExit(f"单场重规划校验失败 {len(problems)} 处 — 盘上旧规划未动, 重试即可 (零 GPU)")

    path = shots_mod.shots_path(book_title, ep)
    if path.exists():
        bak = path.with_name(f"shots.json.bak_replan_{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, bak)
        logger.info("[replan] 旧版备份: %s", bak.name)

    # 1008 P5/P6: 术层创意槽 + 器层组装 (单场重规划同链 — 只导演全写会退回旧链)
    try:
        from app.services.anim_pipeline.assembler import assemble_episode
        from app.services.anim_pipeline.creative_slots import fill_creative_slots
        from app.services.anim_pipeline.registers import assign_registers
        from app.services.anim_pipeline.shooting_script import load as load_ss
        ss = load_ss(book_title, ep)
        if ss:
            # 1009 寄存器: 全doc未钉镜一次补齐 (零LLM幂等) — 他场旧镜无钉标时
            # 器层会退实景原链, 主体围栏失效 (ep2 15镜实锤); 本场新镜自然包含
            assign_registers(doc, bible)
            n_fill = fill_creative_slots(doc, ss, bible=bible)
            # 1009 P1 三级降级链 L1: 未填镜补填重试 ×2 (同 scorched 流; L2=器层默认槽)
            for _retry in range(2):
                _un = {str(s.get("shot_id")) for s in doc.get("shots") or []
                       if str(s.get("narration") or "").strip()
                       and not s.get("creative_slots")}
                if not _un:
                    break
                _n2 = fill_creative_slots(doc, ss, only=_un)
                if not _n2:
                    break
            n_asm = assemble_episode(doc, ss, bible)
            logger.info("[replan] %s 术层 %d 镜 + 器层组装 %d 镜 ✓", arc_id.upper(), n_fill, n_asm)
            # 1013 夜修: 装配后人物终审 (同 _scenes_flow, 软警告不炸)
            _pq = director2.person_quota_issues(doc, skip_placeholders=False)
            for _w in _pq:
                logger.warning("[replan] 装配后人物终审: %s", _w)
            doc.setdefault("_post_asm_warnings", []).extend(_pq)
        else:
            # 1013 修 (同 _scorched_flow): 法层缺失 → 术/器链离线必须喊出来,
            # 单场重规划退规层裸奔链 (寄存器/替身/主体围栏全无) 而非无声降级
            logger.warning("[replan] ⚠️ 法层 shooting_script.json 缺失 — 术/器链"
                           "离线, 本场走规层裸奔链; 先重生法层再单场重规划可回全链")
    except Exception as exc:  # noqa: BLE001 — 术/器失败不阻断 (器层默认槽兜底)
        logger.warning("[replan] 术层/器层失败 (不阻断): %s", exc)

    path = _finalize_doc(doc, _manifest_segs(manifest), "replan")
    logger.info("[replan] %s 场 %s 重生 → %s", book_title, arc_id.upper(), path)
    return path


def _v1_plan(book_ref: str, ep: int, teardown_file: str | None = None, force: bool = False) -> Path:
    """v1 两步法 (导演步+转译步, ≤8s 碎镜) — 已被 director2 替代, 留档."""
    cfg = load()
    book_id, book_title = resolve_book(cfg.db_path, book_ref)
    ep_row = fetch_episode(cfg.db_path, book_id, ep)
    from app.services.anim_pipeline import (
        director2,  # 留档路径补漏 (0919 pyflakes): L703 collect_warnings 用
    )

    existing = shots_mod.shots_path(book_title, ep)
    if existing.exists() and not force:
        raise SystemExit(f"已存在 {existing} (--force 覆盖; 已有产物状态会丢失)")

    segs = _segments(ep_row["script_text"])
    if not segs:
        raise SystemExit("稿件里没解析到【起-止秒｜段落名】标签 — 两步法依赖段落标签分块")
    teardown = ""
    if teardown_file:
        td = Path(teardown_file)
        if td.exists():
            teardown = f"\n\n# 拆解报告参考 (风格套路)\n{td.read_text(encoding='utf-8')[:8000]}"
        else:
            logger.warning("拆解报告不存在, 跳过: %s", td)

    # Step1 导演步: 按【】段落分块调用 (kimi-highspeed 输出硬帽 ~24k 字符,
    # 整集 40+ 段的导演 JSON 单发必截断 — 0911 三连实锤; 分块响应 ~5k 字符安全)
    all_segments: list[dict[str, Any]] = []
    ep_summary = ""
    for seg in segs:
        body = (
            f"# 任务: 只为下面这一个稿件段落做导演分段 (整集其余段不用管)\n\n"
            f"# 口播稿（该段）\n【{seg['t_start']}-{seg['t_end']}秒｜{seg['label']}】\n{seg['text']}\n\n"
            f"# 要求\n- 在 [{seg['t_start']}, {seg['t_end']}] 区间内切成 ≤8s 语义段, 首尾相接覆盖全段。\n"
            f"- page_type: 本段若是钩子/核心隐喻/结尾问句则相关子段标 C, 否则全 B。\n"
            f"- 只输出 segments 数组 JSON (不需要 ep_summary)。{teardown}"
        )
        logger.info("[plan] Step1 导演 %s-%ss｜%s ...", seg["t_start"], seg["t_end"], seg["label"])
        raw = _call_director(cfg, book_title, ep_row["title"], body)
        got = raw.get("segments") or []
        if not got:
            logger.warning("[plan] 导演步该段返回空, 跳过: %s", seg["label"])
            continue
        ep_summary = ep_summary or str(raw.get("ep_summary", ""))
        all_segments.extend(got)
        logger.info("[plan] Step1 %s → %d 子段", seg["label"], len(got))

    if not all_segments:
        raise SystemExit("导演步全部段落返回空")
    director_raw = {"ep_summary": ep_summary, "segments": all_segments}
    logger.info("[plan] Step1 合计 %d 子段, Step2 转译...", len(all_segments))
    doc = _build_doc(book_id, book_title, ep, ep_row["title"], director_raw, segs, cfg)
    for f in _auto_fix(doc):
        logger.info("[plan] auto-fix: %s", f)
    _fill_gaps(cfg, doc, segs)
    for f in _auto_fix(doc):
        logger.info("[plan] auto-fix(补洞后): %s", f)
    tagged = shots_mod.tag_brand_cards(doc)
    if tagged:
        logger.info("[plan] 品牌卡镜: %s (k2/h3 零 GPU 直通, 草稿插定稿卡)", ",".join(tagged))

    problems = shots_mod.validate(doc)
    if problems:
        for p in problems:
            logger.error("[plan] 校验不过: %s", p)
        raise SystemExit(f"规划产物结构校验失败 {len(problems)} 处 — 重跑 plan 即可 (零 GPU 成本)")

    warns = director2.collect_warnings(doc)  # 软警告: 不挡, 记录留痕
    if warns:
        doc["plan_warnings"] = warns
        for w in warns[:8]:
            logger.warning("[plan] 软警告: %s", w)
    _old = shots_mod.load_doc(doc.get("book_title"), doc.get("ep")) if (doc.get("book_title") and shots_mod.shots_path(doc["book_title"], doc["ep"]).exists()) else None
    _st = shots_mod.strip_media_on_narr_change(_old, doc)
    if _st:
        logger.warning("[narr-change] 文案变→媒体失效 %d 镜: %s", len(_st), _st)
    path = shots_mod.save(doc)
    logger.info("[plan] %s → %s", shots_mod.summary(doc), path)
    return path


def extend_segment(book_ref: str, ep: int, seg_ref: str, count: int) -> Path:
    """为一个【】段落扩镜 (不动已有镜头): 导演切 count 个 ≤8s 子段 + 转译."""
    cfg = load()
    book_id, book_title = resolve_book(cfg.db_path, book_ref)
    ep_row = fetch_episode(cfg.db_path, book_id, ep)
    doc = shots_mod.load_doc(book_title, ep)

    segs = _segments(ep_row["script_text"])
    if not segs:
        raise SystemExit("稿件里没解析到【起-止秒｜段落名】标签")
    seg = next((s for s in segs if seg_ref in f"{s['t_start']}-{s['t_end']}｜{s['label']}"), None)
    if not seg:
        cands = [f"{s['t_start']}-{s['t_end']}｜{s['label']}" for s in segs]
        raise SystemExit(f"段落没匹配到: {seg_ref} (候选: {cands})")

    existing = [s for s in doc["shots"] if seg["t_start"] <= s["t_start"] and s["t_end"] <= seg["t_end"]]
    existing_desc = "\n".join(
        f"- {s['shot_id']} [{s['page_type']}] {s['t_start']}-{s['t_end']}s: {s['narration'][:60]}"
        for s in existing
    ) or "(无)"
    next_idx = max((int(s["shot_id"][1:]) for s in doc["shots"] if s["shot_id"][1:].isdigit()), default=0)

    body = f"""# 任务: 只为下面这一个稿件段落做导演工作 (整集其余段不用管)

# 口播稿（该段）
【{seg['t_start']}-{seg['t_end']}秒｜{seg['label']}】
{seg['text']}

# 该段已有镜头（新段画面与其错开, 不重复构图）
{existing_desc}

# 要求
- 切成恰好 {count} 个 ≤8s 语义段（字段同 schema, segments 数组）。
- shot_id 依次为 s{next_idx + 1:02d}…s{next_idx + count:02d}。
"""
    logger.info("[extend] %s 导演补 %d 镜 (段 %ds, 已有 %d 镜)...", seg_ref, count, seg["t_end"] - seg["t_start"], len(existing))
    director_raw = _call_director(cfg, book_title, ep_row["title"], body)
    if not director_raw.get("segments"):
        raise SystemExit("导演步没返回 segments")

    pending: list[tuple[dict[str, Any], str]] = []
    for i, s in enumerate(director_raw["segments"][:count]):
        pending.append((_normalize_shot(s, next_idx + i, f"{seg['t_start']}-{seg['t_end']}秒｜{seg['label']}"), str(s.get("visual_desc", ""))))
    mapping = _call_translator(cfg, [{"shot_id": s["shot_id"], "visual_desc": v} for s, v in pending])
    new_shots: list[dict[str, Any]] = []
    for s, _ in pending:
        s["image_prompt_zh"] = mapping.get(s["shot_id"], "")
        new_shots.append(s)

    doc["shots"].extend(new_shots)
    doc["shots"].sort(key=lambda s: (s["t_start"], s["shot_id"]))

    problems = shots_mod.validate(doc)
    if problems:
        for p in problems:
            logger.warning("[extend] 校验问题: %s", p)
        new_ids = {n["shot_id"] for n in new_shots}
        bad_new = [p for p in problems if any(i in p for i in new_ids)]
        if bad_new:
            raise SystemExit(f"新镜结构校验失败 {len(bad_new)} 处 — 重跑 extend (零 GPU)")

    _old = shots_mod.load_doc(doc.get("book_title"), doc.get("ep")) if (doc.get("book_title") and shots_mod.shots_path(doc["book_title"], doc["ep"]).exists()) else None
    _st = shots_mod.strip_media_on_narr_change(_old, doc)
    if _st:
        logger.warning("[narr-change] 文案变→媒体失效 %d 镜: %s", len(_st), _st)
    path = shots_mod.save(doc)
    logger.info("[extend] +%d 镜 → %s (%s)", len(new_shots), path, shots_mod.summary(doc))
    return path
