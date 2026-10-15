# -*- coding: utf-8 -*-
"""字幕样式常量 + _StyledTextSegment (内联划重点).

拆包自 jy_draft_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import json

import pyJianYingDraft as draft_mod
from pyJianYingDraft import TextSegment, Timerange

__all__ = ["_StyledTextSegment"]

# ── 字幕样式 (2026-08-15 用户口径: 美观字号 5, 非 pyJYD 默认 8) ──
_SUBTITLE_SIZE = 5.0
# 字幕黑底条 (2026-08-27 参考片同款): 用户实测 v9 后弃用 — 我们的字幕多压暗调画面,
# 黑底条反而累赘。pyJYD 写法备查: TextBackground(color="#000000", alpha=0.55, round_radius=0.08)
# 署名条 (黄底黑字, 引用卡署名用) 保留: _CREDIT_BG
_CREDIT_BG = draft_mod.TextBackground(color="#FFDE00", alpha=0.95, round_radius=0.06)
# 内联划重点升级 (2026-08-17 v2): +1→+2.5 字号差 + 金色, 代替被砍掉的独立强调轨
# (剪映"智能划重点"的真实做法 — 关键词嵌在字幕行内, 变色变大, 不另起文字层)
_HL_COLOR = (1.0, 0.96, 0.54)  # 引文金 (45期实测)
_HL_COLOR_RED = (0.72, 0.11, 0.11)  # 冲击红 (四模板验证)
_HL_SIZE_DELTA = 2.5

# 字幕样式 (2026-08-21 用户定稿): 孤月体 / 字号5 / 奶油色 #F9F3C4 / 居中
# (原定义在元素级 PPT 段, 拆包时上移至此 — _StyledTextSegment 运行时引用它)
_SUBTITLE_COLOR = (0.976, 0.953, 0.769)  # #F9F3C4
# HF 卡上字幕色 (2026-09-10 用户令): 纸墨系卡底与奶油色太近 — HF 窗内字幕切 #465773
_SUBTITLE_COLOR_HF = (70 / 255, 87 / 255, 115 / 255)  # #465773
_SUBTITLE_ALIGN = 1  # 0=左 1=中 2=右


class _StyledTextSegment(TextSegment):
    """字幕 + 内联划重点: 高亮区间大两号半 + 变色, 其余基础样式.

    v2 (2026-08-17): 代替被砍掉的独立强调轨 — 剪映"智能划重点"的做法,
    关键词嵌在字幕行内变色变大, 不另起文字层(解决与字幕/HF卡重合+截断三问题).
    """
    _HL_DUAL_COLOR = True  # 数字/专名→金, 问句核心→红

    def __init__(self, text: str, timerange: Timerange, *,
                 highlight_ranges: list[tuple[int, int]] | None = None,
                 red_ranges: list[tuple[int, int]] | None = None,
                 base_color: tuple[float, ...] | None = None,
                 hl_color: tuple[float, ...] | None = None,
                 hl_size: float | None = None,
                 **kwargs):
        kwargs.setdefault("style", draft_mod.TextStyle(
            size=_SUBTITLE_SIZE, color=base_color or _SUBTITLE_COLOR,
            align=_SUBTITLE_ALIGN))
        # 字幕黑底条已撤 (2026-08-27 用户实测弃用), 裸白字回归
        super().__init__(text, timerange, **kwargs)
        self._hl_ranges = sorted(highlight_ranges or [])
        self._red_ranges = sorted(red_ranges or [])
        # 0916 动画线打字卡参数化: 同字号金黄承重词 (86万赞 1:1); 默认=J线划重点口径
        self._hl_color_v2 = hl_color if hl_color is not None else _HL_COLOR
        self._hl_size_v2 = (hl_size if hl_size is not None
                            else _SUBTITLE_SIZE + _HL_SIZE_DELTA)

    def export_material(self) -> dict:
        ret = super().export_material()
        if not self._hl_ranges and not self._red_ranges:
            return ret
        content = json.loads(ret["content"])
        base = dict(content["styles"][0])

        def make_style(color, ranges):
            """仅产出高亮段 (金/红), 空档留给下方合并时统一填 base — 避免两组 base 重叠."""
            hl = dict(base)
            hl["size"] = self._hl_size_v2
            fill = json.loads(json.dumps(base.get("fill") or {}))
            if "content" in fill and "solid" in fill["content"]:
                fill["content"]["solid"]["color"] = list(color)
            hl["fill"] = fill
            return [{**hl, "range": [s, e]} for s, e in ranges if s < e]

        gold = make_style(self._hl_color_v2, self._hl_ranges) if self._hl_ranges else []
        # 红通道并入金 (2026-08-26 用户决策): 放大字颜色统一 fff58a, 不再金红混用
        red = make_style(self._hl_color_v2, self._red_ranges) if self._red_ranges else []
        # 合并两组 (金+红), 按位置排序, 空白用 base 填充; 重叠时先到者优先
        merged = sorted(gold + red, key=lambda s: s["range"][0])
        styles = []
        cursor = 0
        for st in merged:
            s, e = st["range"]
            if s < cursor:
                continue  # 已被更早区间覆盖, 丢弃避免嵌套样式
            if s > cursor:
                styles.append({**base, "range": [cursor, s]})
            styles.append(st)
            cursor = e
        if cursor < len(self.text):
            styles.append({**base, "range": [cursor, len(self.text)]})
        if styles:
            content["styles"] = styles
            ret["content"] = json.dumps(content, ensure_ascii=False)
        return ret
