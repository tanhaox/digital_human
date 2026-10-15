"""Template filler — copy template source files into a per-job workspace,
then perform safe ``{{KEY}}`` placeholder substitution inside ``index.html``.

Placeholder rules:
- Only whitelisted keys are substituted (no LLM-style injection).
- Unknown ``{{...}}`` tokens are left untouched (HF template is authoritative).
- ``avatar.b64`` is copied verbatim (template controls its own asset name).

The workspace is ``<hf_visual_root>/<job_id>/``.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from app.config import get_config


# Whitelist of placeholders the orchestrator will substitute.  Anything outside
# this set is left as-is to keep the template author in full control.
_SAFE_KEYS = frozenset(
    {
        "title",
        "subtitle",
        "kicker",
        "metric_label_1",
        "metric_value_1",
        "metric_label_2",
        "metric_value_2",
        "metric_label_3",
        "metric_value_3",
        "metric_label_4",
        "metric_value_4",
        "chart_label_1",
        "chart_value_1",
        "chart_label_2",
        "chart_value_2",
        "chart_label_3",
        "chart_value_3",
        "chart_label_4",
        "chart_value_4",
        "chart_label_5",
        "chart_value_5",
        "chart_json",
        # 开场字幕卡 (hf_opening, 2026-08-11): 多行台词 + 关键冲击词
        "opening_lines_json",
        "opening_red_words",
        "opening_accent_words",
        # 财经片头 (hf_opening v3, 2026-08-11): hero/热词/副句/scatter
        "hero_text",
        "hot_word",
        "sub_text",
        "scatter_words",
        # 引用卡 (hf_quote, 2026-08-11): 一句话观点 + 出处/人物 + 人像
        "quote_text",
        "quote_body",
        "attrib_name",
        "attrib_role",
        "portrait_b64",
        "chart_head",
        "source",
        # 片尾来源声明卡 (hf-source-v1, 2026-08-18): 结构化来源列表 JSON + 免责尾注
        "sources_json",
        "disclaimer",
        "duration_sec",
        # 片头卡刊号日期 (hf-title-v3): 渲染沙箱 Date 冻结为 epoch, 由填充端注入
        "issue_date",
        # 品牌字段 (共享模板逐人设注入): 账号名/印章/标语 (2026-08-08)
        "brand_name",
        "stamp_name",
        "brand_tag",
        # 身份卡 (hf-identity-v1, 2026-09-05): 自介句上屏 + 热词
        "identity_text",
        "identity_body",
        # 收尾互动卡 (hf-follow-v1, 2026-09-05): 口号两句 + FOLLOW
        "slogan",
        "follow_word",
        # 老谭读书 bs1 模板族 (2026-09-08, 13 页型; 两线不互通—静读书仍纸墨系)
        "foot", "ep_tag", "book", "items", "no", "subtitle", "left", "right",
        "body", "story", "moral", "stats", "bars", "question", "next", "note",
        "img", "img_cap", "quote", "quote_src",
        # 拆书线尾卡双卡 (2026-09-05): 书籍卡 + 书摘金句卡
        "book_title",
        "author",
        "guide",
        "bio",
        "q1", "q2", "q3",
        "k1", "k2", "k3",
        "logo_b64",
    }
)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# HF 上屏文字净标点 (2026-08-18 用户口径: 只允许回车, 不允许标点)。
# 仅去句读/括号/引号/破折号; 保留 数字/字母/%/·/./- (域名与数据不被破坏)。
_PUNCT_STRIP = "，,。．！!？?；;：:、（）()【】[]《》<>〈〉「」『』‘’“”\"'…—–"
_PUNCT_TABLE = str.maketrans({ch: None for ch in _PUNCT_STRIP})
_JSON_SUBS = {"opening_lines_json", "sources_json", "scatter_words", "chart_json",
              "opening_red_words", "opening_accent_words"}
# 含 HTML 敏感内容或注入属性/文本节点的键: 净标点后仍需转义
_ESCAPE_TEXT = {"quote_text", "attrib_name", "attrib_role", "hero_text",
                "hot_word", "sub_text", "disclaimer", "source"}


def _strip_punct(s: str) -> str:
    return s.translate(_PUNCT_TABLE)


def _strip_obj(o):
    """递归净标点 JSON 叶子字符串 (域名里的 . 不在剥离集, 不受影响)."""
    if isinstance(o, str):
        return _strip_punct(o)
    if isinstance(o, dict):
        return {k: _strip_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_strip_obj(v) for v in o]
    return o


def _build_substitutions(input_data: dict) -> dict[str, str]:
    """Build a ``{placeholder: value}`` dict from the validated input.

    末尾统一净标点 + 转义 (2026-08-18): HF 上屏文字只允许回车不允许标点;
    JSON 类键先递归净叶子再转义; 含 HTML 敏感内容的文本键净标点后转义。
    """
    subs: dict[str, str] = {}
    subs["title"] = str(input_data.get("title", ""))
    subs["subtitle"] = str(input_data.get("subtitle", ""))
    subs["kicker"] = str(input_data.get("kicker", ""))
    metrics = input_data.get("metrics") or []
    for i in range(4):
        if i < len(metrics):
            m = metrics[i]
            subs[f"metric_label_{i+1}"] = str(m.get("label", ""))
            subs[f"metric_value_{i+1}"] = str(m.get("value", ""))
    chart = input_data.get("chart") or {}
    chart_items = chart.get("items") or []
    for i in range(5):
        if i < len(chart_items):
            c = chart_items[i]
            subs[f"chart_label_{i+1}"] = str(c.get("label", ""))
            subs[f"chart_value_{i+1}"] = str(c.get("value", ""))
    # 完整 chart 结构经 JSON 内联给模板 layout() 三模式自适应渲染 (2026-08-01)
    subs["chart_json"] = json.dumps(chart, ensure_ascii=False, separators=(",", ":"))
    # 开场字幕卡 (hf_opening, 2026-08-11): 多行台词 + 强调词
    subs["opening_lines_json"] = str(input_data.get("opening_lines_json", "[]"))
    subs["opening_red_words"] = str(input_data.get("opening_red_words", "[]"))
    subs["opening_accent_words"] = str(input_data.get("opening_accent_words", "[]"))
    # 财经片头 v3 参数
    subs["hero_text"] = str(input_data.get("hero_text", ""))
    subs["hot_word"] = str(input_data.get("hot_word", ""))
    subs["sub_text"] = str(input_data.get("sub_text", ""))
    subs["scatter_words"] = str(input_data.get("scatter_words", "[]"))
    # 引用卡参数
    subs["quote_text"] = str(input_data.get("quote_text", ""))
    subs["quote_body"] = str(input_data.get("quote_body", ""))
    subs["attrib_name"] = str(input_data.get("attrib_name", ""))
    subs["attrib_role"] = str(input_data.get("attrib_role", ""))
    subs["portrait_b64"] = str(input_data.get("portrait_b64", ""))
    subs["chart_head"] = str(chart.get("label") or chart.get("type") or "")
    subs["source"] = str(input_data.get("source", ""))
    # 来源声明卡: 结构化来源列表 → JSON 内联属性 (hf-source-v2 容量 8)
    subs["sources_json"] = json.dumps(
        (input_data.get("sources") or [])[:8], ensure_ascii=False, separators=(",", ":")
    )
    subs["disclaimer"] = str(input_data.get("disclaimer", ""))
    subs["duration_sec"] = str(input_data.get("duration_sec", "12"))
    # 刊号日期 (hf-title-v3): HF 渲染沙箱 Date 冻结为 epoch (实测 1970.01),
    # 帧内取不到真实时钟 — 由填充端注入, 缺省取本机当月 (YYYY.MM)
    subs["issue_date"] = str(input_data.get("issue_date") or datetime.now().strftime("%Y.%m"))
    # 品牌字段: 共享模板不硬编码账号名, 由 input_data 注入 (2026-08-08)
    subs["brand_name"] = str(input_data.get("brand_name", ""))
    subs["stamp_name"] = str(input_data.get("stamp_name", ""))
    subs["brand_tag"] = str(input_data.get("brand_tag", ""))
    # 身份卡/收尾互动卡 (2026-09-05): 漏挂白名单 → 成片渲染出原始 {{identity_body}}
    # / {{slogan}} / {{FOLLOW_WORD}} 占位符 (job bdb6674b 剪映草稿实锤)
    subs["identity_text"] = str(input_data.get("identity_text", ""))
    subs["identity_body"] = str(input_data.get("identity_body", ""))
    subs["slogan"] = str(input_data.get("slogan", ""))
    subs["follow_word"] = str(input_data.get("follow_word", ""))
    # 拆书线尾卡 (2026-09-05): 书籍卡 + 书摘金句卡
    subs["book_title"] = str(input_data.get("book_title", ""))
    subs["author"] = str(input_data.get("author", ""))
    subs["guide"] = str(input_data.get("guide", ""))
    subs["bio"] = str(input_data.get("bio", ""))
    for i in (1, 2, 3):
        subs[f"q{i}"] = str(input_data.get(f"q{i}", ""))
        subs[f"k{i}"] = str(input_data.get(f"k{i}", ""))
    subs["logo_b64"] = str(input_data.get("logo_b64", ""))
    # bs1 族 (0908): 全键直取 input_data; img/img_cap 与 logo_b64 同类 (data URI 含 :;/+
    # 会被净标点剥坏), 文本键归 escape-only 组 (杂志编辑风保留标点/分隔符/换行)
    for _k in ("foot", "ep_tag", "book", "items", "no", "subtitle", "left", "right",
               "body", "story", "moral", "stats", "bars", "question", "next", "note",
               "img", "img_cap", "quote", "quote_src"):
        subs[_k] = str(input_data.get(_k, ""))

    # 净标点 + 转义收口
    for k in list(subs):
        v = subs[k]
        if k == "duration_sec" or k == "portrait_b64" or k == "logo_b64" or k == "img":
            continue
        if k in ("quote_body", "identity_body", "slogan",
                 "author", "guide", "bio", "q1", "q2", "q3",
                 "k1", "k2", "k3",
                 # bs1 族 (0908): 标点/分隔符(| |||)/换行/书名号都是版式语义, 仅防注入
                 "foot", "ep_tag", "book", "items", "no", "subtitle", "left", "right",
                 "body", "story", "moral", "stats", "bars", "question", "next", "note",
                 "img_cap", "quote", "quote_src"):
            # 金句/自介/口号/书卡文本原串保留标点 (2026-09-04~05 编辑风:
            # 断行/书名号/引号都是版式的一部分), 仅 HTML 转义防注入
            subs[k] = _html_escape(v)
        elif k in _JSON_SUBS:
            try:
                obj = json.loads(v)
            except Exception:
                obj = None
            if obj is not None:
                v = json.dumps(_strip_obj(obj), ensure_ascii=False, separators=(",", ":"))
            subs[k] = _html_escape(v)
        elif k in _ESCAPE_TEXT:
            subs[k] = _html_escape(_strip_punct(v))
        else:
            subs[k] = _strip_punct(v)
    return subs


def _html_escape(text: str) -> str:
    """转义 HTML 特殊字符, 使 chart JSON 能安全内联到 <script> 节点内 (2026-08-01)."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _replace_placeholders(html: str, subs: dict[str, str]) -> str:
    def _sub(match: re.Match) -> str:
        key = match.group(1)
        if key in _SAFE_KEYS:
            return subs.get(key, "")
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, html)


def fill_template(
    template_dir: Path,
    template_id: str,
    input_data: dict,
    job_id: str,
) -> Path:
    """Copy the template files into the per-job workspace and substitute placeholders.

    Returns the workspace path.
    """
    cfg = get_config().defaults
    workspace = Path(cfg.hf_visual_root) / job_id
    workspace.mkdir(parents=True, exist_ok=True)

    template_dir = Path(template_dir)

    # 1. Copy template source files (whitelist: only index.html + avatar.b64).
    # Anything else in the template source directory is treated as non-essential
    # and skipped — prevents stale demo artefacts (e.g. test_news_b64.mp4) from
    # being dragged into the job workspace.
    _TEMPLATE_FILE_WHITELIST = {"index.html", "avatar.b64"}
    for name in _TEMPLATE_FILE_WHITELIST:
        src = template_dir / name
        if src.is_file():
            shutil.copy2(src, workspace / name)

    # 2. Write input.json — authoritative record of what was rendered
    (workspace / "input.json").write_text(
        json.dumps(input_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 3. Substitute placeholders inside index.html (only whitelisted keys)
    html_path = workspace / "index.html"
    html = html_path.read_text(encoding="utf-8")
    subs = _build_substitutions(input_data)
    html = _replace_placeholders(html, subs)
    html_path.write_text(html, encoding="utf-8")

    return workspace