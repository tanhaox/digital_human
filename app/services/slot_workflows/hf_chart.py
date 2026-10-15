"""HF 图表归一化: render_config 数据 → 统一 chart 结构.

对应 H 线 (hf_chart) 的图表侧; 口播文本提取见 hf_extract.py。
"""
from __future__ import annotations

import re

__all__ = [
    "_normalize_chart_input",
    "_coerce_items",
    "_chart_fallback",
    "_apply_chart_metadata",
    "_chart_has_data",
]


def _chart_has_data(chart: dict) -> bool:
    """图卡是否有可渲染数据: items 非空, 或 growth 带锚点数值.

    两者皆无 = 全空数据: hf_chart 模板空兜底会造 value:0 的 470px 巨数
    ("页面上只有一个字母 o", 2026-09-09 slot #32 实锤), 调用方应降级标题卡。
    """
    chart = chart or {}
    if chart.get("items"):
        return True
    return bool(re.search(r"-?\d+(?:\.\d+)?", str(chart.get("growth") or "")))


def _coerce_items(data) -> list[dict]:
    """Coerce render_config.data (dict-value / bare-number lists) → items."""
    items: list[dict] = []
    if not isinstance(data, list):
        return items
    for i, d in enumerate(data, start=1):
        if isinstance(d, dict) and d.get("value") is not None:
            try:
                items.append({"label": str(d.get("label") or f"数据{i}")[:20], "value": float(d["value"])})
            except (ValueError, TypeError):
                continue
        else:
            try:
                items.append({"label": f"数据{i}", "value": float(d)})
            except (ValueError, TypeError):
                continue
    return items


def _chart_fallback(render_config: dict, chart: dict) -> list[dict]:
    """Fallback items: render_config.chart.items → extracted chart items."""
    items: list[dict] = []
    rc_chart = render_config.get("chart")
    src_items = rc_chart.get("items") if isinstance(rc_chart, dict) else None
    if not src_items:
        src_items = chart.get("items") or []
    for it in src_items:
        if isinstance(it, dict) and it.get("value") is not None:
            try:
                items.append({"label": str(it.get("label") or "数据")[:20], "value": float(it["value"])})
            except (ValueError, TypeError):
                continue
    return items


def _apply_chart_metadata(chart: dict, render_config: dict) -> dict:
    """补全 unit/growth/color_scheme/label; 图卡数据规则路由 (2026-09-04)."""
    for k in ("unit", "growth", "color_scheme"):
        if render_config.get(k):
            chart[k] = str(render_config[k])
        elif not chart.get(k):
            chart[k] = ""
    if render_config.get("label"):
        chart["label"] = str(render_config["label"])
    elif not chart.get("label"):
        chart["label"] = ""

    # 图卡数据规则闸门 (用户裁决 2026-09-04, 模板 JS 双保险):
    #   pie 分段 <3 → 转 bar (恰 2 点走对比卡布局, 1 点走巨数卡);
    #   pie 分段 >5 → 转 bar (>5 分段不可读)。
    # 取代旧"pie 单扇区补其他"逻辑 (补出来的双段环形正是最丑的形态)。
    if chart["type"] == "pie" and not (3 <= len(chart["items"]) <= 5):
        chart["type"] = "bar"
    return chart


def _normalize_chart_input(render_config: dict, extracted: dict) -> dict:
    """归一化 LLM render_config + 口播提取结果 → 统一 chart 结构 (2026-08-01).

    兼容旧格式:
      - chart_type: "pie_chart"→pie, "bar_chart"/"line_chart"/其他→bar;
      - data: [{label,value}] 直接可用; [number] 旧格式按 label="数据N" 补全;
      - 缺 chart 字段时用 extracted.chart (口播提取的 bar items) 兜底。

    返回 {type, unit, growth, label, color_scheme, items:[{label,value}]}。
    """
    chart = dict(extracted.get("chart") or {})
    chart_type = render_config.get("chart_type") or chart.get("type") or "bar"
    if not isinstance(chart_type, str):
        chart_type = "bar"
    chart_type = chart_type.lower()
    if chart_type in ("pie", "pie_chart"):
        chart["type"] = "pie"
    else:
        chart["type"] = "bar"

    items = _coerce_items(render_config.get("data"))
    # data 缺失/无效时依次兜底: render_config.chart.items → 口播提取 items
    if not items:
        items = _chart_fallback(render_config, chart)
    # 图卡数据规则闸门 (2026-09-04 用户裁决, 判定在 [:5] 截断前 — 6 段占比结构
    # 截 5 段会破坏合计≈100%, 整体转 bar 更诚实):
    #   pie 分段 <3 → bar (恰 2 点走对比卡布局, 1 点走巨数卡, 由模板 JS 分派)
    #   pie 分段 >5 → bar (>5 分段不可读)
    if chart["type"] == "pie" and not (3 <= len(items) <= 5):
        chart["type"] = "bar"
    chart["items"] = items[:5]

    return _apply_chart_metadata(chart, render_config)
