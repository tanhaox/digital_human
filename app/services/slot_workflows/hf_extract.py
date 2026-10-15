"""HF 口播文本提取: 标题/副题/metrics/chart 从分句中抽取.

对应 H 线 (hf_chart / hf_title) 的文本侧; 图表归一化见 hf_chart.py。
"""
from __future__ import annotations

import re

__all__ = [
    "_extract_hf_content",
    "_clean_fragment",
    "_pick_title",
    "_extract_numbers",
    "_score_metrics",
]

# 情绪标签 (用于从分句中剥离 [情绪] 标记)
_EMOTION = r"(?:情绪|serious|calm|happy|sad|angry|opening|rising|climax|falling|closing)"
# 开场白/口头禅前缀 (host 段文本误喂给标题卡时, 不让"大家好"上屏)
_OPENING_PREFIX = r"^(?:大家好|各位朋友|各位观众|哈喽|你好|hello|我是老陈|老陈)[，,。\.、]?"


def _clean_fragment(s: str) -> str:
    """Strip [情绪] markers and whitespace from a text fragment."""
    s = re.sub(rf"\[{_EMOTION}\]", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def _pick_title(chunks: list[str], title_max: int) -> str:
    """Pick a title from chunks: first, or first non-prefix later chunk."""
    title = _clean_fragment(chunks[0])
    title = re.sub(_OPENING_PREFIX, "", title)
    # 若首句只剩开场白(去前缀后为空), 顺延用后续分句做标题
    if not title:
        for nxt in chunks[1:]:
            cand = _clean_fragment(nxt)
            cand = re.sub(_OPENING_PREFIX, "", cand)
            if cand:
                title = cand
                break
    # 截断: 优先停在完整分句(最后一个不超上限的标点), 避免"叫生存空"式残句
    if len(title) > title_max:
        last = 0
        for m in re.finditer(r"[，。！？,\.!?]", title):
            if m.end() <= title_max:
                last = m.end()
            else:
                break
        title = title[:last] if last else title[:title_max]
    return title


def _extract_numbers(chunks: list[str]) -> tuple[list[dict], list[dict]]:
    """Extract (metrics, chart_items) across all text chunks.

    metric: {label, value, priority}; chart item: {label, value(float)}.
    Only %-valued numbers become chart items; all numbers become metrics.
    label is the chunk prefix (up to 20 chars); metrics dedup by label+value.
    """
    metrics: list[dict] = []
    chart_items: list[dict] = []
    seen: set[str] = set()
    for c in chunks:
        nums = re.findall(r"(-?\d+(?:\.\d+)?)\s*(%|万亿|亿|万|千|元|倍|个|点|岁)?", c)
        for val, unit in nums:
            label = c[:20]
            value_text = f"{val}{unit}".strip()
            if unit == "%":
                chart_items.append({"label": label, "value": float(val)})
            dedup = f"{label}|{value_text}"
            if dedup not in seen:
                seen.add(dedup)
                metrics.append({"label": label, "value": value_text, "priority": 1 if unit == "%" else 0})
            if len(metrics) >= 4 and len(chart_items) >= 5:
                return metrics, chart_items
        if len(metrics) >= 4 and len(chart_items) >= 5:
            break
    return metrics, chart_items


def _score_metrics(metrics: list[dict]) -> list[dict]:
    """Sort by priority (%-values first) and set emphasis on the %-leader."""
    metrics.sort(key=lambda m: -m.pop("priority", 0))
    for m in metrics:
        m.setdefault("emphasis", False)
    if metrics and metrics[0].get("value") and "%" in metrics[0]["value"]:
        metrics[0]["emphasis"] = True
    return metrics


def _extract_hf_content(text: str, title_max: int = 16) -> dict:
    """从口播文本提取标题卡内容 (2026-08-01, 修复黑底白字标题卡根因).

    入口先剥拼音标注 (2026-08-25): <行|HANG2> 只服务 TTS, 渲染进 HF 卡即污染.

    HF 标题卡模板 (news-magazine-v1) 有大标题/副题/metrics/chart 设计,但此前
    把整句口播(含 || 停顿符)硬塞进 title,metrics/chart 用口播占位垃圾填充,
    导致渲染结果=黑底一行白字。本函数:
      - title: 只取首个分句,压到 title_max 字内(模板 108px 标题,过长会溢出);
      - subtitle: 取第 2 个分句(有内容时),替代空副题;
      - metrics: 抽取分句中的真实数字(优先),抽不到则跳过(模板 layout 会删空行);
                带 % 的数值也进 metrics(2026-08-01 问题1修复: 无图表数据时也有文字展示);
      - chart: 带 % 的数值构造图表 items,与 metrics 互补(文字+图表双轨).

    返回 dict: {title, subtitle, metrics, chart} 全部为模板友好结构。
    """
    from app.services.pinyin_fix import strip_pinyin_marks

    text = strip_pinyin_marks((text or "").strip())
    chunks = [c.strip() for c in re.split(r"\|\||\n", text) if c.strip()]
    if not chunks:
        return {"title": "数据展示", "subtitle": "", "metrics": [], "chart": {"type": "bar", "items": []}}

    # 大小字分层 (2026-08-25): 大字=定性锚点(名词性短语), 小字=张力短语 —
    # 候选全部来自本 slot 口播分句 (语义锚定, 禁外部造句); 无合格锚点回退旧策略。
    title, subtitle = _pick_title_and_sub(chunks, title_max)

    metrics, chart_items = _extract_numbers(chunks)

    return {
        "title": title or "数据展示",
        "subtitle": subtitle,
        "metrics": _score_metrics(metrics)[:4],
        "chart": {"type": "bar", "unit": "", "items": chart_items[:5]},
    }


# 定性锚点判定 (2026-08-25): 名词性短语 4~10 字, 非问句, 无句尾语气词,
# 不以代词/连词开头 — "美军航母困局"✅ / "他们为什么不回港"❌
_VERB_TAIL = re.compile(r"[了吗呢吧啊呀]$")
_PRON_START = ("那", "这", "但", "可", "而", "所", "于", "难道", "他", "她", "它", "你", "我")


def _is_anchor_phrase(s: str) -> bool:
    t = re.sub(_OPENING_PREFIX, "", _clean_fragment(s)).rstrip("，。！？；、,.!?;:？")
    if not (4 <= len(t) <= 10):
        return False
    if "？" in s or "?" in s:
        return False
    if _VERB_TAIL.search(t):
        return False
    return not t.startswith(_PRON_START)


def _pick_title_and_sub(chunks: list[str], title_max: int) -> tuple[str, str]:
    """大字=定性锚点, 小字=张力短语 (title 之外的最短分句, 3~16 字)。"""
    anchor_idx = None
    for i, c in enumerate(chunks[:4]):
        if _is_anchor_phrase(c):
            anchor_idx = i
            break
    if anchor_idx is None:
        return (
            _pick_title(chunks, title_max).rstrip("，。！？；、,.!?;:？").strip(),
            _clean_fragment(chunks[1])[:32] if len(chunks) > 1 else "",
        )
    title = re.sub(_OPENING_PREFIX, "", _clean_fragment(chunks[anchor_idx]))
    title = title.rstrip("，。！？；、,.!?;:？").strip()
    others = [
        (_clean_fragment(c), i)
        for i, c in enumerate(chunks)
        if i != anchor_idx and 3 <= len(_clean_fragment(c)) <= 16
    ]
    sub = min(others, key=lambda x: len(x[0]))[0] if others else ""
    return title, sub[:32]


# ── 多行台词分行 (hf_opening, 2026-08-11) ─────────────────────────────────────
# 语义保护: 禁止在词语中间截断, 优先在标点/语气停顿处换行.

# 中文可断点 (标点/语气词后)
_BREAK_AFTER = re.compile(r"[，。！？；、：,.!?;:）)]")
# 不可断开的词 (数字+单位, 专有名词前后缀)
_NO_BREAK = re.compile(r"(\d+[.%]?\s*[个亿万千百条块元秒%]?|AI|OpenAI|GitHub|Meta|华为|微软|谷歌)")
# 常见双字词: 若断点把这类词切开, 会破坏语义 (如"几乎"→"几"+"乎")
_TWO_CHAR_WORDS = {
    "几乎", "因为", "但是", "已经", "就是", "通过", "可以", "开始", "成为",
    "他们", "我们", "你们", "这个", "一个", "那个", "背后", "真的", "可能",
    "过去", "现在", "未来", "然后", "接着", "自己", "没有", "不是", "只是",
}


def _split_lines_semantic(text: str, max_chars: int = 12) -> list[str]:
    """按语义分行: 每行 ≤ max_chars 字, 优先在标点/停顿处断, 禁止切词.

    实现: 贪心填满 max_chars, 但:
      - 优先在标点后断 (从后往前找最近标点);
      - 无标点时, 若断点把常见双字词切开, 倒退到词首 (保护语义);
      - 仍无安全断点才保底 max_chars。
    """
    if not text:
        return []
    # 去情绪标记和停顿符
    t = re.sub(rf"\[{_EMOTION}\]", "", text)
    t = t.replace("||", "").replace("\n", "").strip()
    if not t:
        return []

    lines: list[str] = []
    while t:
        if len(t) <= max_chars:
            lines.append(t)
            break
        window = t[: max_chars + 3]
        # 候选断点: 标点后
        cuts = [m.end() for m in _BREAK_AFTER.finditer(window)]
        # 优先用最后一个标点断点 (贪心, 但 ≤ max_chars)
        cut = next((c for c in reversed(cuts) if c <= max_chars), -1)
        if cut <= 0:
            # 无标点: 用 max_chars, 但检查是否切开双字词
            cut = max_chars
            # 若 cut-1 与 cut 构成双字词后半, 倒退
            for w in _TWO_CHAR_WORDS:
                idx = t.find(w)
                if 0 <= idx < cut <= idx + len(w):
                    cut = idx  # 倒退到词首
                    break
        # 防尾部孤行 (2026-08-18 bug: "…围剿改" + "写。" 孤行): 切后剩余 ≤3 字时
        # 重平衡为两半 (优先中点附近标点断点, 无则对半), 避免 1-2 字吊尾
        if 0 < len(t) - cut <= 3:
            mid = len(t) // 2
            near = [c for c in (m.end() for m in _BREAK_AFTER.finditer(t))
                    if mid - 3 <= c <= mid + 3]
            cut = near[-1] if near else mid
        lines.append(t[:cut])
        t = t[cut:].strip()
    return lines


def _pick_red_words(text: str) -> list[str]:
    """从开场台词中识别关键冲击词 (渲染警示红).

    命中词: 骗/攻击/危险/失控/崩溃/造假/掀翻/屠杀/绝路/黑箱 等强冲击词.
    """
    _RED = ("骗", "攻击", "危险", "失控", "崩溃", "造假", "掀翻", "屠杀",
            "绝路", "黑箱", "砸", "血", "崩", "危", "撕", "斩")
    found = []
    for w in _RED:
        if w in text and w not in found:
            found.append(w)
    return found[:3]  # 最多 3 个, 避免满屏红


def build_opening_lines(text: str, max_chars: int = 12) -> dict:
    """构建 hf_opening 需要的多行台词 + 红词.

    Args:
        text: 开场口播 (Pass1 冲击句, 可能含 || 停顿)
        max_chars: 每行最大字数 (手机屏视觉重心居中, 默认 12)

    Returns:
        {"lines": [...], "red_words": [...]}
    """
    from app.services.pinyin_fix import strip_pinyin_marks

    text = strip_pinyin_marks(text or "")
    lines = _split_lines_semantic(text, max_chars)
    red_words = _pick_red_words(text)
    # 至少 2 行 (第一行冲击词, 后续兑现), 最多 4 行
    if len(lines) > 4:
        lines = lines[:4]
    return {"lines": lines, "red_words": red_words}
