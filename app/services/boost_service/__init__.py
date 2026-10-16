# -*- coding: utf-8 -*-
"""爆品改造服务 (Boost Service) — 洗稿后自动优化口播稿的流量指标.

流水线 (2026-08-14, 用户拍板): P2→P3→P4→P1→P5
  P2→P3→P4→P1→P5 串行 (P1 后移至精修之后, 避免被覆盖).
  boost_titles  = P1 输出的标题候选 (默认用第一个)
保留 script_text 为洗稿原稿 (对比回滚).

失败容错: 单个 Pass 失败跳过, 用已成功部分的拼接; 全部失败回退原稿 (不卡死配音).

模块布局 (2026-09-01 自单文件拆包, 函数体原样搬运零行为变更;
旧 app/services/boost_service.py 已删 — 包 __init__ 即转发层):
  prompts.py      全部 Pass 提示词常量 (纯常量文件)
  llm.py          通用 LLM 通道 (_call/_extract_json, 6+ 模块 lazy import)
  splice.py       稿件拼接/清洗 (clean_boosted_text/_find_end/锚点)
  deconstruct.py  解构层 (伪用户评论, ID-041)
  emotions.py     P5 情绪标注 (span 协议)
  tts_adapt.py    TTS 读法适配 (手改稿数字转读法)
  pipeline.py     run_boost 主编排 + P7 流量评审
"""
from __future__ import annotations

from app.services.boost_service.deconstruct import (
    _format_deconstruct_for_prompt as _format_deconstruct_for_prompt,
)
from app.services.boost_service.deconstruct import (
    deconstruct_article,
    format_pseudo_comments,
)
from app.services.boost_service.emotions import (
    _parse_emotion_annotations as _parse_emotion_annotations,
)
from app.services.boost_service.emotions import (
    annotate_emotions,
)
from app.services.boost_service.llm import _call as _call
from app.services.boost_service.llm import _extract_json as _extract_json
from app.services.boost_service.llm import _resolve_llm_cfg as _resolve_llm_cfg
from app.services.boost_service.pipeline import (
    _FLOW_AUDIT_CACHE as _FLOW_AUDIT_CACHE,
)
from app.services.boost_service.pipeline import (
    audit_flow_metrics,
    run_boost,
)
from app.services.boost_service.prompts import (
    DECONSTRUCT_PROMPT as DECONSTRUCT_PROMPT,
)
from app.services.boost_service.prompts import (
    P1_PROMPT as P1_PROMPT,
)
from app.services.boost_service.prompts import (
    P2_PROMPT as P2_PROMPT,
)
from app.services.boost_service.prompts import (
    P3_PROMPT as P3_PROMPT,
)
from app.services.boost_service.prompts import (
    P4_PROMPT as P4_PROMPT,
)
from app.services.boost_service.prompts import (
    P5_PROMPT as P5_PROMPT,
)
from app.services.boost_service.prompts import (
    P5_SPAN_PROMPT as P5_SPAN_PROMPT,
)
from app.services.boost_service.prompts import (
    P5_SPAN_PROMPT_BOOK as P5_SPAN_PROMPT_BOOK,
)
from app.services.boost_service.prompts import (
    P7_PROMPT as P7_PROMPT,
)
from app.services.boost_service.prompts import (
    P_LOOP_PROMPT as P_LOOP_PROMPT,
)
from app.services.boost_service.prompts import (
    TTS_ADAPT_PROMPT as TTS_ADAPT_PROMPT,
)
from app.services.boost_service.splice import (
    _find_end as _find_end,
)
from app.services.boost_service.splice import (
    _parse_opening as _parse_opening,
)
from app.services.boost_service.splice import (
    _splice_boosted as _splice_boosted,
)
from app.services.boost_service.splice import (
    _strip_p3_head as _strip_p3_head,
)
from app.services.boost_service.splice import (
    clean_boosted_text,
)
from app.services.boost_service.tts_adapt import adapt_tts_readability

__all__ = [
    "run_boost",
    "audit_flow_metrics",
    "adapt_tts_readability",
    "annotate_emotions",
    "deconstruct_article",
    "format_pseudo_comments",
    "clean_boosted_text",
]
