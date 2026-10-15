"""HF visual workflow: 执行入口 (H 线 hf_chart / hf_title).

文本提取见 hf_extract.py, 图表归一化见 hf_chart.py, 模板选择见 common.py。
签名 ``execute_hf_visual_slot(db, slot, workflow)`` 由 slot_executor 特判调用。
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DirectorSlot, VisualRenderJob
from app.schemas import get_video_format_spec
from app.services.slot_workflows.common import _pick_hf_template
from app.services.slot_workflows.hf_chart import _chart_has_data, _normalize_chart_input
from app.services.slot_workflows.hf_extract import _extract_hf_content
from app.services.template_library import TEMPLATES

logger = logging.getLogger(__name__)

__all__ = [
    "execute_hf_visual_slot",
    "_extract_hf_content",
    "_normalize_chart_input",
    "_merge_brand",
]


def _merge_render_config(input_data: dict, render_config: dict) -> None:
    """Override title / metrics from render_config (LLM 真实数据优先)."""
    for k, v in render_config.items():
        if k == "metrics" and isinstance(v, list):
            cleaned = [{"label": str(i["label"])[:20], "value": str(i["value"])}
                       for i in v if isinstance(i, dict) and "label" in i and "value" in i]
            if cleaned:
                input_data["metrics"] = cleaned
        elif k == "title" and isinstance(v, str) and v.strip():
            input_data["title"] = v.strip()[:16]
        elif k not in ("title", "chart", "data"):
            input_data[k] = v


# 账号拆分 (2026-08-18): 发布账号按赛道分离, HF 卡品牌文字跟随首页赛道
# (Article.track, 同驱动评论层/七层分支), 不再读 persona/host 的旧统一账号名.
#   tech (科技/商业) → 老谭科技观;  geo (地缘/国际) → 老谭世界观
_TRACK_BRAND = {
    "tech": {"brand": "老谭科技观", "stamp": "科技", "tag": "科技·商业"},
    "geo": {"brand": "老谭世界观", "stamp": "时局", "tag": "地缘·国际"},
}


def _merge_brand(input_data: dict, slot: DirectorSlot, db: Session) -> None:
    """注入品牌字段 (brand_name / stamp_name / brand_tag), 跟随赛道自动切换.

    两套发布账号 (老谭科技观/老谭世界观) 的差异仅品牌文字 — 模板本身已参数化
    ({{brand_name}}/{{stamp_name}}/{{brand_tag}}), 无需复制两套模板文件.
    赛道取 ``_slot_track`` (script.article.track, 缺省 tech); render_config 显式 brand_tag 仍可覆盖标语.
    """
    tb = _TRACK_BRAND.get(_slot_track(slot), _TRACK_BRAND["tech"])
    input_data["brand_name"] = tb["brand"]
    input_data["stamp_name"] = tb["stamp"]
    # 标语: 显式 brand_tag 优先, 缺省用赛道标语
    tag = input_data.get("brand_tag")
    input_data["brand_tag"] = str(tag).strip() if tag else tb["tag"]


def _ensure_metrics(input_data: dict) -> None:
    """Fill metrics from chart items, or give an empty placeholder row.

    input schema 强制 metrics 非空(minItems=1);口播无数字时给空占位行,
    模板 layout() 会删除空 .m-row → 视觉上标题卡只有标题+副题,不出现垃圾数字。
    """
    if not input_data.get("metrics") and input_data["chart"].get("items"):
        input_data["metrics"] = [
            {"label": it["label"], "value": str(it["value"]), "emphasis": i == 0}
            for i, it in enumerate(input_data["chart"]["items"][:4])
        ]
    if not input_data.get("metrics"):
        input_data["metrics"] = [{"label": "", "value": ""}]


def execute_hf_visual_slot(db: Session, slot: DirectorSlot, workflow: str) -> str:
    """Render an HF visual (chart / title card / opening text card) for the slot duration."""
    from app.services.visual_render_service import execute_visual_render_job

    # 开场字幕卡 (hf_opening): 走专属模板 + 多行台词, 禁忌警告风
    if workflow == "hf_opening":
        return _execute_hf_opening(db, slot)

    # 引用卡 (hf_quote): 一句话观点 + 出处/人物, 编辑纸墨风
    if workflow == "hf_quote":
        return _execute_hf_quote(db, slot)

    # 身份卡 (hf_identity): 自报家门一句话 + About + 品牌印章 (杂志风 06)
    if workflow == "hf_identity":
        return _execute_hf_identity(db, slot)

    # 收尾互动卡 (hf_follow): 口号大字 + 印章 + Follow 栏 (杂志风 07)
    if workflow == "hf_follow":
        return _execute_hf_follow(db, slot)

    # 片尾来源声明卡 (references 风格) → 专用 hf-source 模板:
    # hf-title 的 subtitle→kicker 受 schema maxLength 32 校验, 来源列表必炸,
    # 降级 hf_chart 后渲染近黑屏 (2026-08-18 产线实测)
    if workflow == "hf_title" and (slot.params_json.get("render_config") or {}).get("style") == "references":
        return _execute_hf_source(db, slot)

    duration = round(slot.end_sec - slot.start_sec, 3)
    render_config = slot.params_json.get("render_config") or {}
    input_data = _extract_hf_content(slot.text_context or "")
    # render_config 里的真实数据(如 title/metrics/chart)优先,覆盖从口播提取的结果
    _merge_render_config(input_data, render_config)

    # chart 数据贯通 (问题2): 归一化器统一 render_config.chart_type/data/label/unit/growth
    # 与口播提取结构为 {type, unit, growth, label, color_scheme, items}, 经 {{chart_json}} 进模板
    input_data["chart"] = _normalize_chart_input(render_config, input_data)

    # 空数据降级 (2026-09-09, "页面上只有一个字母 o" 实锤): 口播全无数字且
    # render_config 无 chart/data 时, hf_chart 模板空兜底造 value:0, bigstat 布局
    # 渲染 470px 巨 "0" (形似字母 o), 且 title/subtitle 全不上屏。此处降级标题卡,
    # 复用已提取的 title/subtitle 让卡有真实内容。判定须在模板/时长选择之前,
    # 保证 duration clamp 与 schema 对齐最终模板。
    if workflow == "hf_chart" and not _chart_has_data(input_data["chart"]):
        logger.info("[hf] slot %s hf_chart 无图表数据, 降级 hf_title", slot.slot_index)
        workflow = "hf_title"

    # 按 video_format 选模板: 横屏→news-magazine-v1-ls, 竖屏/方屏→news-magazine-v1
    template_id = _pick_hf_template(slot.director_job)

    # 编辑纸墨系 v3 (2026-09-04): hf_title/hf_chart 用编辑风模板 (纸感米白+锈红,
    # 版式源 .tmp/style_gallery_h.html); 横竖同用 1920x1080 基础版 (合成 scale_pad 归一)
    if workflow in ("hf_title", "hf_chart"):
        template_id = "hf-title-v3" if workflow == "hf_title" else "hf-chart-v3"

    # 时长夹取跟随模板 duration_sec_range (单点真理): hf-title-v2 上限 10 / hf-chart-v2
    # 上限 12, 老 news-magazine 5-30。溢出部分由 composition _normalize_clip_duration
    # 冻结尾帧补齐到分配时长, 音画仍同步。(2026-08-24: 原固定 [5,30] clamp 撞上
    # hf-title-v2 schema max 10, 14.9s 的 hf_title slot 直接校验炸)
    _lo, _hi = TEMPLATES[template_id]["duration_sec_range"]
    input_data["duration_sec"] = max(_lo, min(_hi, round(duration)))

    # 财经标题卡 v2: kicker 用 subtitle (章节副标) — 空则模板隐藏
    if workflow == "hf_title":
        kicker = input_data.get("subtitle") or input_data.get("kicker") or ""
        input_data["kicker"] = kicker
        # 大小字规范硬闸 (0910 实锤): render_config 缺失时口播自动提取会把
        # 整句口播塞进卡面 (title=句子碎片 / kicker=27字长句) — 大字 2~12 字
        # (3字短卡如「别端着」合法, 0910 实锤勿再误杀)、小字 ≤16 字, 违规直接
        # fail → executor 降级 broll (宁缺毋滥)。
        _t = str(input_data.get("title") or "").strip()
        _k = str(input_data.get("kicker") or "").strip()
        if not (2 <= len(_t) <= 12 and len(_k) <= 16):
            raise RuntimeError(
                f"hf_title 卡面违反大小字规范 (title={_t[:16]!r} {len(_t)}字 / "
                f"kicker={_k[:16]!r} {len(_k)}字) — 降级 broll")

    # 品牌字段 (brand_name/stamp_name/brand_tag): 从 script → persona → host 闭环注入,
    # 模板共享, 不再硬编码"老陈聊财经"等账号名 (2026-08-08)
    _merge_brand(input_data, slot, db)
    _ensure_metrics(input_data)
    job = VisualRenderJob(template_id=template_id, input_json=input_data, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)

    result = execute_visual_render_job(db, job.id, template_id, input_data)
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error_message") or "HF render failed")
    out_path = result.get("output_path")
    if not out_path or not Path(out_path).exists():
        raise RuntimeError("HF render output missing")
    return out_path


def _execute_hf_opening(db: Session, slot: DirectorSlot) -> str:
    """开场字幕卡 (hf_opening): 前 5 秒多行台词贴字兜听觉.

    - style: v1 = 禁忌警告风 (黑底白字+警示红, 顿挫硬切), v2 = Apple 官网审美 (浅灰+深蓝强调, 细腻缓入),
             v3 = 财经片头 (深炭+暖金 chrome-text, 多层时间轴+scatter, 大厂 UI 标准)
    - 模板: hf-opening-v{1|2|3} (竖) / hf-opening-v{1|2}-ls (横, v3 当前仅横屏设计)
    - 时长: 5-8 秒 (前 5 秒核心, 最多 8 秒)
    - 内容: v1/v2=多行台词+强调词; v3=hero_text(主句)+hot_word(金词)+sub_text(副句)+scatter_words(散布词)
    """
    from app.services.visual_render_service import execute_visual_render_job

    spec = get_video_format_spec(slot.director_job.video_format)
    render_config = slot.params_json.get("render_config") or {}
    is_landscape = spec["width"] > spec["height"]
    # 财经片头 v3 统一默认 (2026-08-18: v1 禁忌警告风为遗留, 账号统一财经体系);
    # v3 当前仅横屏设计 → 竖屏回退 v1
    style = render_config.get("style") or ("v3" if is_landscape else "v1")
    if style not in ("v1", "v2", "v3"):
        style = "v3" if is_landscape else "v1"
    # style 已是 "v1/v2/v3" (含 v), 直接拼; 横屏 v1/v2 用 -ls 模板, v3 当前仅横屏设计用基础模板
    template_id = f"hf-opening-{style}-ls" if is_landscape and style != "v3" else f"hf-opening-{style}"
    duration = round(slot.end_sec - slot.start_sec, 3)

    # v3 财经片头: hero/hot/sub/scatter 参数
    if style == "v3":
        input_data = {
            "hero_text": str(render_config.get("hero") or render_config.get("hero_text") or "")[:16],
            "hot_word": str(render_config.get("hot") or render_config.get("hot_word") or ""),
            "sub_text": str(render_config.get("sub") or render_config.get("sub_text") or ""),
            "scatter_words": json.dumps(render_config.get("scatter") or render_config.get("scatter_words") or [], ensure_ascii=False),
            "duration_sec": max(5, min(8, round(duration))),
        }
        # 无显式 hero 时从口播取: 语义分行首行做 hero (108px 字号 ≤14 字防溢出),
        # 剩余行做 sub_text (2026-08-18 修复: 原 [:20] 整句溢出裁切)
        if not input_data["hero_text"]:
            from app.services.slot_workflows.hf_extract import build_opening_lines

            built = build_opening_lines(slot.text_context or "", max_chars=14)
            lines = built.get("lines", [])
            input_data["hero_text"] = lines[0] if lines else (slot.text_context or "")[:14]
            if not input_data["sub_text"] and len(lines) > 1:
                input_data["sub_text"] = "".join(lines[1:])[:32]
            if not input_data["hot_word"]:
                reds = built.get("red_words") or []
                input_data["hot_word"] = reds[0] if reds else ""
        # 无 hot 词时从口播检测冲击词
        if not input_data["hot_word"]:
            try:
                from app.services.slot_workflows.hf_extract import _pick_red_words
                words = _pick_red_words(input_data["hero_text"])
                if words:
                    input_data["hot_word"] = words[0]
            except Exception:
                pass
        # 无 scatter 时给默认财经词
        if not input_data["scatter_words"].strip() or input_data["scatter_words"] == "[]":
            input_data["scatter_words"] = json.dumps(["资本", "黑箱", "博弈", "杠杆", "泡沫", "算盘", "命门", "逻辑"], ensure_ascii=False)
        _merge_brand(input_data, slot, db)
        job = VisualRenderJob(template_id=template_id, input_json=input_data, status="queued")
        db.add(job); db.commit(); db.refresh(job)
        result = execute_visual_render_job(db, job.id, template_id, input_data)
        if result.get("status") != "completed":
            raise RuntimeError(result.get("error_message") or "HF render failed")
        out_path = result.get("output_path")
        if not out_path or not Path(out_path).exists():
            raise RuntimeError("HF render output missing")
        return out_path

    # v1/v2: 多行台词逻辑
    accent_key = "opening_red_words" if style == "v1" else "opening_accent_words"

    duration = round(slot.end_sec - slot.start_sec, 3)

    # 多行台词: 优先用 render_config 显式给的, 否则从口播提取
    lines = render_config.get("lines") or render_config.get("opening_lines") or []
    accent_words = render_config.get("red_words") or render_config.get("accent_words") or []
    if not lines:
        # fallback: 从开场口播 (slot.text_context) 语义分行提取
        try:
            from app.services.slot_workflows.hf_extract import build_opening_lines

            built = build_opening_lines(slot.text_context or "")
            lines = built.get("lines", [])
            if not accent_words:
                accent_words = built.get("red_words", [])
        except Exception:
            lines = []

    input_data = {
        "opening_lines_json": json.dumps(lines, ensure_ascii=False),
        accent_key: json.dumps(accent_words, ensure_ascii=False),
        "duration_sec": max(5, min(8, round(duration))),
    }
    # 品牌注入
    _merge_brand(input_data, slot, db)

    job = VisualRenderJob(template_id=template_id, input_json=input_data, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)

    result = execute_visual_render_job(db, job.id, template_id, input_data)
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error_message") or "HF render failed")
    out_path = result.get("output_path")
    if not out_path or not Path(out_path).exists():
        raise RuntimeError("HF render output missing")
    return out_path


_QUOTE_MAX_CHARS = 35  # 风格页 05-C 档位上限 (2026-09-05 用户裁决: 引用卡单句 ≤35字)
_QUOTE_NORM_STRIP = re.compile(r"[\s，。、；：,.;:!?！？·…—\-\"'“”‘’()（）\[\]【】|]")
_QUOTE_SENT_END = re.compile(r"[。！？!?；;]")


def _norm_quote(s: str) -> str:
    """逐字比对口径: 去空白 + 全部标点 (LLM 半角/全角标点差异不判错)."""
    return _QUOTE_NORM_STRIP.sub("", s or "")


def _visible_len(s: str) -> int:
    """模板字数口径: 去空白、标点计入 (hf_quote_v2 JS 同款 count)."""
    return len(re.sub(r"\s", "", s or ""))


def _cap_to_35(text: str, tag: str) -> str:
    """>35字 → 截到 ≤35 的最长句边界; 单句即超 → RuntimeError (调用方降级).
    字数口径与模板 JS 一致 (去空白、标点计入)。"""
    if _visible_len(text) <= _QUOTE_MAX_CHARS:
        return text
    cut = ""
    for m in _QUOTE_SENT_END.finditer(text):
        cand = text[: m.end()]
        if _visible_len(cand) <= _QUOTE_MAX_CHARS:
            cut = cand
    if not cut:
        raise RuntimeError(
            f"{tag}: 原句 {_visible_len(text)}字 >{_QUOTE_MAX_CHARS} 且无句边界可截, 降级"
        )
    logger.info("[hf] %s %d字 超上限, 截到句边界: %s", tag, _visible_len(text), cut[:40])
    return cut


def _gate_quote_text(quote: str, text_context: str) -> str:
    """引用语逐字闸门 (2026-09-05): quote 必须逐字抄自口播原文且 ≤35字.

    ① 逐字校验: 去标点空白后是 slot 引用句的子串 → 放行;
    ② 强制校正: LLM 改写/截字 → 用原文中最相似句覆盖 (不信 LLM 抄写);
    ③ 上限: >35字 截到 ≤35 的最长句边界; 单句即超 → RuntimeError 降级
       (fallback 链), 不出 4 行溢行卡。
    """
    raw = text_context or ""
    norm_q, norm_t = _norm_quote(quote), _norm_quote(raw)

    if norm_q and norm_q in norm_t:
        final = quote.strip()
    else:
        sents = [s.strip() for s in re.split(r"(?<=[。！？!?；;])", raw) if s.strip()]
        best, best_r = None, -1.0
        for s in sents:
            r = difflib.SequenceMatcher(None, norm_q, _norm_quote(s)).ratio()
            if r > best_r:
                best, best_r = s, r
        if best is None:
            raise RuntimeError("hf_quote 引用闸门: slot 无口播句可引用, 降级")
        logger.info("[hf_quote] quote 逐字校验不过 (相似度 %.2f), 原句覆盖: %s",
                    best_r, best[:40])
        final = best

    return _cap_to_35(final, "hf_quote 引用闸门")


def _slot_track(slot: DirectorSlot) -> str:
    """slot → 发布赛道 (script.article.track, 缺省 tech)。"""
    script = slot.director_job.script if slot.director_job else None
    return (script.article.track if script and script.article else None) or "tech"


def _require_tech_track(slot: DirectorSlot, workflow: str) -> None:
    """身份/互动卡为财经线 (tech) 专用 — 地缘线 (geo) 另做一套 (2026-09-05 用户裁决).
    geo 误规划 → RuntimeError → slot_executor 走 fallback 链降级。"""
    track = _slot_track(slot)
    if track != "tech":
        raise RuntimeError(f"{workflow} 仅财经线可用 (当前 track={track}), 降级")


def _execute_hf_quote(db: Session, slot: DirectorSlot) -> str:
    """引用卡 (hf_quote): 一句话观点 + 出处/人物 + 可选人像, 编辑纸墨风.

    - 模板: hf-quote-v2 (横屏 1920x1080, 版式源 gallery 05 金句卡)
    - 内容: quote_text(引用语, 兼容旧键) + quote_body(保留标点原串, 模板按标点断行)
            + hot_word(金词→锈红) + attrib_name(人物) + attrib_role(身份) + portrait_b64(人像)
    - 闸门: _gate_quote_text 逐字校验 + 35字上限 (2026-09-05, 05-A/B/C 档位守门)
    """
    from app.services.visual_render_service import execute_visual_render_job

    template_id = "hf-quote-v2"
    duration = round(slot.end_sec - slot.start_sec, 3)
    render_config = slot.params_json.get("render_config") or {}

    raw_ctx = (slot.text_context or "").replace("||", "")
    input_data = {
        "quote_text": str(render_config.get("quote") or render_config.get("quote_text") or ""),
        "hot_word": str(render_config.get("hot") or render_config.get("hot_word") or ""),
        "attrib_name": str(render_config.get("name") or render_config.get("attrib_name") or ""),
        "attrib_role": str(render_config.get("role") or render_config.get("attrib_role") or ""),
        "portrait_b64": str(render_config.get("portrait") or render_config.get("portrait_b64") or ""),
        "duration_sec": max(4, min(10, round(duration))),
    }
    # 无 quote 时从口播取 (原 [:80] 硬截撤除, 统一走闸门)
    if not input_data["quote_text"]:
        input_data["quote_text"] = raw_ctx
    # 逐字闸门: 抄写必须逐字等于口播原文 + ≤35字 (校正/截句/降级三态)
    input_data["quote_text"] = _gate_quote_text(input_data["quote_text"], raw_ctx)
    # quote_body: 保留标点原串 (金句断行/节奏靠标点; quote_text 走净标点仅作 schema 必填)
    input_data["quote_body"] = input_data["quote_text"]
    # 无 hot 词、或校正后 hot 词已不在引用语内 → 从最终引用语重捡冲击词
    if not input_data["hot_word"] or input_data["hot_word"] not in input_data["quote_text"]:
        input_data["hot_word"] = ""
        try:
            from app.services.slot_workflows.hf_extract import _pick_red_words
            words = _pick_red_words(input_data["quote_text"])
            if words:
                input_data["hot_word"] = words[0]
        except Exception:
            pass
    _merge_brand(input_data, slot, db)

    job = VisualRenderJob(template_id=template_id, input_json=input_data, status="queued")
    db.add(job); db.commit(); db.refresh(job)
    result = execute_visual_render_job(db, job.id, template_id, input_data)
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error_message") or "HF render failed")
    out_path = result.get("output_path")
    if not out_path or not Path(out_path).exists():
        raise RuntimeError("HF render output missing")
    return out_path


# 身份卡品牌默认句 (财经线老谭科技观; 口播无自介句时的兜底)
_DEFAULT_TECH_IDENTITY = "我是老谭，专盯 AI 圈的一举一动，从硅谷到海淀。"


def _execute_hf_identity(db: Session, slot: DirectorSlot) -> str:
    """身份卡 (hf_identity): 自报家门一句话 + About 栏 + 品牌印章.

    - 模板: hf-identity-v1 (横屏 1920x1080, 版式源杂志风预览页面 06 身份句)
    - 内容: identity_text(自介句 — 有口播上下文时逐字闸门同 hf_quote;
            无则品牌默认句) + hot_word + 品牌印章(brand_name 五字)
    - 财经线 (tech) 专用 (2026-09-05 用户裁决: 地缘线另做一套)
    """
    from app.services.visual_render_service import execute_visual_render_job

    _require_tech_track(slot, "hf_identity")

    template_id = "hf-identity-v1"
    duration = round(slot.end_sec - slot.start_sec, 3)
    render_config = slot.params_json.get("render_config") or {}

    raw_ctx = (slot.text_context or "").replace("||", "")
    identity = str(render_config.get("identity")
                   or render_config.get("identity_text") or "").strip()
    if identity and raw_ctx:
        # 逐字闸门同 hf_quote: 自介句必须逐字抄口播 (改写→原句覆盖, 超长→截句/降级)
        identity = _gate_quote_text(identity, raw_ctx)
    else:
        identity = _cap_to_35(identity or raw_ctx or _DEFAULT_TECH_IDENTITY,
                              "hf_identity 身份闸门")

    input_data = {
        "identity_text": identity,
        "hot_word": str(render_config.get("hot") or render_config.get("hot_word") or ""),
        "duration_sec": max(4, min(10, round(duration))),
    }
    input_data["identity_body"] = identity  # 保留标点原串, 模板按标点断行
    if not input_data["hot_word"] or input_data["hot_word"] not in identity:
        input_data["hot_word"] = ""
        try:
            from app.services.slot_workflows.hf_extract import _pick_red_words
            words = _pick_red_words(identity)
            if words:
                input_data["hot_word"] = words[0]
        except Exception:
            pass
    _merge_brand(input_data, slot, db)

    job = VisualRenderJob(template_id=template_id, input_json=input_data, status="queued")
    db.add(job); db.commit(); db.refresh(job)
    result = execute_visual_render_job(db, job.id, template_id, input_data)
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error_message") or "HF render failed")
    out_path = result.get("output_path")
    if not out_path or not Path(out_path).exists():
        raise RuntimeError("HF render output missing")
    return out_path


def _execute_hf_follow(db: Session, slot: DirectorSlot) -> str:
    """收尾互动卡 (hf_follow): 口号大字两行(末子句锈红) + 品牌印章 + Follow 栏.

    - 模板: hf-follow-v1 (横屏 1920x1080, 版式源杂志风预览页面 07 收尾互动卡)
    - 内容: slogan(缺省账号口号「听懂逻辑，少走弯路。」) + follow_word(Follow) +
            品牌印章/刊名底栏 (_merge_brand) — 全品牌层固定件
    - 财经线 (tech) 专用; 收尾口号句由它承载 (与 hf_quote 收尾金句分工:
      口号句→follow 卡, 非口号点题句→quote 卡)
    """
    from app.services.visual_render_service import execute_visual_render_job

    _require_tech_track(slot, "hf_follow")

    template_id = "hf-follow-v1"
    duration = round(slot.end_sec - slot.start_sec, 3)
    render_config = slot.params_json.get("render_config") or {}

    slogan = str(render_config.get("slogan") or "").strip() or "听懂逻辑，少走弯路。"
    input_data = {
        # schema maxLength 40 → 硬防护截断 (口号层常态远短于此)
        "slogan": slogan[:40],
        "follow_word": str(render_config.get("follow")
                           or render_config.get("follow_word") or "").strip() or "Follow",
        "duration_sec": max(3, min(10, round(duration))),
    }
    _merge_brand(input_data, slot, db)

    job = VisualRenderJob(template_id=template_id, input_json=input_data, status="queued")
    db.add(job); db.commit(); db.refresh(job)
    result = execute_visual_render_job(db, job.id, template_id, input_data)
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error_message") or "HF render failed")
    out_path = result.get("output_path")
    if not out_path or not Path(out_path).exists():
        raise RuntimeError("HF render output missing")
    return out_path


def _execute_hf_source(db: Session, slot: DirectorSlot) -> str:
    """片尾来源声明卡 (hf-source-v2): 结构化来源列表 + 免责尾注, 编辑纸墨风.

    - 模板: hf-source-v2 (横屏 1920x1080, 版式源 gallery 08 来源卡)
    - 内容: title(卡题) + sources([{media, title}]≤8, 来自 render_config) +
            disclaimer(免责尾注) + 品牌角标
    """
    from app.services.visual_render_service import execute_visual_render_job

    template_id = "hf-source-v2"
    duration = round(slot.end_sec - slot.start_sec, 3)
    render_config = slot.params_json.get("render_config") or {}

    sources = render_config.get("sources") or []
    input_data = {
        "title": str(render_config.get("title") or "内容来源声明"),
        "sources": [
            {"media": str(s.get("media") or "")[:40], "title": str(s.get("title") or "")[:60]}
            for s in sources[:8] if isinstance(s, dict)
        ],
        "disclaimer": str(render_config.get("disclaimer")
                          or "内容综合自公开报道 仅供参考 不构成投资建议"),
        "duration_sec": max(4, min(10, round(duration))),
    }
    _merge_brand(input_data, slot, db)

    job = VisualRenderJob(template_id=template_id, input_json=input_data, status="queued")
    db.add(job); db.commit(); db.refresh(job)
    result = execute_visual_render_job(db, job.id, template_id, input_data)
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error_message") or "HF render failed")
    out_path = result.get("output_path")
    if not out_path or not Path(out_path).exists():
        raise RuntimeError("HF render output missing")
    return out_path
