"""Director Agent 2.0 — LLM 工序单阶段 (build prompt → rewrite → parse).

失败路径: job 标记 failed + 环节轨迹 "plan_llm"/"error" + 前端 llm_error 事件。
PlanCancelled 原样上抛 (取消语义不得吞掉)。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.config import get_config
from app.models import DirectorJob, Persona
from app.services.director_events import PlanCancelled, publish as _evt
from app.services.director_parser import parse_llm_plan
from app.services.director_prompt import build_director_prompt
from app.services.director_service._trace import append_trace
from app.services.llm_service import LLMService, get_llm_service

logger = logging.getLogger(__name__)

__all__ = ["_llm_plan_phase"]


def _llm_plan_phase(
    db: Session,
    job: DirectorJob,
    job_id: str | None,
    alignment: dict[str, Any],
    script: Any,
    script_title: str | None,
    material_catalog: dict[str, Any] | None,
    total_duration: float,
    enabled_pipelines: set[str] | None,
    is_cancelled: Callable[[], bool] | None,
) -> Any:
    """Run LLM plan phase, return parsed plan or None (failed path handled)."""
    cfg = get_config()
    llm = get_llm_service()
    # 优先用爆品改造稿 (boosted_text), 让导演按观众实际听到的新稿配画面.
    # 若未改造或改造为空, 回退到原始洗稿稿.
    director_script = (script.boosted_text or script.script_text) if hasattr(script, "boosted_text") else script.script_text
    # 从改造稿提取结构意图 (钩子/预埋/回收/呼吸点/情绪), 解决导演盲盒.
    visual_intent = None
    if hasattr(script, "boosted_text") and script.boosted_text:
        try:
            from app.services.director_prompt._intent import extract_visual_intent, summarize_intent

            visual_intent = extract_visual_intent(
                script.boosted_text,
                alignment["segment_timings"],
                getattr(script, "emotion_annotations", None),
            )
            if visual_intent:
                logger.info("[director] visual_intent: %s", summarize_intent(visual_intent))
        except Exception as exc:
            logger.warning("[director] extract_visual_intent failed: %s", exc)
    # 人物级视觉主题 (2026-08-11): script.host → persona.visual_theme (科技/地缘场景词)
    visual_theme = None
    try:
        if script.host:
            persona = db.query(Persona).filter(Persona.host_id == script.host.id).first()
            if persona and getattr(persona, "visual_theme", None):
                visual_theme = persona.visual_theme
                logger.info("[director] visual_theme: %s", visual_theme)
    except Exception as exc:
        logger.warning("[director] visual_theme resolve failed: %s", exc)
    # 证据图池 (2026-09-05): 注入 prompt 让 LLM 知道素材包有真实截图可规划 —
    # 不注入则规则"无则用 broll_pexels"恒生效, evidence_image 永不被选 (全库仅 1 slot 实锤)
    evidence_pool: list[Any] = []
    try:
        from app.services.evidence_service import collect_evidence_pool

        evidence_pool = collect_evidence_pool(db, script)
        if evidence_pool:
            logger.info(
                "[director] evidence pool: %d 张入 prompt (规划可选 evidence_image)",
                len(evidence_pool),
            )
    except Exception as exc:
        logger.warning("[director] evidence pool collect for prompt failed: %s", exc)
    prompt = build_director_prompt(
        script_text=director_script,
        segment_timings=alignment["segment_timings"],
        material_catalog=material_catalog,
        video_format=job.video_format if job else script.video_format,
        script_title=script_title,
        enabled_pipelines=enabled_pipelines,
        visual_intent=visual_intent,
        visual_theme=visual_theme,
        evidence_pool=evidence_pool,
    )

    try:
        raw_output = llm.rewrite_article(
            raw_text=prompt,
            model="pro",
            stream=False,
            # 护栏 (2026-08-25 v3): 此前不设上限不设格式 — 截断=job报废。
            # max_tokens 32000 实测: v4-pro 对 31KB 规划 prompt 的 reasoning 失控
            # (实测 13.5K~24.4K, 8000/16000 均被吃光 → content 空 → JSONDecodeError
            # char 0, 15:49/16:00 两次 failed 实锤); 32000 下 reasoning 24433 +
            # content 7307 finish=stop 正常完成。pro 为纯 reasoning 模型,
            # enable_thinking=False 无效 (实测被忽略)。
            max_tokens=32000,
            response_format={"type": "json_object"},
        )
        if is_cancelled and is_cancelled():
            raise PlanCancelled
        if not raw_output or not raw_output.strip():
            # 空响应防御 (2026-08-25): reasoning 耗尽预算等场景 content 为空 —
            # 直接给可读错误而非 JSONDecodeError char 0, 排障一眼定位。
            raise RuntimeError(
                "LLM 返回空内容 (reasoning 可能耗尽 max_tokens, 当前 16000) — 请重试")
        plan = parse_llm_plan(
            raw_output,
            alignment["segment_timings"],
            total_duration,
            enabled_pipelines=enabled_pipelines,
        )
        append_trace(
            db, job, "plan_llm", "done",
            f"LLM 工序单完成: {len(plan.slots)} slots (prompt ~{len(prompt)//1024}KB)",
        )
        return plan
    except PlanCancelled:
        raise
    except Exception as exc:
        job.status = "failed"
        job.error_message = f"director agent failed: {type(exc).__name__}: {exc}"
        append_trace(db, job, "plan_llm", "error", f"LLM 规划失败: {str(exc)[:200]}")
        db.commit()
        if job_id:
            _evt(job_id, {"type": "llm_error", "msg": str(exc)[:200]})
        return None
