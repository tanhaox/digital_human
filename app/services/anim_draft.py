# -*- coding: utf-8 -*-
"""动画产线 对齐归真 + 剪映草稿装配 (0914 系统化, P1 段手工流程的机器化).

align_episode (音画对齐):
  音轨真理源 = 主产线口播 (episode.script_id → 最新 completed AudioJob 的
  manifest.json/wav 包, P1 段实证: 006+009+_011_cut.wav 拼的就是主线音频,
  不是 sandbox 逐镜 TTS — 用户耳朵验收过的是主线渲染).
  shots.narration 与音频包文本做规范化字符流对齐 → 每镜 t_start/t_end 按实测
  音频归真 (旧轴备份 t_start_orig, 复用 tts.py 归真约定) + 漂移三分类审计
  (OK≤0.25s / 吸收≤0.5s / H3重跑>0.5s / 段超8s).
  dry_run=True 只出审计报告不动 shots.json.

build_draft (剪映草稿, pyJianYingDraft, storyboard_draft.py 范式):
  video 轨: 归真时间轴逐镜 anim/<sid>.mp4; 品牌卡镜插定稿卡 (5s 播完+首帧定格
  续槽, P1 的 s11 同款); 缺片镜 img 定格 → 纯色占位卡降级 (拼图彩排).
  caption 轨: text_layer → 暖金色字幕 (H3 文字配方同色系).
  typing 轨: 0-2s 打字卡逐字 (文案 = roadmap_json.打字卡, 书级冻结).
  voice 轨: 整集口播 concat (voice_full.wav, 随 align 落 ep 目录).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sqlite3
import wave
from contextlib import closing
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft_mod
from pyJianYingDraft import ClipSettings, TextIntro, TextSegment, TextStyle, trange

from app.database import get_session_maker
from app.models import AudioFile, AudioJob
from app.models.book import Episode
from app.services.anim_pipeline import shots as shots_mod
from app.services.anim_pipeline.shots import norm_for_match
from app.services.anim_pipeline.config import load as anim_cfg_load
from app.services.anim_service import AnimBusyError, AnimNotFound, ep_guard, resolve_ep

logger = logging.getLogger(__name__)

_US = 1_000_000
# 暖金 (H3 文字配方同色系: 楷体/书法+暖色; 本版 TextStyle 不支持字体族, 剪映里可后调)
_GOLD = (0xC9 / 255, 0xA2 / 255, 0x5E / 255)

# ── caption 档 = 新闻线 (J线) 观感平移 (0916 用户令: 字幕太大/断句不是完全体) ──
# 源: jy_draft_service/subtitle_style.py + ppt_element.py 2026-08-21 用户定稿
# 孤月体 / 字号5 / 奶油色#F9F3C4 / 居中 / 黑色描边+阴影 / transform_y=-0.75
from app.services.jy_draft_service.subtitle_style import _StyledTextSegment
from app.services.jy_draft_service.subtitle_text import (
    find_highlight_ranges, split_subtitle, wash_subtitle_text)
from app.services.jy_draft_service.auto_choreo import _CAT_ANIM, _classify_chunk

_CAP_FONT = draft_mod.FontType.孤月体
_CAP_COLOR = (0.976, 0.953, 0.769)  # #F9F3C4 奶油色 (base_color 传给 _StyledTextSegment)
_CAP_TRANSFORM_Y = -0.75
_CAP_BORDER = draft_mod.TextBorder(alpha=0.85, color=(0.0, 0.0, 0.0), width=12)
_CAP_SHADOW = draft_mod.TextShadow(alpha=0.55, color=(0.0, 0.0, 0.0), diffuse=12, distance=3)
_CAP_SPLIT_LIMIT = 30  # 横屏断句上限 (J线 job_draft 横屏同款; >30 字拆多条滚动)

# ── 86万赞打字卡 1:1 (0916 用户令; 锚=.tmp/s6top_open_0_5s_8fps.png 逐帧判读) ──
# 白字居中偏上两行 · 三承重词同字号金黄 · 逐字 ~0.25s 起 ~1.9s 满 无光标 ·
# 2-3s 定格 · 打字音效同帧 (打字（字幕专用）1416ms 与打字窗严丝合缝)
_TYPE_BASE = (1.0, 1.0, 1.0)          # 正文白 (锚点实况)
_TYPE_HL = (1.0, 0.75, 0.09)          # 承重词黄 (jy_style_palette 关键词黄, 53期 mined)
_TYPE_SIZE = 6.8                      # 44字骨架两行装下 (30字×5号口径: chars/line=150/size)
_TYPE_Y = 0.45                        # 画面上四分之一带 (锚点实况)
_TYPE_SHADOW = draft_mod.TextShadow(alpha=0.45, color=(0.0, 0.0, 0.0), diffuse=8, distance=2)
_TYPE_SFX = "打字（字幕专用）"
_TYPE_START_S = 0.25                  # 首字出现 (锚点 ~0.25s)
_TYPE_END_S = 1.9                     # 打满 (锚点 ~1.9s)

# ── 选书后 2s 版式 (0916 模板令, 64-商业认知 拆解) ──
_FONT_SIYUAN = "674043984025433344"   # 思源黑体 (模板明文实测 id)
_TYPE_LINE_SPACING = 6               # 0916 用户令: 打字卡行间距 6 (抖音同款)


# ── 字幕数字阿拉伯还原 (0916 用户令: 一九二八年=1928年) ──
_NUM_WORDS = {c: i for i, c in enumerate("零一二三四五六七八九")}
_NUM_UNITS = {"十": 10, "百": 100, "千": 1000}
_NUM_BIG = {"万": 10000}
# 白名单: 数字串但实为词, 还原会坏义 (一起→1起 类); 0916 令: 老谭口号/
# 第X集序数/一本惯用语 禁改
# 白名单: 数字串但实为词, 还原会坏义; 0916 令: 老谭口号/一本惯用语 禁改
_NUM_WHITELIST = ("一起", "一样", "一直", "一切", "一共", "一旦", "一点", "一些",
                  "统一", "十分", "万分", "万一", "一边", "一面", "一路",
                  "读一本书", "多一个硬本事", "一本书", "一本通", "每一个", "另一个", "一下")
_NUM_NEXT_OK = set("个位次年天秒分集本条张步场块件美元倍人户万家公司章节期轮次遍")
# 注: 量词集外的单字数字一律不转 (四射/五花八门/不三不四 类成语描述词防坏义)


def _cn_seq_to_int(seq: str) -> int | None:
    """中文数字串 (≤百万级, 十/百/千/万) → 整数; 解析失败 None."""
    total, num = 0, 0
    for ch in seq:
        if ch in _NUM_WORDS:
            num = num * 10 + _NUM_WORDS[ch]
        elif ch in _NUM_UNITS:
            total += (num or 1) * _NUM_UNITS[ch]
            num = 0
        elif ch in _NUM_BIG:
            total = (total + num) * _NUM_BIG[ch]
            num = 0
        else:
            return None
    return total + num


def _fmt_num(v: int, seq: str) -> str:
    """显示形: 万级保留 万 写法 (百万→100万), 其余纯数字."""
    if "万" in seq and v >= 10000 and v % 10000 == 0:
        return f"{v // 10000}万"
    return str(v)


def num_to_arabic(text: str) -> str:
    """字幕数字阿拉伯还原 (保守版, 0916 两轮验收):
    百分之X→X%; 逐位串(一九二八,≥2位)→1928; 带单位串(四十/三百)→整值(万级
    留万); 单字数字仅跟量词才转; 白名单词/第X集序数 原样."""
    out = text
    for i, w in enumerate(_NUM_WHITELIST):
        out = out.replace(w, f"\x00{i}\x00")
    _kept: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        _kept.append(m.group(0))  # 整段摘走 (含数字), 防 inner 数字被后续正则改
        return f"\x00E{len(_kept) - 1}\x00"

    out = re.sub(r"第[零一二三四五六七八九十百]+集", _stash, out)
    out = re.sub(r"百分之([零一二三四五六七八九十]{1,4})",
                 lambda m: f"{_cn_seq_to_int(m.group(1))}%" if _cn_seq_to_int(m.group(1)) is not None else m.group(0),
                 out)

    def _conv(m: "re.Match[str]") -> str:
        s = m.group(0)
        if len(s) >= 2 and all(c in _NUM_WORDS for c in s):
            return "".join(str(_NUM_WORDS[c]) for c in s)  # 逐位读法 (年份/编号)
        if len(s) == 1:
            return s  # 单字: 交给量词规则 (成语/描述词防坏义)
        v = _cn_seq_to_int(s)
        return _fmt_num(v, s) if v is not None and v > 0 else s

    out = re.sub(r"[零一二三四五六七八九十百千万]+", _conv, out)
    # 单字数字 + 量词 → 阿拉伯 (三个/九集/5秒类) — 0918 "一"除名: "一"+单字量词是
    # 不定冠词 (这一集/一本/一个/一天 口语弱读非数量, 用户四例实锤); 真数量的
    # "一"走长串路径 (一百/一万/一九二八) 不受影响
    out = re.sub(r"([二三四五六七八九])(?=[个位次年天秒分集本条张步场块件美元倍人户万家公司章节期轮次遍])",
                 lambda m: str(_NUM_WORDS[m.group(1)]), out)
    for i, w in enumerate(_NUM_WHITELIST):
        out = out.replace(f"\x00{i}\x00", w)
    out = re.sub(r"\x00E(\d+)\x00", lambda m: _kept[int(m.group(1))], out)
    return out


def _custom_font(resource_id: str):
    """自定义剪映字体 (pyJYD 枚举外的, 按 resource_id 注入; ppt_element 同款)."""
    _meta = type("FM", (), {"resource_id": resource_id})()
    return type("CF", (), {"value": _meta})()


def _book_author_short(book_id: str) -> str:
    """book_projects.author → 底字用短名: 去译者(;/，后) 去【国籍】前缀."""
    try:
        con = sqlite3.connect(anim_cfg_load().db_path)
        r = con.execute("select author from book_projects where id=?", (book_id,)).fetchone()
        con.close()
        a = str((r or [""])[0] or "")
        a = re.split(r"[;；,，/]", a)[0].strip()
        a = re.sub(r"^【[^】]*】", "", a).strip()
        return a
    except Exception:  # noqa: BLE001
        return ""


def _typing_emph_spans(text: str) -> list[tuple[int, int]]:
    """打字卡三承重词区间 (骨架锚定): 坚持每晚睡前 / 收益主体槽 / 超过90%.

    骨架书级冻结 (0914 定稿): "如果你能坚持每晚睡前看一集，一个月后，
    {收益主体}，会超过90%的{对比人群}。" — 槽位用冻结锚词定位, 缺锚即跳 (宁缺毋滥)。
    """
    spans = []
    for w in ("坚持每晚睡前", "超过90%"):
        i = text.find(w)
        if i >= 0:
            spans.append((i, i + len(w)))
    m = re.search(r"一个月后，(.+?)，会超过", text)
    if m:
        spans.append((m.start(1), m.end(1)))
    return sorted(spans)


def _typing_split_lines(text: str) -> list[str]:
    """打字卡按冻结骨架切行 (0916 行槽制): 三句 = 三行, 槽位随书但切点冻结.

    "如果你能坚持每晚睡前看一集，" | "一个月后，{X}，" | "会超过90%的{Y}。"
    切点 = 看一集后的逗号 + 「，会超过」前的逗号; 骨架不合 (极端改写) 回退整句一行.
    """
    i2 = text.find("，会超过")
    i1 = text.find("，", text.find("看一集"))
    if i1 > 0 and i2 > i1:
        return [text[:i1 + 1], text[i1 + 1:i2 + 1], text[i2 + 1:]]
    if i1 > 0:
        return [text[:i1 + 1], text[i1 + 1:]]
    return [text]


def _typing_line_ys(n: int) -> tuple[float, ...]:
    """行槽 y. 0916 定稿: 行1 上提 (0.32 独立元素), 行2+3 块下移 (0.10/-0.03,
    块中心 0.035 — 行间距 6 撑高块后与行1 两元素防叠, 用户验收调)."""
    if n >= 3:
        return (0.32,) + tuple(round(0.10 - 0.13 * i, 3) for i in range(n - 1))
    if n == 2:
        return (0.10, -0.03)
    return (0.32,)


def _collect_text_events(shots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """text_layer → (caption 事件, 强调字事件): 绝对时间 + 同轨去重叠 (截尾保后).

    0916 根治"字幕/强调字不是完全体": caption/hero 的 t_end 常超镜尾, 绝对时间
    与后镜同轨段重叠 → pyJYD SegmentOverlap 被 per-shot 容错吞掉、后一条整段
    丢失 (ep1 实测 caption 7/51 丢段; hero 轨此前从未注册整轨全灭). 同轨重叠
    解法 = 砍前条尾巴不推后条 (字幕跟口播, 时间锚不漂). 强调字按 impact/hero
    分桶钳制 (异轨共存: 同帧 "VS"+hero_number 双层设计, ep1 s29 实锤).

    0921 overlay 桶弃渲 (用户实锤 ep6 "零星多生产的字幕单独一轨"): 军规下沉的
    overlay 条目实产全是碎字 (计数竞争落选单字/拆镜残片/impact 双登记), 零概念词
    — 装配层不再收集渲染. 条目仍在 text_layer 数据层 (音效 cue / 回填候选 / 留档).
    """
    caps: list[dict[str, Any]] = []
    heroes: list[dict[str, Any]] = []
    impacts: list[dict[str, Any]] = []
    for s in shots:
        tls = list(s.get("text_layer") or [])
        # 品牌卡镜: 仪式句走后期金字 (旧资产烤字根治, v3 文字层纪律)
        if s.get("brand_card") and not tls and (s.get("narration") or "").strip():
            _span = max(float(s["t_end"]) - float(s["t_start"]), 2.0)
            tls = [{"kind": "title", "text": str(s["narration"]).strip(),
                    "t_start": 0.6, "t_end": round(_span - 0.4, 2)}]
        for t in tls:
            kind = str(t.get("kind") or "caption")
            t0, t1 = float(t.get("t_start") or 0), float(t.get("t_end") or 0)
            if t1 <= t0:
                t1 = max(float(s["t_end"]) - float(s["t_start"]), 1.0)
            ev = {"a0": float(s["t_start"]) + t0, "a1": float(s["t_start"]) + t1,
                  "text": str(t.get("text") or ""), "kind": kind, "sid": s["shot_id"]}
            if kind == "overlay":
                continue  # 0921 弃渲: 数据层保留 (音效cue/回填), 不进渲染事件流
            elif kind == "impact":
                impacts.append(ev)
            elif kind in ("hero_number", "year", "title", "logo_note"):
                heroes.append(ev)
            else:
                caps.append(ev)
    for evs in (caps, heroes, impacts):
        evs.sort(key=lambda e: e["a0"])
        for i, ev in enumerate(evs):
            nxt = evs[i + 1]["a0"] - 0.05 if i + 1 < len(evs) else ev["a1"]
            ev["a1"] = min(ev["a1"], nxt)
            if ev["a1"] - ev["a0"] < 0.3:
                ev["a1"] = ev["a0"] + 0.3  # 病态窄窗保底可见; 真重叠交给 add 容错留痕
    return caps, heroes + impacts


def _render_hero(script: Any, events: list[dict[str, Any]]) -> int:
    """标题层渲染 (0916 降维令后仅剩 title; hero_number/impact/logo_note 全降维进
    字幕行内高亮 — 0917 补 logo_note: 独立层只产出孤岛浮字, 见 _render_captions)."""
    n = 0
    for ev in events:
        kind = ev["kind"]
        if kind != "title":
            continue
        style, ty = TextStyle(size=16, bold=True, color=_GOLD,
                              max_line_width=0.86), -0.62
        try:
            script.add_segment(TextSegment(
                ev["text"],
                trange(int(round(ev["a0"] * _US)),
                       max(int(round((ev["a1"] - ev["a0"]) * _US)), 500_000)),
                style=style, clip_settings=ClipSettings(transform_y=ty)), "hero")
            n += 1
        except Exception as exc:  # noqa: BLE001 — 单条失败不炸草稿
            logger.warning("[draft] 标题字失败 %s(%s): %s", ev["sid"], kind, exc)
    return n


_CN_DIGITS = "零一二三四五六七八九"


def _word_variants(word: str) -> list[str]:
    """锚定用数字变体: 40→四十 (整值), 1928→一九二八 (年份逐位)."""
    import re
    out = [word]
    for m in re.finditer(r"\d+", word):
        v = int(m.group(0))
        if 11 <= v <= 99:
            tens, ones = divmod(v, 10)
            cn = ("" if tens == 1 else _CN_DIGITS[tens]) + "十" + \
                 ("" if not ones else _CN_DIGITS[ones])
        else:
            cn = "".join(_CN_DIGITS[int(c)] for c in m.group(0))
        out.append(word.replace(m.group(0), cn, 1))
    return out


def _anchor_range(word: str, chunk: str) -> tuple[int, int] | None:
    """强调词 → 字幕块内行内高亮区间 (降维锚定, 0916 用户令: 两层浮字多余).

    三级: 逐字命中 → 数字变体 (40年→四十 / 1928→一九二八) → 子序列最短窗口
    (回本难→回本越难, 窗长 ≤ 词长+4 防跨句误锚). 纯标点/单字符词不锚 (宁缺毋滥,
    音效照挂).
    """
    if len(word) < 2 or not any(c.isalnum() for c in word):
        return None
    for w in _word_variants(word):
        i = chunk.find(w)
        if i >= 0:
            return (i, i + len(w))
    idxs: list[int] = []
    pos = 0
    for ch in word:
        j = chunk.find(ch, pos)
        if j < 0:
            return None
        idxs.append(j)
        pos = j + 1
    if idxs[-1] - idxs[0] + 1 > len(word) + 4:
        return None
    return (idxs[0], idxs[-1] + 1)


def _render_captions(script: Any, events: list[dict[str, Any]],
                     emph_by_sid: dict[str, list[tuple[str, str]]] | None = None) -> dict[str, int]:
    """caption 轨统一装配 (J线三件套平移): 洗涤 → 断句 → 内联划重点 → TextIntro 动效.

    动效 = J线 R9 v3 配方平移 (0916): 首条可见字幕卡拉OK逐字点亮; 语义分类
    (悬念→向上滑动/金句→放大) 受密度闸门 (前30s全开,之后≥4s);
    plain 块走渐显基础入场 (J线"渐显=专业稿标配"教义, 动画线口播叙述体 plain
    占 51/58, 纯分类平移只剩零星动效). 音效不动 — 动画线音效是 hero 帧键控.

    0921 星光闪闪禁令 (用户实锤 ep6 "3本资产账"字幕): 星光闪闪出戏, 金额类/
    数字强调词动效一律降级放大 (J线 _CAT_ANIM money 配方在动画线不适用).

    0916 降维令 (用户: 两层浮字多余): hero_number/impact 不再单独渲染, 导演
    强调词锚进字幕行内高亮 (+2.5 号金) + 词型动效 (数字→放大/冲击→上滑/
    其余→放大), 即 J线 v2 "砍强调轨, 划重点进字幕行" 教义.
    """
    emph_by_sid = emph_by_sid or {}
    n = dropped = anim_n = anim_skip = 0
    anchored_words: set[tuple[str, str]] = set()
    total_words = sum(len(ws) for ws in emph_by_sid.values())
    first_cap = True
    last_emph_at: float | None = None
    cursor_us = 0  # 链式推起点 (J线铁律): start=上段末+1ms, 拆镜后事件贴邻也不重叠
    for ev in events:
        washed = num_to_arabic(wash_subtitle_text(ev["text"]))
        if not washed:
            continue
        chunks = split_subtitle(washed, _CAP_SPLIT_LIMIT)
        total_c = sum(len(c) for c in chunks)
        c0 = ev["a0"]
        for j, ch in enumerate(chunks):
            c1 = ev["a1"] if j == len(chunks) - 1 else \
                c0 + (ev["a1"] - ev["a0"]) * len(ch) / max(total_c, 1)
            s0 = max(int(round(c0 * _US)), cursor_us + 1000)
            if int(round(c1 * _US)) <= s0:
                c0 = c1
                continue  # 前段占满窗口, 无缝可放 (宁缺不叠)
            # 导演强调词降维锚定 (同镜 word → 行内区间)
            hits: list[tuple[tuple[int, int], str]] = []
            for word, kind in emph_by_sid.get(ev["sid"], ()):
                rng = _anchor_range(word, ch)
                if rng:
                    hits.append((rng, kind))
                    anchored_words.add((ev["sid"], word))
            hl = sorted(set(find_highlight_ranges(ch)) | {r for r, _ in hits})
            anim, anim_ms, is_emph = "渐显", 400, False
            if first_cap:
                anim, anim_ms = "卡拉OK", None
            elif hits:
                if last_emph_at is None or c0 <= 30.0 or c0 - last_emph_at >= 4.0:
                    w0, k0 = hits[0]
                    if k0 == "impact":
                        anim, anim_ms = "向上滑动", 400
                    else:
                        anim, anim_ms = "放大", 400
                    is_emph = True
                else:
                    anim_skip += 1
            else:
                cat, _kw = _classify_chunk(ch)
                if cat in _CAT_ANIM and (
                        last_emph_at is None or c0 <= 30.0 or c0 - last_emph_at >= 4.0):
                    anim, anim_ms = _CAT_ANIM[cat]
                    is_emph = True
                elif cat in _CAT_ANIM:
                    anim_skip += 1
            if anim == "星光闪闪":  # 0921 禁令: J线 money 配方出戏, 降级放大
                anim = "放大"
            if anim and not hasattr(TextIntro, anim):
                anim, anim_ms, is_emph = "渐显", 400, False
            try:
                seg = _StyledTextSegment(
                    ch, trange(s0, max(int(round((c1 - c0) * _US)), 250_000)),
                    font=_CAP_FONT, border=_CAP_BORDER, shadow=_CAP_SHADOW,
                    base_color=_CAP_COLOR,
                    highlight_ranges=hl,
                    clip_settings=ClipSettings(transform_y=_CAP_TRANSFORM_Y))
                if anim:
                    seg.add_animation(getattr(TextIntro, anim),
                                      duration=anim_ms * 1000 if anim_ms else None)
                    anim_n += 1
                script.add_segment(seg, "caption")
                n += 1
                cursor_us = s0 + max(int(round((c1 - c0) * _US)), 250_000)
            except Exception as exc:  # noqa: BLE001 — 字幕单条失败不炸草稿
                dropped += 1
                logger.warning("[draft] 字幕失败 %s: %s", ev["sid"], exc)
            if is_emph:
                last_emph_at = c0
            first_cap = False
            c0 = c1
    if total_words > len(anchored_words):
        logger.info("[draft] 强调词未锚进字幕 %d 个 (音效照挂, 仅无行内高亮)",
                    total_words - len(anchored_words))
    return {"captions": n, "caption_drops": dropped,
            "caption_anims": anim_n, "anim_density_skip": anim_skip,
            "hero_merged": len(anchored_words), "hero_unanchored": total_words - len(anchored_words)}


# ── 音轨真理源: 主产线口播包 ──────────────────────────────────

def sentence_table(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """音频包 → 全集句级时间表 (0917 refs 协议公共设施).

    与 _sentence_segs 同口径 (句末符切 + wav 静音吸附 + 字数比例回退),
    音频轴 0 起。规划镜 refs 引句号 → 系统查表定镜界 — 句级时间的唯一真相源,
    闸门校验 (镜界落句边) 与规划消费同一张表, 结构上消灭两表打架。"""
    return _sentence_segs(files)


def _sentence_segs(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """音频包 → 句级 segs (0916: 老音频是 12-24s 巨型多句包, 包边≠句边 —
    时间轴漂移的真正源头). 句边界 = 包文本按句末符切 + ffmpeg silencedetect
    吸附包内真实停顿 (J线 _caption_bounds_snapped 同款), 无 wav/检测失败回退
    字数比例 (标点降权 + 句尾停顿加权)."""
    import re as _re
    from scripts.tts_lib.silence_split import _detect_silences, _silence_candidates

    segs: list[dict[str, Any]] = []
    t_cur = 0.0
    for f in files:
        d = float(f.get("dur_s") or 0)
        text = str(f.get("text") or "")
        if not text.strip() or d <= 0:
            t_cur += d
            continue
        sents = [s for s in _re.split(r"(?<=[。！？；\n])", text) if s.strip()]
        if len(sents) <= 1:
            segs.append({"t_start": round(t_cur, 2), "t_end": round(t_cur + d, 2),
                         "duration": round(d, 2), "text": text.strip(),
                         "file": f.get("file"), "pack_t0": round(t_cur, 2)})
            t_cur += d
            continue
        weights = [sum(0.5 if c in "，、：；。！？,.!?;: \n" else 1.0 for c in s) + 0.35
                   for s in sents]
        total_w = sum(weights)
        exp: list[float] = []
        cum = 0.0
        for w in weights[:-1]:
            cum += w
            exp.append(cum / total_w * d)
        cands: list[float] = []
        try:
            wav = Path(f["file"])
            if wav.exists():
                cands = _silence_candidates(_detect_silences(wav), d)
        except Exception:  # noqa: BLE001 — 检测失败回退比例
            cands = []
        tol = max(0.5, 0.3 * d / len(sents))
        bounds, prev = [0.0], 0.0
        for e in exp:
            best = None
            for c in cands:
                if c <= prev + 0.1:
                    continue
                if best is None or abs(c - e) < abs(best - e):
                    best = c
            pick = best if best is not None and abs(best - e) <= tol else e
            pick = min(max(pick, prev + 0.15), d - 0.1)
            bounds.append(pick)
            prev = pick
        bounds.append(d)
        for i, s in enumerate(sents):
            sd = bounds[i + 1] - bounds[i]
            if sd > 0.05:
                # 0918 根治位: TTS 指令不是文本 — 停顿标记 -Xs- 与拼音标注
                # <字|PINYIN> 在句级表出口统一剥 (字幕/refs narration/闸门全下游
                # 一起干净; 纯标记句保留轴占位, text 置空由消费端跳过)
                _t = re.sub(r"-\d+(?:\.\d+)?s-", "", s)
                _t = re.sub(r"<([^|>]+)\|[^>]+>", r"\1", _t).strip()
                # 0919 60s 特区: 带 file/pack_t0 → hook_zone 逗号切可静音吸附
                segs.append({"t_start": round(t_cur + bounds[i], 2),
                             "t_end": round(t_cur + bounds[i + 1], 2),
                             "duration": round(sd, 2), "text": _t,
                             "file": f.get("file"), "pack_t0": round(t_cur, 2)})
        t_cur += d
    return segs


def _wav_duration(path: Path) -> float:
    with closing(wave.open(str(path), "rb")) as w:
        return w.getnframes() / w.getframerate()


@lru_cache(maxsize=128)
def _wav_edge_silence(path: str) -> tuple[float, float]:
    """包头/包尾静音时长 (1010 用户令: 接缝静音闸). -35dB:d=0.12 敏检,
    与 hook_zone._stops 同参; 检测失败返回 (0,0) 不炸装配."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", path, "-af", "silencedetect=noise=-35dB:d=0.12",
             "-f", "null", "-"],
            capture_output=True, text=True, errors="replace", timeout=30)
        ss = [float(m.group(1)) for m in re.finditer(r"silence_start: ([\d.]+)", r.stderr)]
        se = [float(m.group(1)) for m in re.finditer(r"silence_end: ([\d.]+)", r.stderr)]
        dur = _wav_duration(Path(path))
        head = se[0] if ss and ss[0] <= 0.05 else 0.0
        tail = dur - ss[-1] if se and se[-1] >= dur - 0.05 else 0.0
        return (round(head, 2), round(tail, 2))
    except Exception:  # noqa: BLE001 — 探测失败 = 无数据, 闸放行
        return (0.0, 0.0)


# 接缝静音闸阈值 (1010): 前包尾+后包头连缀静音超过此值 = 口播超长停顿
# (ep2 实锤 008→009 接缝 1.94s: 品牌句包尾静音 1.63 + 下包头 0.31 连缀,
# 58.5-60.3s 无声+粉卡)。设计内上限 = 0.8s 停顿标记 + 自然气口 ≈1.1s,
# 阈值 1.2s 不误伤仪式停顿。
_VOICE_SEAM_GATE = 1.2


def timeline_violations(book_ref: str, ep: int, tol: float = 0.06) -> list[str]:
    """时间纪律直验 (0916 全面加强·生成闸门): 全部镜 t_start/t_end 必须落在
    句级包边上. 任何路径 (老 doc/重规划回归/未对齐) 产生的乱纪在此现形 —
    K2/H3 开跑前验, 防按乱纪状态烧 GPU 后错配."""
    import bisect
    book_id, book_title = resolve_ep(book_ref, ep)
    audio = fetch_episode_audio(book_id, ep)
    sents = _sentence_segs(audio["files"])
    if not sents:
        return []
    tc = float(anim_cfg_load().brand_card.opening_sec)
    edges = sorted({round(tc + s["t_start"], 2) for s in sents}
                   | {round(tc + s["t_end"], 2) for s in sents})
    doc = shots_mod.load_doc(book_title, ep)
    # 0919 60s 特区槽位边界并入合法集: 装包器产物 = 句边/逗号切(>6.5s 长句)/
    # 60s 硬界 — 三者皆系统有意切点, 非乱纪; 非 zone 镜仍严格句边执法。
    # 0919(三) 全片装包制: packed 镜槽界 (逗号切/窗裁块) 同理并入 — 装包器
    # 切点皆系统有意, LLM 零时间输出下不存在乱纪来源。
    edges = sorted(set(edges)
                   | {round(float(s.get("t_start") or 0), 2)
                      for s in doc.get("shots") or [] if s.get("zone") or s.get("packed")}
                   | {round(float(s.get("t_end") or 0), 2)
                      for s in doc.get("shots") or [] if s.get("zone") or s.get("packed")})
    bad: list[str] = []
    for s in sorted(doc.get("shots") or [], key=lambda x: float(x.get("t_start", 0))):
        for k in ("t_start", "t_end"):
            t = float(s.get(k) or 0)
            i = bisect.bisect_left(edges, t)
            near = min((abs(t - e) for e in edges[max(0, i - 1):i + 2]), default=99.0)
            if near > tol:
                bad.append(f"{s['shot_id']}.{k}={t:.2f}(偏{near:.2f}s)")
    return bad


def fetch_episode_audio(book_id: str, ep: int) -> dict[str, Any]:
    """episode.script_id → 最新 completed AudioJob 的音频包.

    返回 {files: [{file: 绝对路径, text, dur_s}], job_id, output_dir}.
    优先 manifest.json (0913 包化: 一个 wav=一整包文本, 合成输入的真相);
    老行级产物回退 AudioFile 行 + Segment 单行文本.
    """
    with closing(get_session_maker()()) as db:
        episode = db.query(Episode).filter(
            Episode.book_id == book_id, Episode.ep_index == ep).first()
        if not episode or not episode.script_id:
            raise AnimNotFound(f"第 {ep} 集没有产线 script_id (先在讲书页产出/进产线)")
        job = (db.query(AudioJob)
               .filter(AudioJob.script_id == episode.script_id,
                       AudioJob.status == "completed")
               .order_by(AudioJob.created_at.desc()).first())
        if not job:
            raise AnimNotFound("该集没有已完成的 TTS 音频 (先在音频页生成)")
        rows = (db.query(AudioFile)
                .filter(AudioFile.audio_job_id == job.id)
                .order_by(AudioFile.filename).all())
        texts: dict[str, str] = {}
        mod_ids: dict[str, Any] = {}
        seg_durs: dict[str, float] = {}
        mf = Path(job.output_dir) / "manifest.json"
        if mf.exists():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                texts = {e.get("file"): (e.get("text") or "") for e in data.get("segments") or []
                         if e.get("file")}
                # 0917 模块总线: 包级 module_id 透传 (TTS 模块墙所标, 切场/分镜消费)
                mod_ids = {e.get("file"): e.get("module_id") for e in data.get("segments") or []
                           if e.get("file") and e.get("module_id") is not None}
                # 0918 时长单真相源: manifest duration 含气口 (垫在 wav 上), 而 DB
                # AudioFile.duration 停在合成时点 — 两源差 11.7s 实锤 (镜轴 vs voice
                # 三层不一致元凶)。规划/对齐一律吃 manifest。
                seg_durs = {e.get("file"): float(e.get("duration") or 0)
                            for e in data.get("segments") or [] if e.get("file")}
            except Exception:  # noqa: BLE001 — manifest 坏则回退行级
                logger.warning("[align] manifest.json 解析失败, 回退 Segment 文本: %s", mf)
        if not texts:
            from app.models import Segment
            seg_ids = [f.segment_id for f in rows if f.segment_id]
            seg_texts = {s.id: s.text for s in db.query(Segment).filter(
                Segment.id.in_(seg_ids or [""])).all()} if seg_ids else {}
            texts = {f.filename: seg_texts.get(f.segment_id or "", "") for f in rows}
        files = []
        for f in rows:
            # full_paragraph.wav = 整段拼接试听产物 (播放器用), 非合成包 —
            # 混进 files 会打翻模块判定 (无 module_id) 并污染时间轴 (0918 实锤:
            # 22 包+它=23 → module_windows 判 None → 规划退回 LLM 自由切场)
            if f.filename == "full_paragraph.wav":
                continue
            p = Path(f.file_path)
            if not p.exists():
                continue
            dur = seg_durs.get(f.filename) or (f.duration if f.duration and f.duration > 0 else _wav_duration(p))
            files.append({"file": str(p), "name": f.filename,
                          "text": texts.get(f.filename, ""), "dur_s": round(float(dur), 3),
                          "module_id": mod_ids.get(f.filename)})
        if not files:
            raise AnimNotFound(f"音频 job {job.id} 没有可用 wav")
        return {"files": files, "job_id": job.id, "output_dir": job.output_dir}


def concat_voice(files: list[dict[str, Any]], out_wav: Path) -> Path:
    """整集口播 = 按序 concat (气口已烘进各包 wav, 包间零隙)."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    lst = out_wav.with_name("_voice_concat.txt")
    lst.write_text("".join(
        "file '" + f["file"].replace("'", "'\\''") + "'\n" for f in files
        if str(f.get("text") or "").strip()), encoding="utf-8")  # 空文本包(静音)不入轨
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", str(out_wav)],
        capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not out_wav.exists():
        # 混格式兜底: 重编码 pcm
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(lst), "-ar", "44100", "-ac", "2", str(out_wav)],
            capture_output=True, text=True, timeout=300)
        if r2.returncode != 0:
            raise RuntimeError(f"口播 concat 失败: {r.stderr or r2.stderr}")
    return out_wav


# ── 文本对齐 (字符流 → 音频时间轴) ────────────────────────────

def _char_time_map(files: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], float]:
    """音频包文本 → 规范化字符流 + 每包 [char_lo, char_hi, t_lo, dur]."""
    parts: list[str] = []
    spans: list[dict[str, Any]] = []
    t = 0.0
    offset = 0
    for f in files:
        norm = norm_for_match(f["text"])
        if norm:
            spans.append({"lo": offset, "hi": offset + len(norm), "t": t, "dur": f["dur_s"]})
            parts.append(norm)
            offset += len(norm)
        t += f["dur_s"]
    stream = "".join(parts)
    return stream, spans, t


def _char_to_time(pos: int, spans: list[dict[str, Any]]) -> float:
    for sp in spans:
        if sp["lo"] <= pos < sp["hi"]:
            frac = (pos - sp["lo"]) / max(sp["hi"] - sp["lo"], 1)
            return sp["t"] + frac * sp["dur"]
    return spans[-1]["t"] + spans[-1]["dur"] if spans else 0.0


def _locate(stream: str, needle: str, cursor: int) -> int | None:
    """规范化文本定位: 全串 → 头 12 字锚; 找不到返回 None."""
    if not needle:
        return None
    p = stream.find(needle, cursor)
    if p >= 0:
        return p
    head = needle[:12]
    if len(head) >= 6:
        p = stream.find(head, cursor)
        if p >= 0:
            return p
    return None


def restamp_slots(doc: dict[str, Any], sent_table: list[dict[str, Any]],
                  tc: float | None = None) -> dict[str, Any]:
    """音频重生成后, 装包制槽镜 (zone/packed) 按新句级表重钉时间 — 文本锚定, 零 LLM.

    0919 "槽位神圣" 的前提是槽=当版句级表; 音频重生成后包时长漂移, 前提失效
    (1001 ep4 实锤: 旧场界把仪式句包切走 89% 给 s99; _realign_narrations 对
    槽镜一律跳过, 对齐落盘修不了 — 全产线无人给槽镜重新代入音频时长)。本函数
    补这一环:
    - 逐镜 (t 序) 在句级 norm 流上顺序定位, norm(镜口播) == 流区间 → 按新区间重钉;
    - 整句槽 (区间两端都落句界): 新时间=句界原值, 同版音频恒等不动;
    - 句内槽 (逗号切): 仅当所在句时长与句内槽现状之和漂移 >0.2s 才按字数比例重拆
      (同版音频保住静默吸附精度, 神圣性在同一版音频内不破);
    - 文本对不上 (口播被改/残切): 该镜跳过不动, 日志点名 — 保守, 不猜。
    非槽镜不归本函数管 (_realign_narrations 管)。组/场包络随镜同步。
    """
    from app.services.anim_pipeline import shots as shots_mod

    if tc is None:
        tc = float(anim_cfg_load().brand_card.opening_sec)
    units = [u for u in (sent_table or [])
             if str(u.get("text") or "").strip()
             and float(u.get("t_end") or 0) > float(u.get("t_start") or 0)]
    # 1010 ep3 实锤: 孤引号/纯符号残句 norm 后为空 → all() 失败让 51 镜重钉
    # 静默退出 — 空norm句剔出字符流 (0.4-0.6s 残句, 不承载锚定), 不再整体弃疗
    _pairs = [(u, shots_mod.norm_for_match(str(u["text"])))
              for u in units]
    _pairs = [(u, nm) for u, nm in _pairs if nm]
    units = [u for u, _ in _pairs]
    norms = [nm for _, nm in _pairs]
    if not units:
        return {"moved": [], "skipped": [], "comma": 0}
    offs: list[int] = []
    acc = 0
    for nm in norms:
        offs.append(acc)
        acc += len(nm)
    stream = "".join(norms)
    edges = set(offs) | {len(stream)}
    # 句号 → 现有句内槽镜 span 和 (漂移判定基准)
    inside_spans: dict[int, float] = {}
    inside_shots: dict[int, list[dict[str, Any]]] = {}

    def char_time(p: int) -> float:
        """流字符位 → 音频轴秒 (句内字符比例插值; p=流长 → 末句终点)。"""
        if p >= len(stream):
            return float(units[-1]["t_end"])
        for i, nm in enumerate(norms):
            if offs[i] <= p < offs[i] + len(nm):
                u = units[i]
                frac = (p - offs[i]) / max(len(nm), 1)
                return float(u["t_start"]) + frac * (float(u["t_end"]) - float(u["t_start"]))
        return float(units[-1]["t_end"])

    def unit_span_at(a: int, b: int) -> tuple[float, float] | None:
        """区间 [a,b) 两端都落句界 → (首句 t_start, 末句 t_end); 否则 None。"""
        if a not in edges or b not in edges:
            return None
        i0 = next(i for i in range(len(units)) if offs[i] == a) if a < len(stream) else len(units) - 1
        i1 = next(i for i in range(len(units)) if offs[i] + len(norms[i]) == b)
        return float(units[i0]["t_start"]), float(units[i1]["t_end"])

    shots = sorted(doc.get("shots") or [], key=lambda s: float(s.get("t_start") or 0))
    slots = [s for s in shots if s.get("zone") or s.get("packed")]
    if not slots:
        return {"moved": [], "skipped": [], "comma": 0}

    # 第一遍: 定位每镜的流区间 [a,b)
    spans: list[tuple[dict[str, Any], int, int] | None] = []
    cur = 0
    for s in slots:
        target = shots_mod.norm_for_match(str(s.get("narration") or ""))
        p = stream.find(target, cur) if target else -1
        if p < 0:
            spans.append(None)
            continue
        spans.append((s, p, p + len(target)))
        cur = p + len(target)
    # 句内槽现状 span 和 (按"区间完整落在单句内"归句); 静音垫量剔除 —
    # 垫量是上一轮轴隙吸收的产物, 计入会把"句长≠槽和"恒判漂移 → 每轮重拆再
    # 吸收, 无限振荡 (s29 系 6 镜每跑必动实锤)
    for ent in spans:
        if ent is None:
            continue
        s, a, b = ent
        for i, nm in enumerate(norms):
            if offs[i] <= a and b <= offs[i] + len(nm):
                inside_spans[i] = inside_spans.get(i, 0.0) + (
                    float(s.get("t_end") or 0) - float(s.get("t_start") or 0)
                    - float(s.get("_silence_pad_s") or 0))
                inside_shots.setdefault(i, []).append(s)
                break

    moved: list[str] = []
    skipped: list[str] = []
    comma_n = 0
    for ent in spans:
        if ent is None:
            skipped.append("?")
            continue
        s, a, b = ent
        sid = str(s.get("shot_id"))
        exact = unit_span_at(a, b)
        if exact is not None:
            new_t0, new_t1 = exact
        else:
            # 句内(或跨句带句内边)槽: 所跨句未漂移 → 不动 (保静默吸附精度);
            # 漂移判双条件 — 时长和 + 句内槽镜端点位置 (只查时长会漏"整体平移")
            drifted = False
            for i, nm in enumerate(norms):
                if not (offs[i] < b and a < offs[i] + len(nm)):
                    continue  # 不相交
                u0, u1 = float(units[i]["t_start"]), float(units[i]["t_end"])
                u_dur = u1 - u0
                if abs(u_dur - inside_spans.get(i, 0.0)) > 0.2:
                    drifted = True
                    break
                _gs = inside_shots.get(i) or []
                if _gs and (abs(min(float(x["t_start"]) for x in _gs) - (u0 + tc)) > 0.2
                            or abs(max(float(x["t_end"]) - float(x.get("_silence_pad_s") or 0)
                                       for x in _gs) - (u1 + tc)) > 0.2):
                    drifted = True
                    break
            if not drifted:
                continue
            new_t0, new_t1 = char_time(a), char_time(b)
            comma_n += 1
        n0, n1 = round(tc + new_t0, 2), round(tc + new_t1, 2)
        # 恒等判定剔静音垫量 (垫是轴隙吸收产物, 非文本时间); 重钉即作废旧垫
        _padcur = float(s.get("_silence_pad_s") or 0)
        if abs(n0 - float(s.get("t_start") or 0)) < 0.05 and \
           abs(n1 - (float(s.get("t_end") or 0) - _padcur)) < 0.05:
            continue  # 同版音频恒等
        s["t_start"], s["t_end"] = n0, n1
        s.pop("_silence_pad_s", None)
        moved.append(sid)
    # 轴隙吸收/重叠让位 (1001): 句间静音单元 (空文本句=气口) 不入字符流, 重钉后
    # 时间轴留洞 → 静音并入前镜 (视频定格过气口, 与"槽含气口"旧轴同构);
    # 逗号分数越界造出的重叠 → 前镜 t_end 让位。只动相邻双槽镜的接缝。
    _skip_ids = {id(slots[j]) for j, ent in enumerate(spans) if ent is None}
    _tiled = 0
    for i in range(len(slots) - 1):
        sa, sb = slots[i], slots[i + 1]
        if id(sa) in _skip_ids or id(sb) in _skip_ids:
            continue
        gap = float(sb["t_start"]) - float(sa["t_end"])
        if abs(gap) <= 0.05:
            continue
        if gap < 0:
            sa["t_end"] = float(sb["t_start"])   # 重叠: 前镜让位
            _tiled += 1
            continue
        lo, hi = float(sa["t_end"]) - tc, float(sb["t_start"]) - tc
        has_text = any(str(u.get("text") or "").strip()
                       for u in (sent_table or [])
                       if float(u.get("t_end") or 0) > lo + 0.01
                       and float(u.get("t_start") or 0) < hi - 0.01)
        if not has_text:                          # 纯静音段 → 并入前镜
            sa["t_end"] = float(sb["t_start"])
            sa["_silence_pad_s"] = round(float(sa.get("_silence_pad_s") or 0) + gap, 2)
            _tiled += 1
    if skipped:
        skipped = [str(s.get("shot_id")) for s, ent in zip(slots, spans) if ent is None]
    # 组包络同步 (只随镜动); 场骨架 (arcs) 不随镜改写 — 骨架是切场+立意人闸的
    # 权威, 镜覆盖度缩骨架会把历史欠账 (品牌句钳断/塌缩剔除) 永久固化 (1004
    # 定位 ep5 实锤: restamp 把 A2 骨架 108.9 缩到 62.2, 场窗并集被循环打回);
    # 场界刷新归 plan_scene splice (arcs[idx] 随重钉后的组界写回)。
    if moved or _tiled:
        by_g: dict[str, list[dict[str, Any]]] = {}
        for s in doc.get("shots") or []:
            if s.get("group_id"):
                by_g.setdefault(s["group_id"], []).append(s)
        for g in doc.get("groups") or []:
            gs = by_g.get(g.get("group_id") or "")
            if gs:
                g["t_start"] = min(float(x["t_start"]) for x in gs)
                g["t_end"] = max(float(x["t_end"]) for x in gs)
    return {"moved": moved, "skipped": skipped, "comma": comma_n}


def align_episode(book_ref: str, ep: int, dry_run: bool = False, stretch: bool = True) -> dict[str, Any]:
    """音画对齐归真: 主线音频实测时长 → 重写 shots t_start/t_end + 漂移审计.

    stretch=True (音频先行令): 未生成视频的镜同时把 anim.beats 按真实音频重排
    (H3 首次生成的时长即正确时长); anim_done 镜不动 beats (视频已在, 重排会失配,
    交给漂移审计人决策重roll)。段超 8s 的镜标 overrun_s (草稿定格吸收或人重切)。
    """
    book_id, book_title = resolve_ep(book_ref, ep)
    audio = fetch_episode_audio(book_id, ep)
    cfg = anim_cfg_load()
    tc = float(cfg.brand_card.opening_sec)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        if str(doc.get("planner_flow", "")).startswith("director2"):
            # v2 时间轴对账归真 (0916: 原生轴的镜切点漂在包内 → 定格补差泛滥;
            # 确定性重跑 _realign_narrations — 切点吸附包边界, 不重规划不花 LLM)
            from app.services.anim_pipeline import director2 as _d2
            # 句级 segs (0916): 老音频巨型多句包 → 句切+停顿吸附, 包边=句边
            segs = _sentence_segs(audio["files"])
            realigned = 0
            split_n = dropped_n = 0
            redesign_n = 0
            duration_problems = 0
            if not dry_run:
                _before = {s["shot_id"]: (s.get("t_start"), s.get("t_end"))
                           for s in doc["shots"]}
                _pre = {x["shot_id"]: x for x in shots_mod.load_doc(book_title, ep)["shots"]}
                # 1001 修: 槽镜 (zone/packed) 先按新句级表重钉 — _realign_narrations
                # 对槽镜一律跳过 (0919 槽位神圣), 音频重生成后全产线无人修其时间
                # (ep4 s99 切走仪式句包 89% 实锤)。同版音频恒等, 神圣性不破。
                _rs = restamp_slots(doc, segs, tc)
                if _rs["moved"]:
                    logger.warning("[align] 音频漂移 → 槽镜重钉 %d 镜 (%s)",
                                   len(_rs["moved"]), ",".join(_rs["moved"][:12]))
                if _rs["skipped"]:
                    logger.warning("[align] 槽镜文本锚失败 %d 镜 (不动, 人检): %s",
                                   len(_rs["skipped"]), ",".join(_rs["skipped"][:12]))
                _ret = _d2._realign_narrations(doc, segs)
                split_n, dropped_n = _ret.get("split", 0), _ret.get("dropped", 0)
                # 0917: 新拆出的分身不是复印机 — 克隆家系当场批量换构图
                # (LLM 失败=保持克隆, collect_warnings 亮灯, 不炸确定性归真)
                redesign_n = 0
                if split_n or _d2._clone_families(doc["shots"]):
                    try:
                        _rr = _d2.redesign_split_clones(doc, doc.get("bible") or {})
                        redesign_n = _rr.get("redesigned", 0)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[align] 拆镜分身重设计失败 (保持克隆, 软警告亮灯): %s", exc)
                # 0917 时长核验 (用户令): 音频↔场窗↔镜槽↔H3窗 对账 + 人读表落盘
                try:
                    _audit = _d2.duration_audit(doc, segs)
                    _d2.write_duration_sheet(doc, _audit)
                    duration_problems = len(_audit.get("problems") or [])
                    if duration_problems:
                        logger.warning("[align] 时长核验 %d 问题 (详见 时长核验.md): %s",
                                       duration_problems, _audit["problems"][:3])
                except Exception as exc:  # noqa: BLE001 — 核验失败不炸归真
                    logger.warning("[align] 时长核验失败 (忽略): %s", exc)
                _st = shots_mod.strip_media_on_narr_change({"shots": list(_pre.values())}, doc)
                if _st:
                    logger.warning("[align] 文案变→媒体失效 %d 镜: %s", len(_st), _st)
                # 状态型自愈 (0916 粉卡差额根治): 槽≠beats 的镜一律重拉 beats;
                # 有视频且槽比 beats 长>0.3s = 出片必然短缺 → 清视频回 approved
                # (图保留, H3 按新 beats 重出). 事件钩子只抓"正在变", 这里抓
                # "既成漂移" (改槽后 beats 没跟的存量病).
                from app.services.anim_pipeline.director2 import H3_MAX_SPAN as _CAP
                _healed = []
                for _s in doc["shots"]:
                    _span = float(_s["t_end"]) - float(_s["t_start"])
                    if not isinstance(_s.get("anim"), dict) or _span <= 0:
                        continue
                    _dur = float(_s["anim"].get("duration_s") or 0)
                    if abs(_span - _dur) <= 0.05:
                        continue
                    shots_mod.stretch_beats(_s, min(round(_span, 2), _CAP))
                    # 品牌镜豁免 (0920): 其视频=定稿资产, 与 beats 时长无关 —
                    # 清了就是又一次"品牌卡被对齐冲掉"; 长度差草稿端定格吸收
                    # 0921 修 s30b 无限打回 (ep6 实锤: 槽10.44/beats10.0 恒差
                    # 0.44s, 每次对齐清视频→重生成还是10.0→再清): beats 已在
                    # H3 上限 (_dur≥CAP) 的槽溢出是设计内 (H3_MAX_SPAN 注释
                    # "超出草稿定格吸收"), 重生成不变长, 清了永不收敛; 只有
                    # 未到上限却槽长出 >0.6s (超草稿微降速带宽, 0918) 才真漂移;
                    # 1001 修: restamp 静音垫量 (_silence_pad_s) 豁免 — 槽长出的
                    # 部分是气口静音, 视频定格可过, 清片=白烧 GPU
                    _pad = float(_s.get("_silence_pad_s") or 0)
                    if (_s.get("video_file") and not _s.get("brand_card")
                            and _dur < _CAP and _span > _dur + 0.6 + _pad):
                        _s.pop("video_file")
                        _s["status"] = "approved"
                        _s.pop("gen_narr_sha", None)
                        _healed.append(f"{_s['shot_id']}(槽{_span:.1f}≠beats{_dur:.1f}s)")
                if _healed:
                    logger.warning("[align] beats 漂移自愈 %d 镜: %s", len(_healed), _healed)
                # 品牌镜: align 一律不碰 (0920 用户令修死循环实锤).
                # 旧 0916 块在此强制"迁移逐镜生成" (video_file 非 anim/ 开头即
                # 判旧固定资产 → 注入骨架+清素材+回 planned) — 与现行"定稿资产
                # 直通"设计打架: 🏔生成品牌卡 → 对齐落盘打回 → 再生成 → 再打回,
                # 永不收敛. 其立项动机 (5s 资产放长槽=差额裸奔) 已由草稿端根治
                # (仪式句锚定 + 前后段 keyframe 定格补差), 此块只剩破坏力, 删.
                # 品牌镜全生命周期: k2/h3 零 GPU 直通插资产 (见 shots.tag_brand_cards),
                # 草稿端兜底链 本书资产→全局资产→占位卡.
                realigned = sum(1 for s in doc["shots"]
                                if _before.get(s["shot_id"]) != (s.get("t_start"), s.get("t_end")))
                shots_mod.save(doc)
            # v2 时间轴体检: 轴隙/覆盖/时长窗硬校验 + 真实集长核对 + 音效清单刷新
            issues = _d2.validate_doc(doc)
            ordered = sorted(doc["shots"], key=lambda s: float(s["t_start"]))
            gaps = [{"from": ordered[i - 1]["shot_id"], "to": ordered[i]["shot_id"],
                     "gap_s": round(float(ordered[i]["t_start"]) - float(ordered[i - 1]["t_end"]), 2)}
                    for i in range(1, len(ordered))
                    if abs(float(ordered[i]["t_start"]) - float(ordered[i - 1]["t_end"])) > 0.5]
            # 真实集长 = 非空包终点 (空文本包幻影 z26 教训: 裸加会把覆盖差撑飞)
            manifest_end = tc + sum(float(f.get("dur_s") or 0) for f in audio["files"]
                                    if str(f.get("text") or "").strip())
            last_end = float(ordered[-1]["t_end"])
            coverage = round(last_end - manifest_end, 2)
            cue_n = 0
            if not dry_run:
                voice = shots_mod.ep_dir(book_title, ep) / "voice_full.wav"
                if not voice.exists():
                    concat_voice(audio["files"], voice)
                try:
                    cues = _sound_cue_manifest(doc)
                    (shots_mod.ep_dir(book_title, ep) / "音效提示.md").write_text(cues, encoding="utf-8")
                    cue_n = max(cues.count(chr(10)) - 4, 0)
                except Exception:  # noqa: BLE001 — 清单失败不炸体检
                    pass
            return {"planner_flow": doc.get("planner_flow"), "audio_job": audio["job_id"],
                    "ep_len_s": round(last_end, 1), "realigned_shots": realigned,
                    "split_shots": split_n, "dropped_shots": dropped_n,
                    "redesign_clones": redesign_n, "duration_problems": duration_problems,
                    "stats": {"镜数": len(ordered), "硬校验问题": len(issues),
                              "轴隙": len(gaps), "尾部覆盖差s": coverage,
                              "音效点": cue_n},
                    "issues": issues[:6], "gaps": gaps[:6], "dry_run": dry_run}

        base = shots_mod.ep_dir(book_title, ep)
        stream, spans, total_audio = _char_time_map(audio["files"])
        if not stream:
            raise AnimNotFound("音频包文本全空, 无法对齐")
        rate = total_audio / max(len(stream), 1)  # 全局 字符/秒 兜底估值用

        shots = sorted(doc["shots"], key=lambda s: float(s.get("t_start", 0)))
        cursor = 0
        rows: list[dict[str, Any]] = []
        for s in shots:
            norm = norm_for_match(s.get("narration", ""))
            if not norm:
                rows.append({"shot_id": s["shot_id"], "matched": False, "class": "无口播"})
                continue
            p = _locate(stream, norm, cursor)
            if p is None:
                # 兜底: 从当前 cursor 起按字符比例估 (LLM 改写/漏配), 审计里标出;
                # 近似推进 cursor 防下一镜匹配落进本镜区间 (时间轴重叠)
                t0, t1 = _char_to_time(cursor, spans), _char_to_time(cursor + len(norm), spans)
                cursor += len(norm)
                matched = False
            else:
                t0, t1 = _char_to_time(p, spans), _char_to_time(p + len(norm), spans)
                cursor = p + len(norm)
                matched = True
            if "t_start_orig" not in s:
                s["t_start_orig"], s["t_end_orig"] = s.get("t_start"), s.get("t_end")
            dur = t1 - t0
            s["t_start"] = round(tc + t0, 2)
            s["t_end"] = round(tc + t1, 2)
            s["audio_dur_s"] = round(dur, 3)
            # 音频先行令: 未生成视频的镜 beats 按真实音频重排 — H3 首次生成即正确时长
            if not dry_run and stretch and s["status"] != "anim_done" and dur > 0.3:
                seg = min(round(dur, 2), 8.0)  # H3 单镜上限 8s, 超出部分草稿定格吸收
                if abs(float((s.get("anim") or {}).get("duration_s", 0)) - seg) > 0.05:
                    shots_mod.stretch_beats(s, seg)
                if dur > 8.05:
                    s["overrun_s"] = round(dur - 8.0, 2)  # 供人决策: extend 重切 or 定格吸收
                else:
                    s.pop("overrun_s", None)
            old_span = (s["t_end_orig"] or 0) - (s["t_start_orig"] or 0)
            drift = dur - old_span
            if dur > 8.0:
                cls = "段超8s"
            elif abs(drift) > 0.5:
                cls = "H3重跑"
            elif abs(drift) > 0.25:
                cls = "吸收"
            else:
                cls = "OK"
            if not matched and cls == "OK":
                cls = "未匹配"
            rows.append({"shot_id": s["shot_id"], "matched": matched, "class": cls,
                         "old_span": round(old_span, 2), "audio_span": round(dur, 2),
                         "drift": round(drift, 2),
                         "t_start": s["t_start"], "t_end": s["t_end"]})

        ep_len = tc + total_audio
        stats: dict[str, int] = {}
        for r in rows:
            stats[r["class"]] = stats.get(r["class"], 0) + 1
        report = {"audio_job": audio["job_id"], "packages": len(audio["files"]),
                  "audio_total_s": round(total_audio, 1), "ep_len_s": round(ep_len, 1),
                  "title_card_s": tc, "stats": stats, "shots": rows, "dry_run": dry_run}
        (base / "align_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        if not dry_run:
            shots_mod.save(doc)
            concat_voice(audio["files"], base / "voice_full.wav")
        logger.info("[align] %s ep%d: 口播 %.0fs, 全集成片 ≈ %.0fs, %s%s",
                    book_title, ep, total_audio, ep_len, stats,
                    " (dry-run 未落盘)" if dry_run else "")
        return report


# ── 剪映草稿装配 ─────────────────────────────────────────────

def _resolve_typing_text(book_id: str, ep: int) -> str:
    """打字卡文案 (书级冻结): input_json.系列打字卡.card > roadmap_json.打字卡
    > 稿内 '打字卡:' 行 > 书名占位.

    0918 实锤: ep2 roadmap/稿内两源皆空 → return "" → typing 轨整轨丢失
    (抖音前 2s 无字无打字音效)。书级冻结卡真相源 = input_json['系列打字卡']
    (ep1 自产回填), 补为第一优先级; 末端书名占位兜底 (docstring 曾承诺未实现)。"""
    con = sqlite3.connect(anim_cfg_load().db_path)
    try:
        r = con.execute(
            "select e.roadmap_json, e.script_text, p.input_json from book_episodes e "
            "join book_projects p on p.id = e.book_id where e.book_id=? and e.ep_index=?",
            (book_id, ep)).fetchone()
    finally:
        con.close()
    if r:
        try:
            card = (json.loads(r[2] or "{}").get("系列打字卡") or {})
            if isinstance(card, dict) and card.get("card"):
                return str(card["card"]).strip().strip("。")
        except Exception:  # noqa: BLE001
            pass
        try:
            rm = json.loads(r[0] or "{}")
            if rm.get("打字卡"):
                return str(rm["打字卡"]).strip().strip("。")
        except Exception:  # noqa: BLE001
            pass
        m = re.search(r"打字卡[:：]\s*(.+)", r[1] or "")
        if m:
            return m.group(1).strip().strip("。")
    return ""


def _placeholder_card(shot: dict[str, Any], out: Path, label: str = "待生成") -> None | Path:
    """缺片占位卡 (拼图彩排): 粉底动画系 + 镜号/标签/时间/口播摘要.

    label (0916 文案说真话令): 整镜缺="待生成"; 差额补="片尾缺X.Xs·视频有";
    品牌内容段="内容段缺X.Xs·品牌卡在本镜". 防误读为没生成."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1920, 1080), "#E91E8C")
    d = ImageDraw.Draw(img)
    font_dir = Path(r"C:\Windows\Fonts")
    fp = font_dir / ("msyhbd.ttc" if (font_dir / "msyhbd.ttc").exists() else "msyh.ttc")
    f_big = ImageFont.truetype(str(fp), 96)
    f_sml = ImageFont.truetype(str(fp), 44)
    d.text((80, 80), f"{shot['shot_id']}  {label}", font=f_big, fill="#FFFFFF")
    ts = f"{shot['t_start']:.0f}-{shot['t_end']:.0f}s ({shot['t_end'] - shot['t_start']:.0f}s)"
    d.text((960 - d.textlength(ts, font=f_sml) / 2, 420), ts, font=f_sml, fill="#FFFFFF")
    line = (shot.get("narration") or "")[:26]
    d.text((960 - d.textlength(line, font=f_sml) / 2, 560), line, font=f_sml, fill="#FFD9EC")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _shot_material(shot: dict[str, Any], base: Path, cards_dir: Path,
                   kinds: dict[str, str]) -> list[tuple[Path, bool]]:
    """镜素材解析 (真片优先): [(素材, 是否视频), ...] 按播放顺序.

    0916 用户令: 未生成留空 (不垫占位卡/定格) — 返回 [] 即空槽."""
    sid = shot["shot_id"]
    if shot.get("brand_card"):
        # 0916 终版教义落地 (0917 用户实锤"品牌卡差额卡补"复发): 品牌镜逐镜生成 —
        # 满槽生成片优先; 无生成片才回退固定品牌资产 (主循环回退分支管仪式句锚定,
        # 能走到这里 = 有生成片, 旧路由无条件换固定资产 = 把生成成果扔了)。
        if shot.get("video_file") and (base / shot["video_file"]).exists():
            kinds[sid] = "real:mp4"
            return [(base / shot["video_file"], True)]
        from app.services.anim_pipeline import brand as brand_mod
        _bk = brand_mod.brand_paths(base.parent.name)  # base = .../动画/{书}/ep{n}
        card = _bk["video"] if _bk["video"].exists() else Path(anim_cfg_load().brand_card.video)
        kinds[sid] = "brand_card"
        return [(card, True)]
    if shot.get("video_file") and (base / shot["video_file"]).exists():
        kinds[sid] = "real:mp4"
        return [(base / shot["video_file"], True)]
    if shot.get("image_file") and (base / shot["image_file"]).exists():
        kinds[sid] = "still:img"
        return [(base / shot["image_file"], False)]
    kinds[sid] = "gap_card"
    return [(_placeholder_card(shot, cards_dir / f"{sid}.png"), False)]


def _prep_opening_assets(book_title: str, base: Path) -> tuple[Path, Path, list[str]]:
    """书揭示件备料 (零 GPU): 书库 epub 提封面 — S00c 书封墙 + S00d 主书封.

    拆书线 storyboard_draft.prep_book_wall 同源 (封面链: 本书 epub → 书库其他书
    epub 拼墙); 提不出走占位卡。飞入/扫描/收敛动画与音效 = 剪映后期位。
    """
    open_dir = base / "opening"
    s00c, s00d = open_dir / "S00c.png", open_dir / "S00d.png"
    if s00c.exists() and s00d.exists():
        return s00c, s00d, ["复用已有备料"]
    open_dir.mkdir(parents=True, exist_ok=True)  # prep_book_wall 只写不建目录
    notes: list[str] = []
    try:
        from app.config import load_config, set_config
        set_config(load_config())
        src_dir = Path(load_config().defaults.book_source_dir)
    except Exception:  # noqa: BLE001 — config 链不在则用拆书线同款兜底路径
        src_dir = Path("G:/Desktop/畅销书")
    try:
        from scripts.storyboard_draft import prep_book_wall  # 拆书线同源备料
        notes = list(prep_book_wall(open_dir, book_title, src_dir))
    except Exception as exc:  # noqa: BLE001
        notes = [f"备料失败: {exc}"]
        logger.warning("[draft] 书揭示件备料失败: %s", exc)
    for p in (s00c, s00d):
        if not p.exists():
            _placeholder_card({"shot_id": p.stem, "t_start": 0, "t_end": 1.5,
                               "narration": f"书揭示件占位 {book_title}"}, p)
            notes.append(f"{p.stem} 占位卡")
    return s00c, s00d, notes


_STILL_CACHE = "still_mp4"  # ep 目录下的转换缓存夹


def _as_video_path(mat_path: Path, base: Path, dur_s: float) -> Path:
    """静图 → 短 mp4 (0915 剪映迁移丢图实锤: 视频轨上的 PNG 在新版剪映打开时被丢弃 —
    库无 PhotoSegment, 转视频是跨版本免疫解)。mp4 原样返回; 缓存于 ep/still_mp4/。"""
    if mat_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
        if mat_path.name.isascii():
            return mat_path
        # 已有视频但中文文件名 (品牌卡等直通件): 硬链接到 ASCII 缓存名, 免重编码
        # (哈希含后缀: 同词干 png 转换件与 mp4 直通件名字必须不同, 否则互相顶替)
        cache0 = base / _STILL_CACHE
        cache0.mkdir(parents=True, exist_ok=True)
        ln = cache0 / ("bg_" + hashlib.md5(
            (mat_path.stem + mat_path.suffix.lower()).encode("utf-8")).hexdigest()[:10]
            + mat_path.suffix.lower())
        if not ln.exists():
            try:
                os.link(mat_path, ln)
            except OSError:
                shutil.copy2(mat_path, ln)
        return ln
    cache = base / _STILL_CACHE
    cache.mkdir(parents=True, exist_ok=True)
    # 缓存键掺源文件指纹 (0916 卡片撞车根治): 同名不同内容的源 (重出的图/换文案的卡/
    # 旧规划的卡) 会互相顶包 — s27 旧"待生成"卡顶掉新图实锤. 指纹变 → 缓存名变,
    # 永不串档; 同词干旧档 (含无指纹旧命名) 一律清场重转, 不做迁移 (迁移无法证明
    # 旧内容=当前源, s27 教训).
    _src = mat_path.stat()
    # 时长进缓存键 (0919 开场人声链实锤: 无声稿 reveal 窗 2.0s 转的缓存件, 人声上线
    # 后窗口变 2.8s 仍命中旧件 → VideoSegment 截取越界炸整稿)
    _fp = hashlib.md5(
        f"{mat_path.stem}{_src.st_mtime_ns}{_src.st_size}{round(dur_s, 2)}".encode()).hexdigest()[:8]
    out = cache / f"{mat_path.stem}_{_fp}.mp4"
    for _old in cache.glob(f"{mat_path.stem}*.mp4"):
        if _old != out:
            try:
                _old.unlink(missing_ok=True)
            except OSError:
                # 1010: 剪映开着旧草稿握着素材句柄 (WinError 32) — 清场失败不炸
                # 装配, 旧档留着 (缓存键含指纹, 不会被误命中)
                logger.warning("[draft] 旧卡档被占用跳过清理: %s", _old.name)
    if not out.exists():
        r = subprocess.run(
            # +0.6s 余量: 帧量化使实际时长略短于 -t, 定格段源范围越界会炸整稿
            # 0915 定案: 编码档案完全仿 VHS 产出 (s27 实测) — crf19/24fps/bt709+tv 全标, 缺颜色元数据的件被剪映迁移整段丢弃
            ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{max(dur_s, 1.0) + 0.6:.2f}",
             "-i", str(mat_path),
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # 奇数尺寸偶数化 (S00d 1080x1543 实锤: libx264 拒编)
             "-c:v", "libx264", "-preset", "medium", "-crf", "19",
             "-pix_fmt", "yuv420p", "-r", "24",
             "-colorspace", "bt709", "-color_range", "tv",
             "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
             "-movflags", "+faststart",
             "-an", str(out)],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not out.exists():
            logger.warning("[draft] 静图转视频失败 %s: %s", mat_path.name, r.stderr[:120])
            return mat_path  # 失败退回原图 (旧行为)
        # 产物校验 (半截烂卷实锤: 中断残留的 moov 缺失文件会被缓存复用)
        _ok = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=codec_type", "-of", "csv=p=0", str(out)],
                             capture_output=True, text=True, timeout=30)
        if "video" not in (_ok.stdout or ""):
            logger.warning("[draft] 转换产物无视频轨 %s — 删残卷退回原图", out.name)
            out.unlink(missing_ok=True)
            return mat_path
    return out


def _sound_cues(doc: dict) -> list[dict[str, Any]]:
    """R9 同帧音效事件流: 大字出场帧/场转场 → 类别 + 库内实存候选 (轮换防腻).

    音效对齐强调大字出现帧 (45期律, 误差<10ms); 类别映射 kind/内容 → jy_sound_semantics
    13 类; 候选按出现序轮换且**只列库内实存文件** (data/jy_sounds, 语义库 15/55 名字
    无文件 — 0816 待收割, 过滤后清单与自动挂永不指空)。清单导出与自动挂轨共用此流。
    """
    import json as _json
    from app.config import PROJECT_ROOT as _root
    from app.services.jy_draft_service.sfx import sound_path
    try:
        sem = _json.loads((_root / "config" / "jy_sound_semantics.json")
                          .read_text(encoding="utf-8")).get("categories", {})
    except Exception:  # noqa: BLE001 — 清单尽力而为
        sem = {}

    def classify(kind: str, text: str, color: str = "") -> str:
        if kind == "typewriter":
            return "typing"
        if kind == "title":
            return "title_in"
        if kind == "impact" or (kind == "overlay" and color == "red"):
            return "suspense_hook" if any(c in text for c in "？？?") else "impact"
        if kind == "year":
            return "punchline"  # 年份=历史标记, 点题感而非收银机
        if any(ch.isdigit() for ch in text):
            return "money"
        return "punchline"

    def candidates(cat: str, idx: int) -> list[str]:
        sounds = (sem.get(cat) or {}).get("sounds") or []
        pick = [s if isinstance(s, str) else str(s.get("name", s)) for s in sounds]
        avail = [n for n in pick if sound_path(n)]
        return [avail[(idx + i) % len(avail)] for i in range(min(3, len(avail)))] if avail else []

    events: list[dict[str, Any]] = []
    idx = 0
    prev_arc = None
    for s in sorted(doc["shots"], key=lambda x: float(x["t_start"])):
        arc = str(s.get("arc_id") or "")
        if prev_arc is not None and arc != prev_arc:
            events.append({"at": float(s["t_start"]), "kind": f"转场·新场 {arc}",
                           "text": arc, "cat": "transition_soft",
                           "cands": candidates("transition_soft", idx)})
            idx += 1
        prev_arc = arc
        for tl in s.get("text_layer") or []:
            kind = str(tl.get("kind") or "caption")
            if kind not in ("hero_number", "year", "impact", "title", "typewriter", "overlay"):
                continue  # caption=正文字幕, 不挂强调音效
            text = str(tl.get("text") or "")
            cat = classify(kind, text, str(tl.get("color") or ""))
            events.append({"at": float(s["t_start"]) + float(tl.get("t_start") or 0),
                           "kind": kind, "text": text, "cat": cat,
                           "cands": candidates(cat, idx)})
            idx += 1
    return events


def _sound_cue_manifest(doc: dict) -> str:
    """音效提示.md (人工兜底清单): _sound_cues 事件流 → markdown."""
    lines = ["# 同帧音效提示（R9 协同 · 大字出现帧挂音效）", "",
             "> 音效对齐**强调大字出现帧**（非句首）; 同类内轮换选音防腻。语义库: config/jy_sound_semantics.json",
             "> 草稿已自动挂 sfx 轨; 本清单为核对/人工兜底用。", ""]
    for ev in _sound_cues(doc):
        lines.append(f"- `{ev['at']:7.2f}s` [{ev['kind']}] **{ev['text'][:24]}** → {ev['cat']}: "
                     + (" / ".join(ev["cands"]) if ev["cands"] else "(库缺)"))
    return chr(10).join(lines) + chr(10)


def _attach_sound_cues(script: Any, cues: list[dict[str, Any]]) -> dict[str, int]:
    """音效自动挂轨 (J线 attach_sound 平移, 0916): 首选轮换候选 → sfx 音频轨.

    密度闸门 = J线 R9 v3 口径: 前 30s 全类别, 之后全局间隔 ≥4s 防腻 (闸门管整个
    事件, 与 J线 _auto_choreograph 同律)。缺文件/重叠由 attach_sound 自行跳过。
    """
    from app.services.jy_draft_service.sfx import attach_sound
    stats = {"sfx": 0, "sfx_skip_density": 0, "sfx_skip_missing": 0}
    last_at: float | None = None
    for ev in cues:
        at = float(ev["at"])
        if last_at is not None and at - last_at < 4.0 and at > 30.0:
            stats["sfx_skip_density"] += 1
            continue
        if not ev["cands"]:
            stats["sfx_skip_missing"] += 1
            continue
        if _attach_sfx(script, ev["cands"][0], at):
            stats["sfx"] += 1
            last_at = at
        else:
            stats["sfx_skip_missing"] += 1
    return stats


# ── 开场人声链 (0916 用户令: 老谭解说 "今天我们要拆解的是【停顿】《书名》") ──
_INTRO_LINE1 = "今天我们要拆解的是"
_INTRO_SFX_DROP = "一滴水滴声"
_INTRO_SFX_GEAR = "发条旋钮转动齿轮"
_INTRO_SPEED = 1.5
NL_ = chr(10)  # 文案 sidecar 换行 (heredoc 转义陷阱回避)  # 0916 用户令: 语速 1.5 倍 (剪映同款口径)
_SFX_TARGET_LUFS = -18.0  # 0916 统一音量令: 全部音效响度归一到此 (只衰减不放大)


def _sfx_vol(name: str, base: float = 0.9) -> float:
    """响度归一音量: 实测 LUFS → 只衰减到目标 (Victory 过热实锤; 测不出回退 base)."""
    from app.services.jy_draft_service.sfx import sound_loudness
    try:
        lufs = sound_loudness(name)
    except Exception:  # noqa: BLE001
        lufs = None
    if lufs is None:
        return base
    gain = 10 ** ((_SFX_TARGET_LUFS - lufs) / 20)
    return max(min(base * gain, base), 0.05)


def _attach_sfx(script: Any, name: str, at: float, max_sec: float | None = None) -> bool:
    """动画线统一挂音效口: attach_sound + 响度归一音量 (0916 统一音量令)."""
    from app.services.jy_draft_service.sfx import attach_sound
    return attach_sound(script, "sfx", name, at, volume=_sfx_vol(name), max_sec=max_sec)


def _opening_asset_paths(book_title: str) -> tuple[Path, Path]:
    """/_资产/开场白_{书}_{1,2}.wav (书级素材, 0920 用户令: wav 也是素材)."""
    s = shots_mod._safe(book_title)
    base = shots_mod.anim_output_root() / "_资产"
    return base / f"开场白_{s}_1.wav", base / f"开场白_{s}_2.wav"


def _intro_voice(book_title: str, base: Path) -> dict[str, Any] | None:
    """开场两段人声 = 书级素材读取 (0920 用户令: 同图书图片路数).

    装配只读 _资产/开场白_{书}_{1,2}.wav, **不现场合成** (旧径每集装配期现调
    IndexTTS2.5, 服务未在线即静默缺段 — ep4 装配实锤两连). 缺素材 → None +
    明确日志; 生成走 produce_opening (POST /api/anim/ep/{book}/{ep}/opening,
    书级一次全系列复用). 落位不变: 拆解句 @3.00 书墙首帧, 书名 @ 滴声半程.
    """
    v1, v2 = _opening_asset_paths(book_title)
    if not (v1.exists() and v2.exists()):
        logger.warning("[draft] 开场白素材缺失: %s / %s — POST /api/anim/ep/{book}/{ep}/opening "
                       "生成 (书级一次, 全系列复用)", v1.name, v2.name)
        return None
    return {"v1": v1, "v2": v2, "d1": _wav_duration(v1), "d2": _wav_duration(v2)}


def produce_opening(book_title: str) -> dict[str, str]:
    """开场白书级素材生产 (幂等; 一次生成全系列复用 — 与品牌卡同路数).

    源优先级: ① _资产已有 → 原样返回 ② 迁移: 各集 ep*/opening/ 缓存里最新
    一集的成品 (intro_{1,2}t.f.wav) 拷入 _资产 ③ IndexTTS2.5 现合成 (经 gpu
    manager session 自动拉起) → 双向裁静音 → atempo 1.5× → 落 _资产.
    附文案 sidecar 开场白_{书}_文案.txt 留痕. 返回 {1,2,source}.
    """
    v1, v2 = _opening_asset_paths(book_title)
    if v1.exists() and v2.exists():
        return {"1": str(v1), "2": str(v2), "source": "cached"}
    safe = shots_mod._safe(book_title)
    work = shots_mod.anim_output_root() / "_资产" / f"开场白_{safe}_中间产物"
    # ② 迁移: 各集 opening 缓存 (老集已合成过, 不重烧 TTS)
    cands = sorted((shots_mod.anim_output_root() / safe).glob("ep*/opening/intro_1t.f.wav"),
                   key=lambda x: x.stat().st_mtime, reverse=True)
    if cands:
        import shutil as _sh
        work.mkdir(parents=True, exist_ok=True)
        _sh.copy2(cands[0], work / "intro_1t.f.wav")
        c2 = cands[0].with_name("intro_2t.f.wav")
        if c2.exists():
            _sh.copy2(c2, work / "intro_2t.f.wav")
        src = "migrated"
    else:
        # ③ 现合成 (生产步骤才碰 TTS; 装配永不)
        work.mkdir(parents=True, exist_ok=True)
        from app.services.anim_pipeline import tts as T
        from app.services.anim_pipeline.config import load
        tcfg = load().tts
        if not T._server_ok(tcfg.base_url):
            try:
                from app.services.gpu_service_manager import get_gpu_service_manager
                with get_gpu_service_manager().session("indextts25"):
                    pass
            except Exception as exc:  # noqa: BLE001 — 拉起尽力
                logger.warning("[opening] IndexTTS2.5 自动拉起失败: %s", exc)
        if not T._server_ok(tcfg.base_url):
            raise RuntimeError("IndexTTS2.5 (7866) 未在线, 开场白无法合成")
        for name, text in (("intro_1", _INTRO_LINE1), ("intro_2", book_title)):
            raw = work / f"{name}.wav"
            if not raw.exists():
                T._synth_one(T.tts_input(text, tcfg.pinyin_map), raw, tcfg)
            trimmed = work / f"{name}t.wav"
            if not trimmed.exists() and raw.exists():
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
                     "-af", "silenceremove=start_periods=1:start_threshold=-38dB:"
                            "start_silence=0.08,areverse,silenceremove=start_periods=1:"
                            "start_threshold=-38dB:start_silence=0.15,areverse",
                     str(trimmed)], capture_output=True, timeout=60)
            # 0924 修: atempo 加工步漏了 — 合成路径只到 trimmed, 没产 .f.wav 最终版
            final = work / f"{name}t.f.wav"
            if not final.exists() and trimmed.exists():
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(trimmed),
                     "-af", f"atempo={_INTRO_SPEED}", str(final)],
                    capture_output=True, timeout=60)
        src = "synthesized"
    import shutil as _sh
    for i in (1, 2):
        src_f = work / f"intro_{i}t.f.wav"
        dst = v1 if i == 1 else v2
        if not src_f.exists():
            raise RuntimeError(f"开场白中间产物缺失: {src_f}")
        _sh.copy2(src_f, dst)
    sidecar = v1.with_name(v1.name.replace("_1.wav", "_文案.txt"))
    sidecar.write_text(_INTRO_LINE1 + NL_ + f"《{book_title}》" + NL_ +
                       f"语速 atempo {_INTRO_SPEED}" + NL_, encoding="utf-8")
    logger.info("[opening] 开场白素材 ✓ (source=%s): %s / %s", src, v1.name, v2.name)
    return {"1": str(v1), "2": str(v2), "source": src}


def _sfx_dur(name_or_path: str) -> float:
    """音效名或媒体路径 → 实测时长 (ffprobe)."""
    from app.services.jy_draft_service.sfx import sound_path
    p = Path(name_or_path)
    if not p.exists():
        p = sound_path(name_or_path)
    if not p:
        return 1.0
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 1.0


def build_draft(book_ref: str, ep: int, with_title_card: bool = True) -> dict[str, Any]:
    """装配剪映草稿: 42镜时间轴 + 品牌卡 + text_layer 字幕 + 整集口播."""
    book_id, book_title = resolve_ep(book_ref, ep)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        base = shots_mod.ep_dir(book_title, ep)
        cfg = anim_cfg_load()
        shots = sorted(doc["shots"], key=lambda s: float(s.get("t_start", 0)))
        if not shots:
            raise AnimNotFound("shots 为空")
        # 0920 ep5 实锤 (半片装配): 骨架 A6/A7 分镜续跑半途停 → 全天在半片上
        # K2/H3, 装配照跑 → 成片尾部 81s 只有口播+字幕没有画面。骨架场无镜 =
        # 硬错拦下 (时长核验早就在 warn, 但装配不读它)。补齐走 ▶️分镜续跑
        # (跳过已有镜的场, 只填空洞)。
        _have = {s.get("arc_id") for s in shots}
        _empty = [str(a.get("arc_id") or f"#{i}")
                  for i, a in enumerate(doc.get("arcs") or [])
                  if a.get("arc_id") not in _have]
        if _empty:
            raise RuntimeError(
                f"骨架场 {'、'.join(_empty)} 无镜 — 这些场的口播将只有声音没有画面。"
                "先 ▶️分镜续跑 补齐 (只填空洞, 不动已有镜), 装配已拦下")
        # 已对齐判据: v1 归真过 (t_start_orig) 或 v2 原生 manifest 轴 (含 opening 预留)
        aligned = (any("t_start_orig" in s for s in shots)
                   or str(doc.get("planner_flow", "")).startswith("director2"))
        if not aligned:
            logger.warning("[draft] 尚未对齐 (无 t_start_orig), 用规划轴装配 — 建议先跑 align")
        # 归真轴已含 title_card 偏移 → 标题卡与 voice 起点都锚在 tc;
        # 未对齐时规划轴无 tc 预留, 强加会与 s01 重叠 — 标题卡只随对齐出现
        with_title_card = aligned
        # ── 开场链 v3 预计算 (0916 人声令): 滚动+滴声+人声 → tc 推轴 ──
        # 滚动 3.0→rev_start (齿轮音对齐滚动) → reveal+滴声; 书名 @ rev_start+
        # 滴声半程; 拆解句收在书名前; 书名念完 +0.5s 静止 = 新 tc (全镜平移).
        from app.services.anim_pipeline import covers as covers_mod
        tc0 = float(cfg.brand_card.opening_sec)
        intro = _intro_voice(book_title, base) if with_title_card else None
        scroll = covers_mod.render_wall_scroll(book_title) if with_title_card else None
        scroll_s = _sfx_dur(str(scroll)) if scroll else 0.0
        rev_start = 3.0 + scroll_s if scroll else 4.0
        drop_half = _sfx_dur(_INTRO_SFX_DROP) / 2
        t_shift = 0.0
        if intro:
            new_tc = rev_start + drop_half + intro["d2"] + 0.5
            t_shift = round(new_tc - tc0, 2)
        if t_shift:
            shots = [{**s, "t_start": round(float(s["t_start"]) + t_shift, 2),
                      "t_end": round(float(s["t_end"]) + t_shift, 2)} for s in shots]
            doc = {**doc, "shots": shots}  # 音效 cue/字幕事件同轴平移 (装配层, 不动 shots.json)
        # 口播包 + 句级 segs (品牌卡仪式句锚定用; voice 段复用) + tc 提前算
        try:
            audio = fetch_episode_audio(book_id, ep)
        except (AnimNotFound, RuntimeError):
            audio = {"files": []}
        _sent_segs = _sentence_segs(audio["files"]) if audio["files"] else []
        tc_us = int((float(cfg.brand_card.opening_sec) + t_shift) * _US)

        import os
        drafts_dir = Path(os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "JianyingPro", "User Data", "Projects", "com.lveditor.draft"))
        # 0920 用户令 (ep5 实锤): 时间戳后缀 = 每次装配新增一份草稿, 剪映列表
        # 层积 (ep1-4 每集只装配一次未暴露; ep5 迭代日装配 5 次 = 5 份)。
        # 定名 = 同名覆盖, 列表里每集始终一份。剪映正开着该稿时文件被锁,
        # rmtree 会炸 → 退回时间戳名 (旧稿保留, 不阻塞装配)。
        name = f"{book_title}_动画_ep{ep}"
        folder = draft_mod.DraftFolder(str(drafts_dir))
        try:
            script = folder.create_draft(name, 1920, 1080, allow_replace=True)
        except OSError as exc:  # noqa: BLE001 — 剪映占用/半加密目录锁
            name = f"{name}_{datetime.now().strftime('%m%d_%H%M')}"
            logger.warning("[draft] 同名草稿被占用 (剪映开着? %s), 改用新名: %s", exc, name)
            script = folder.create_draft(name, 1920, 1080, allow_replace=True)
        script.append_tracks([
            draft_mod.TrackSpec(draft_mod.TrackType.video, "main"),
            draft_mod.TrackSpec(draft_mod.TrackType.video, "book"),  # 主书封层 (压暗墙底之上)
            draft_mod.TrackSpec(draft_mod.TrackType.text, "caption"),
            draft_mod.TrackSpec(draft_mod.TrackType.text, "hero"),  # 标题层 (title/logo_note; 强调词已降维进字幕)
            draft_mod.TrackSpec(draft_mod.TrackType.text, "typing"),
            draft_mod.TrackSpec(draft_mod.TrackType.audio, "voice"),
            draft_mod.TrackSpec(draft_mod.TrackType.audio, "sfx"),  # R9 同帧音效 (attach_sound 平移)
            draft_mod.TrackSpec(draft_mod.TrackType.effect, "fx"),
        ])
        cards_dir = drafts_dir / name / "cards"
        entry_mismatch: list[str] = []  # 错配/截断审计 (循环内累计, 收尾入账本)
        kinds: dict[str, str] = {}
        warn_short: list[str] = []
        last_end_us = 0

        for s in shots:
            slot_us = max(int(round((float(s["t_end"]) - float(s["t_start"])) * _US)), 200_000)
            start_us = int(round(float(s["t_start"]) * _US))
            # 时间轴毛刺防御 (对齐兜底/数据脏): 镜重叠则顺延, 不让 SegmentOverlap 炸整稿
            if start_us < last_end_us:
                start_us = last_end_us
            slot_end_us = start_us + slot_us
            last_end_us = max(last_end_us, slot_end_us)
            # ── 品牌卡镜 (0916 仪式句锚定): 「我是老谭」句在镜内的时位 = 品牌卡位.
            # 首句型 (s07) 品牌卡铺镜首; 尾句型 (s18 收尾仪式) 先填色卡后品牌收尾 —
            # 修复"缺失卡和品牌卡颠倒"实锤. ──
            if s.get("brand_card") and not (
                    s.get("video_file") and (base / str(s["video_file"])).exists()):
                # 0916 品牌镜逐镜生成: 有生成视频走普通素材路径 (下方 continue 跳过);
                # 无则回退链: 本书品牌首帧定格 → 品牌片 @仪式句 → 全局资产 → 卡
                from app.services.anim_pipeline import brand as brand_mod
                _bk = brand_mod.brand_paths(base.parent.name)
                bcard = _bk["video"] if _bk["video"].exists() else Path(cfg.brand_card.video)
                kinds[s["shot_id"]] = "brand_card"
                used = 0
                rit_us = None
                for sg in _sent_segs:
                    if "我是老谭" in sg["text"]:
                        _rt = tc_us + int(sg["t_start"] * _US)
                        if start_us - 500_000 <= _rt < slot_end_us:
                            rit_us = _rt
                            break
                _kf = _bk["keyframe"] if _bk["keyframe"].exists() else Path(cfg.brand_card.keyframe)
                if rit_us is not None and rit_us > start_us + 1_000_000:
                    pre = rit_us - start_us  # 仪式句前的内容位: 品牌定稿首帧定格
                    # (0916 圆满令: 零粉卡 — 内容段用品牌 keyframe 画面, 与品牌片同世界)
                    _fill = _kf if _kf.exists() else _placeholder_card(
                        s, cards_dir / f"{s['shot_id']}_gap.png",
                        label=f"内容段缺{pre / _US:.1f}s·品牌卡在本镜")
                    script.add_segment(draft_mod.VideoSegment(
                        draft_mod.VideoMaterial(str(_as_video_path(_fill, base, pre / _US))),
                        trange(start_us, pre), volume=0), "main")
                    used = pre
                mat = draft_mod.VideoMaterial(str(bcard))
                take = min(slot_us - used, int(mat.duration)) if mat.duration else slot_us - used
                if take > 200_000:
                    script.add_segment(draft_mod.VideoSegment(
                        mat, trange(start_us + used, take), volume=0), "main")
                    used += take
                rem = slot_us - used
                if rem > 200_000:
                    _fill2 = _kf if (_kf and _kf.exists()) else _placeholder_card(
                        s, cards_dir / f"{s['shot_id']}_gap.png",
                        label=f"片尾缺{rem / _US:.1f}s·品牌卡已放")
                    script.add_segment(draft_mod.VideoSegment(
                        draft_mod.VideoMaterial(str(_as_video_path(_fill2, base, rem / _US))),
                        trange(start_us + used, rem), volume=0), "main")
                continue
            mats = _shot_material(s, base, cards_dir, kinds)
            used = 0
            for i, (mat_path, is_video) in enumerate(mats):
                take = slot_us if i < len(mats) - 1 else slot_us - used
                if take <= 0:
                    break
                mat = draft_mod.VideoMaterial(str(_as_video_path(mat_path, base, slot_us / _US)))
                if mat.duration:
                    _md = int(mat.duration)
                    if _md > take + 300_000 and is_video:
                        # 0916 截断止血 (用户实锤: 超长片截前段=尾部文字/视觉事件陪葬):
                        # 微提速保全片 (≤1.22x 不可感知; pyJYD speed 语义 target=source/speed,
                        # 片长于槽→提速而非降速 — v1 方向写反 7.57s 撞轨实锤); 超限仍截+审计
                        _spd = _md / take
                        if _spd <= 1.22:
                            script.add_segment(draft_mod.VideoSegment(
                                mat, trange(start_us + used, take),
                                source_timerange=draft_mod.Timerange(0, _md),
                                speed=_spd, volume=0), "main")
                            used += take
                            entry_mismatch.append(f"{s['shot_id']}(提速{_spd:.2f}x保全片)")
                            continue
                        entry_mismatch.append(f"{s['shot_id']}(截{(_md - take) / _US:.1f}s重roll)")
                    if is_video and 0 < take - _md <= 600_000:
                        # 0918 微降速拉满 (用户实锤: 0.3s 粉卡闪成片难看 — 对称于提速止血):
                        # 视频短缺 ≤0.5s → 降速 3~6% (不可感知) 铺满槽, 粉卡只留真缺口
                        _spd = _md / take  # <1 = 慢放
                        script.add_segment(draft_mod.VideoSegment(
                            mat, trange(start_us + used, take),
                            source_timerange=draft_mod.Timerange(0, _md),
                            speed=_spd, volume=0), "main")
                        used += take
                        entry_mismatch.append(f"{s['shot_id']}(降速{1 / _spd:.2f}x拉满)")
                        continue
                    take = min(take, _md)  # 任意素材夹时长 (卡/静图同律)
                script.add_segment(draft_mod.VideoSegment(
                    mat, trange(start_us + used, take), volume=0), "main")
                used += take
            # 0916 用户令(二稿): 空位用大填色卡补 (诊断层: 镜号/时间/口播摘要) —
            # 只用填色卡, 绝不用生成内容垫 (定格帧/首帧图都不许); 卡精确填满差额,
            # 与逐段音频/字幕同轴 → 重新生成后一眼定位缺什么内容缺什么字幕.
            rem = slot_us - used
            if rem > 200_000:
                script.add_segment(draft_mod.VideoSegment(
                    draft_mod.VideoMaterial(str(_as_video_path(
                        _placeholder_card(s, cards_dir / f"{s['shot_id']}_gap.png",
                                          label=f"片尾缺{rem / _US:.1f}s·视频有"),
                        base, rem / _US))),
                    trange(start_us + used, rem), volume=0), "main")
                if kinds.get(s["shot_id"], "").startswith(("real", "brand", "still")):
                    warn_short.append(f"{s['shot_id']}(缺{rem / _US:.1f}s卡补)")

        # 文字层整体装配 (0916: 逐镜渲染遇跨镜重叠会整段丢 → 先收集去重叠再上轨;
        # caption 档 = J线三件套: 孤月体/5.0/奶油色/断句/内联划重点)
        # 0916 有稿必有字幕令: caption 事件 = 句级稿直出 (音频句轴+停顿吸附,
        # 100% 覆盖), 不再依赖 LLM text_layer 完备性 (实锤 10/54 句无字幕:
        # 导览句被并条/作者译者句被漏); text_layer 只供强调词降维与音效 cue.
        _cap0, hero_events = _collect_text_events(shots)  # noqa: F841 — hero/impact 源 (caption 走句级稿; overlay 0921 弃渲)
        from bisect import bisect_right as _br
        _st_axis = [float(s["t_start"]) for s in shots]

        def _sid_at(t: float) -> str:
            i = _br(_st_axis, t) - 1
            return str(shots[i]["shot_id"]) if 0 <= i < len(shots) else ""

        _tcax = tc_us / _US
        # 0918 字幕洗涤 (用户实锤 -0.5s- 入字幕): TTS 停顿标记不是文本 —
        # 句文本剥 -Xs-, 剥后为空的句 (纯标记行) 不出字幕事件 (时间轴不受影响)
        _PAUSE_MARK = re.compile(r"-\d+(?:\.\d+)?s-")
        cap_events = [{"a0": _tcax + sg["t_start"], "a1": _tcax + sg["t_end"],
                       "text": _PAUSE_MARK.sub("", sg["text"]).strip(),
                       "sid": _sid_at(_tcax + (sg["t_start"] + sg["t_end"]) / 2)}
                      for sg in _sent_segs if sg["t_end"] - sg["t_start"] >= 0.2]
        cap_events = [e for e in cap_events if e["text"]]
        # 0918 title 大字与口播字幕去重 (用户实锤 54s"撞墙测试"双份): title 文本
        # 被同时段句字幕包含 → 字幕已承载, 独立大字层冗余 — 丢弃 (logo_note 0917 同款)
        _cap_texts = [(e["a0"], e["a1"], e["text"]) for e in cap_events]
        _hero_drop = []
        for ev in hero_events:
            if ev["kind"] != "title":
                continue
            t = str(ev["text"]).strip()
            if t and any(a0 - 1.0 <= ev["a0"] <= a1 + 1.0 and t in ct
                         for a0, a1, ct in _cap_texts):
                _hero_drop.append(ev)
        for ev in _hero_drop:
            hero_events.remove(ev)
        if _hero_drop:
            logger.info("[draft] title 大字与字幕重复丢弃 %d 个: %s", len(_hero_drop),
                        "/".join(str(e["text"])[:10] for e in _hero_drop[:4]))
        hero_n = _render_hero(script, hero_events)
        # 0921 overlay 大字轨弃渲 (用户实锤 ep6 零星孤岛字幕): 军规下沉条目
        # 实产全是碎字/残片/双登记, 概念词零命中 — 剪辑层不再渲染大字轨;
        # text_layer overlay 条目保留数据层 (音效 cue / _ensure_motion_text 回填候选)
        overlay_n = 0
        # 0916 降维令: hero_number/impact 浮字砍层 → 同镜强调词表喂字幕行内高亮
        # 0917 补: logo_note 同降维 (孤岛浮字"HBO"实锤 — 字幕行已含该词, 独立层
        # 只产出孤岛; 未锚进字幕行的 logo_note 按 J 线教义丢弃, 音效照挂)
        emph_by_sid: dict[str, list[tuple[str, str]]] = {}
        for ev in hero_events:
            if ev["kind"] in ("hero_number", "year", "impact", "logo_note"):
                emph_by_sid.setdefault(ev["sid"], []).append((ev["text"], ev["kind"]))
        cap_stats = _render_captions(script, cap_events, emph_by_sid)
        # R9 同帧音效自动挂 (0916 attach_sound 平移; 缺文件/密度闸门自动跳过)
        sfx_stats = _attach_sound_cues(script, _sound_cues(doc))

        # 开场套件 (剪辑层 5.5s 预留, 素材拼贴非生成 — 与生成动画不抢时间):
        # S00 打字卡 0-2s (逐字) + S00b 定格 2-3s + S00c 书封墙 3-4.2s + S00d 主书封 4.2-5.5s
        # 飞入/扫描/收敛动画与音效 = 剪映后期位 (素材就位, 特效剪映里套)
        typing_text = ""
        wall_notes: list[str] = []
        if with_title_card:
            typing_text = _resolve_typing_text(book_id, ep)
            bg = Path(cfg.brand_card.title_bg)
            bg = bg if bg.exists() else _placeholder_card(
                {"shot_id": "标题卡", "t_start": 0, "t_end": 2,
                 "narration": typing_text or book_title}, cards_dir / "title_bg.png")
            # 0915 用户定: 标题底图 mp4 与系统生成动画同夹 (anim/) — 与 s27 等同源同位
            _t_src = _as_video_path(bg, base, 3.0)
            _t_dst = base / "anim" / _t_src.name
            if _t_src != _t_dst:
                _t_dst.parent.mkdir(parents=True, exist_ok=True)
                if not _t_dst.exists() or _t_dst.stat().st_mtime < _t_src.stat().st_mtime:
                    shutil.copy2(_t_src, _t_dst)
            script.add_segment(draft_mod.VideoSegment(
                draft_mod.VideoMaterial(str(_t_dst)), trange(0, 3 * _US), volume=0), "main")  # S00+S00b
            if typing_text:
                # 86万赞 1:1 · 0916 定稿四改 (验收: 原生打字动画管不住首行, 首行
                # 直接整行直出): 行1 静态常驻 0→3s; 行2+3 单常驻元素 + 居中打字
                # 1.65s (0.25 起 1.9 满); 字在元素内累加永不清空; 三承重金黄不变.
                emph = _typing_emph_spans(typing_text)
                lines = _typing_split_lines(typing_text)
                ys = _typing_line_ys(len(lines))
                offs, acc = [], 0
                for L in lines:
                    offs.append(acc)
                    acc += len(L)
                line1, rest = lines[0], lines[1:]
                rest_off = len(line1)  # rest 元素内坐标 = 全文坐标 - 行1长度

                def _type_seg(txt: str, start_us: int, dur_us: int, y: float,
                              hl_spans: list[tuple[int, int]]):
                    hl = [(s, min(e, len(txt))) for s, e in hl_spans
                          if s < len(txt) and min(e, len(txt)) > s]
                    return _StyledTextSegment(
                        txt, trange(start_us, dur_us),
                        base_color=_TYPE_BASE,
                        hl_color=_TYPE_HL, hl_size=_TYPE_SIZE,
                        highlight_ranges=hl,
                        style=TextStyle(size=_TYPE_SIZE, align=1, max_line_width=0.82,
                                        auto_wrapping=True,
                                        line_spacing=_TYPE_LINE_SPACING),
                        shadow=_TYPE_SHADOW,
                        clip_settings=ClipSettings(transform_y=y))

                # 行1: 静态直出 (无动画), 常驻整卡 0→3s
                l1_spans = [(max(s - offs[0], 0), min(e - offs[0], len(line1)))
                            for s, e in emph if min(e, offs[0] + len(line1)) > max(s, offs[0])]
                try:
                    script.add_segment(_type_seg(line1, 0, 3 * _US, ys[0], l1_spans),
                                       "typing")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[draft] 打字卡行1失败: %s", exc)
                # 行2+3: 常驻元素 + 原生打字 (元素内坐标换算, \n 偏移逐行累计)
                if rest:
                    rest_txt = chr(10).join(rest)
                    r_spans: list[tuple[int, int]] = []
                    for li in range(1, len(lines)):
                        for s, e in emph:
                            ls = max(s - offs[li], 0)
                            le = min(e - offs[li], len(lines[li]))
                            if le > ls:
                                base_off = offs[li] - rest_off + (li - 1)
                                r_spans.append((ls + base_off, le + base_off))
                    rest_y = sum(ys[1:]) / len(ys[1:])
                    _anim = next((a for a in ("打字机_I", "打字机_II", "打字机III")
                                  if hasattr(TextIntro, a)), None)
                    seg = _type_seg(rest_txt, int(_TYPE_START_S * _US),
                                    int((3.0 - _TYPE_START_S) * _US), rest_y, r_spans)
                    if _anim:
                        try:
                            seg.add_animation(getattr(TextIntro, _anim),
                                              duration=int((_TYPE_END_S - _TYPE_START_S) * _US))
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("[draft] 打字机动画失败(%s): %s", _anim, exc)
                    try:
                        script.add_segment(seg, "hero")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[draft] 打字卡行2+3失败: %s", exc)
                _attach_sfx(script, _TYPE_SFX, _TYPE_START_S)
            s00c, s00d, wall_notes = _prep_opening_assets(book_title, base)
            # ── 书墙滚动 + reveal v3 (0916 五升级令): 滚动+齿轮音对齐 → reveal
            # 素材二封面折叠开幕 + 末帧虚化垫底 + 滴声(书名@半程) + 人声两段 ──
            from app.services.jy_draft_service.sfx import attach_sound as _asx
            cover2 = covers_mod.extract_book_cover(book_title)
            _endf = covers_mod.wall_scroll_end_frame(book_title, scroll) if scroll else None
            rev_us = max(tc_us - int(rev_start * _US), 500_000)
            if scroll:
                script.add_segment(draft_mod.VideoSegment(
                    draft_mod.VideoMaterial(str(scroll)),
                    trange(3 * _US, int(scroll_s * _US)), volume=0), "main")
                _attach_sfx(script, _INTRO_SFX_GEAR, 3.0, max_sec=scroll_s)
            else:
                _wv = _as_video_path(s00c, base, 1.5)
                seg_c = draft_mod.VideoSegment(   # 兜底: 静态书封墙 3-4.0s 飞入
                    draft_mod.VideoMaterial(str(_wv)), trange(3 * _US, int(1.0 * _US)), volume=0)
                seg_c.add_animation(draft_mod.IntroType.向上滑动, 400_000)
                script.add_segment(seg_c, "main")
                _endf = None
            # reveal 垫底: 滚动末帧虚化 (动效突出) + 压暗; 无滚动兜底静态墙
            if _endf is not None:
                _blur = _endf.with_suffix(".endblur.jpg")
                if not _blur.exists():
                    from PIL import ImageFilter
                    from PIL import Image as _PILImage
                    _PILImage.open(_endf).convert("RGB").filter(
                        ImageFilter.GaussianBlur(14)).save(_blur, quality=92)
                bg_v = _as_video_path(_blur, base, rev_us / _US)
            else:
                bg_v = _as_video_path(s00c, base, rev_us / _US)
            script.add_segment(draft_mod.VideoSegment(
                draft_mod.VideoMaterial(str(bg_v)), trange(int(rev_start * _US), rev_us),
                volume=0, clip_settings=ClipSettings(alpha=0.3)), "main")
            # 主书封 = 素材二 (epub 提取本尊) 折叠开幕, reveal 窗口全铺 (念书名时书静止)
            _cover_img = cover2 if (cover2 and Path(cover2).exists()) else s00d
            seg_d = draft_mod.VideoSegment(
                draft_mod.VideoMaterial(str(_as_video_path(Path(_cover_img), base, rev_us / _US))),
                trange(int(rev_start * _US), rev_us), volume=0,
                clip_settings=ClipSettings(scale_x=0.85, scale_y=0.85, transform_y=0.0))  # 0916 令: 书封居中 (原 0.12 顶太近)
            try:
                seg_d.add_animation(draft_mod.IntroType.折叠开幕, min(1_500_000, rev_us))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[draft] 书封折叠开幕失败: %s", exc)
            script.add_segment(seg_d, "book")
            # 滴声 @ reveal 起 (书名 @ 其半程); 人声: 拆解句收在书名前, 书名钉半程点
            _attach_sfx(script, _INTRO_SFX_DROP, rev_start)
            if intro:
                _title_at = rev_start + drop_half
                # 拆解句 @3.00 与书墙首帧对齐 (0916 令); 不越书名点
                _d1 = min(max(int(intro["d1"] * _US) - 2000, 200_000),
                          int((_title_at - 3.0) * _US))
                _d2 = max(int(intro["d2"] * _US) - 2000, 200_000)
                try:
                    script.add_segment(draft_mod.AudioSegment(
                        str(intro["v1"]), trange(3 * _US, _d1)), "voice")
                    script.add_segment(draft_mod.AudioSegment(
                        str(intro["v2"]), trange(int(_title_at * _US), _d2)), "voice")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[draft] 开场人声段失败: %s", exc)
            # 0916 用户令: reveal 段不放底字 (原"作者名/在他的书里提到"两条已删)

        # voice 轨: 逐包口播段 (0916 用户令: 逐条音频让字幕/动画对齐内容更直观;
        # 空文本包不入轨=concat 同口径; 每段 -2ms 防 pyJYD 毫秒取整越界).
        # 失败/缺料兜底回整段 voice_full.wav. (audio 已在装配预计算处取好)
        voice = base / "voice_full.wav"
        # 陈旧检测 (0915 空包幻影实锤): 旧 voice_full 可能含 226s 静音尾巴
        if voice.exists():
            _expect = sum(float(f.get("dur_s") or 0) for f in audio["files"]
                          if str(f.get("text") or "").strip())
            _probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                     "format=duration", "-of", "csv=p=0", str(voice)],
                                    capture_output=True, text=True, timeout=30)
            try:
                _real = float(_probe.stdout.strip() or 0)
            except ValueError:
                _real = 0.0
            if abs(_real - _expect) > 2.0:
                logger.warning("[draft] voice_full 陈旧 (%.1fs ≠ 预期 %.1fs) — 重造", _real, _expect)
                concat_voice(audio["files"], voice)
        if not voice.exists():
            try:
                if audio["files"]:
                    concat_voice(audio["files"], voice)
            except (AnimNotFound, RuntimeError) as exc:
                logger.warning("[draft] 口播轨缺失: %s", exc)
                voice = None
        voice_segs = 0
        _packs = [f for f in audio["files"] if str(f.get("text") or "").strip()]
        if _packs and aligned:
            _cur = tc_us
            _prev_tail = 0.0  # 接缝静音闸 (1010): 前包尾静音滚存
            try:
                for f in _packs:
                    _d = int(round(float(f["dur_s"]) * _US))
                    _h, _t = _wav_edge_silence(str(f["file"]))
                    _seam = round(_prev_tail + _h, 2)
                    if _seam > _VOICE_SEAM_GATE:
                        entry_mismatch.append(
                            f"voice接缝@{_cur / _US:.1f}s静音{_seam:.1f}s"
                            f"(尾{_prev_tail:.1f}+头{_h:.1f})")
                    _prev_tail = _t
                    script.add_segment(draft_mod.AudioSegment(
                        str(f["file"]), trange(_cur, max(_d - 2000, 200_000))), "voice")
                    _cur += _d
                    voice_segs += 1
            except Exception as exc:  # noqa: BLE001 — 逐段失败兜底整段
                logger.warning("[draft] 逐包口播装配失败 (%s), 回退整段", exc)
                voice_segs = 0
        if voice_segs == 0 and voice and voice.exists():
            amat = draft_mod.AudioMaterial(str(voice))
            vdur = int(amat.duration) if amat.duration else int(float(shots[-1]["t_end"]) * _US)
            script.add_segment(draft_mod.AudioSegment(
                str(voice), trange(tc_us if aligned else 0, vdur)), "voice")

        # 0915 定案: 剪映 2026 迁移按数组顺序校验, 时间早于前段结尾的段会被摘下重挂到时间轴末尾
        # (开场套件后追加导致); 保存前按 target start 全轨排序
        for _tr in script.tracks.values():
            _tr.segments.sort(key=lambda s: s.target_timerange.start)
        script.save()

        n_real = sum(1 for v in kinds.values() if v.startswith(("real", "brand")))
        # ── 错配审计 (0916 用户实锤: 拆镜父镜继承旧文案视频) ──
        # ① 指纹错配: 生成时 gen_narr_sha ≠ 当前 narration 指纹 (旧视频对新文案)
        # ② 拆镜继承: 本镜有字母分身 (s63→s63b) 且自己带视频 — 视频按拆前整段生成
        import hashlib as _hs
        for s in shots:
            if not s.get("video_file"):
                continue
            _sha = _hs.md5(norm_for_match(s.get("narration") or "").encode("utf-8")).hexdigest()[:10]
            if s.get("gen_narr_sha") and s["gen_narr_sha"] != _sha:
                entry_mismatch.append(f"{s['shot_id']}(文案已变)")
            sid = str(s["shot_id"])
            if any(str(o["shot_id"]) != sid and str(o["shot_id"]).startswith(sid)
                   and str(o["shot_id"])[len(sid):][:1].isalpha() for o in shots):
                entry_mismatch.append(f"{sid}(拆镜继承视频)")
        if entry_mismatch:
            logger.warning("[draft] 错配嫌疑 %d 处: %s", len(entry_mismatch), entry_mismatch)
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "draft": name,
                 "book": book_title, "ep": ep, "shots": len(shots),
                 "total_s": shots[-1]["t_end"], "real": n_real,
                 "placeholder": len(shots) - n_real, "voice": bool(voice and voice.exists()),
                 "typing": bool(typing_text), "aligned": aligned, "opening_s": tc_us / _US,
                 "opening": "ok" if intro else ("off" if not with_title_card else "missing"),
                 **cap_stats, "hero_texts": hero_n, "overlay_texts": overlay_n,
                 **sfx_stats,
                 "kinds": kinds, "short_holds": warn_short, "opening_wall": wall_notes,
                 "content_mismatch": entry_mismatch}
        hist = base / "build_history.jsonl"
        with hist.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("[draft] %s → %s (真材 %d/%d%s)", name, drafts_dir, n_real, len(shots),
                    f", 定格补偿 {len(warn_short)} 镜" if warn_short else "")
        try:
            cues = _sound_cue_manifest(doc)
            (base / "音效提示.md").write_text(cues, encoding="utf-8")
            logger.info("[draft] 音效: 自动挂 %d 个 (密度跳过 %d/库缺 %d), 音效提示.md 已导出供核对",
                        sfx_stats.get("sfx", 0), sfx_stats.get("sfx_skip_density", 0),
                        sfx_stats.get("sfx_skip_missing", 0))
        except Exception as exc:  # noqa: BLE001 — 清单失败不炸草稿
            logger.warning("[draft] 音效提示清单失败: %s", exc)
        return {"draft_name": name, "drafts_dir": str(drafts_dir), "real": n_real,
                "placeholder": len(shots) - n_real, "voice": bool(voice and voice.exists()),
                "opening": "ok" if intro else ("off" if not with_title_card else "missing"), "aligned": aligned, "short_holds": warn_short,
                "hero_texts": hero_n, "overlay_texts": overlay_n, **cap_stats, **sfx_stats}
