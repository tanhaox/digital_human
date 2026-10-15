# -*- coding: utf-8 -*-
"""全系统统一禁用词/限流词服务 (2026-08-22) — 拆书/新闻线/未来衍生系统共用.

唯一事实源: config/compliance_common.json (categories: 绝对化/医疗/迷信/金融/引流/承诺).
入口:
- load_common_rules()       读词库
- build_redline_prompt()    组装成生成提示词注入块 (源头规避, LLM 生成时就避开)
- check_text(text)          检测文本命中违禁词 (生成后检查兜底)

用法示例:
- 拆书: orchestrator.GateB / facing-units prompt 注入 build_redline_prompt()
- 新闻线: articles.rewrite 加载模板后注入
- 未来系统: 直接 import compliance 服务, 禁止词功能自动赋能
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_COMMON_PATH = Path(__file__).resolve().parents[2] / "config" / "compliance_common.json"
_VISUAL_PATH = Path(__file__).resolve().parents[2] / "config" / "compliance_visual.json"

_cache: dict | None = None
_visual_cache: list[str] | None = None
_subs_cache: dict[str, str] | None = None


def load_common_rules() -> dict:
    """读 config/compliance_common.json (带缓存)."""
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_COMMON_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[compliance] 公共限流词库加载失败: %s", exc)
            _cache = {"categories": {}}
    return _cache


def _all_banned(categories: list[str] | None = None) -> list[str]:
    """聚合指定类别(默认全部)的禁用词."""
    rules = load_common_rules().get("categories") or {}
    keys = categories or list(rules.keys())
    out: list[str] = []
    for k in keys:
        ban = (rules.get(k) or {}).get("ban") or []
        out.extend(str(w) for w in ban)
    return list(dict.fromkeys(out))  # 去重保序


def _all_replaces(categories: list[str] | None = None) -> dict[str, str]:
    """类别 → 替换建议."""
    rules = load_common_rules().get("categories") or {}
    keys = categories or list(rules.keys())
    return {k: (rules.get(k) or {}).get("replace", "") for k in keys}


def build_redline_prompt(categories: list[str] | None = None) -> str:
    """组装成生成提示词注入块 — LLM 生成时就规避 (源头).

    返回多行文本, 可直接拼到任何生成 prompt (拆书 facing/逐集、新闻线洗稿).
    """
    rules = load_common_rules().get("categories") or {}
    keys = categories or list(rules.keys())
    lines = ["【平台禁用词 · 全系统红线 · 生成时必规避, 出现即违规】"]
    for k in keys:
        cat = rules.get(k) or {}
        ban = "、".join(str(w) for w in (cat.get("ban") or [])[:12])
        repl = cat.get("replace", "")
        lines.append(f"- 禁{cat.get('label', k)}: {ban}（→ {repl}）")
    lines.append("- 全篇用'个人感受+参考建议'的温和表达, 禁绝对化/医疗承诺/迷信/金融收益/站外引流。")
    return "\n".join(lines)


def check_text(text: str, categories: list[str] | None = None) -> list[str]:
    """检测文本命中违禁词 (生成后检查兜底). 返回命中词列表."""
    if not text:
        return []
    banned = _all_banned(categories)
    hits = [w for w in banned if w and w in text]
    return hits


def build_check_prompt(categories: list[str] | None = None) -> str:
    """组装成审计/检测 prompt — 供 LLM 或代码检查生成内容."""
    return build_redline_prompt(categories)


def visual_banned() -> list[str]:
    """画面禁区词库 (config/compliance_visual.json, 带缓存).

    0917 动画线用户令: 任何画面禁出现中国地图/国旗/国徽/国家领导人画像 —
    卡通/剪影/变形也不行。宪法提示/软警告/生成闸门/K2 防尾共用此单一事实源。
    """
    global _visual_cache
    if _visual_cache is None:
        try:
            data = json.loads(_VISUAL_PATH.read_text(encoding="utf-8"))
            _visual_cache = [str(w) for cat in (data.get("categories") or {}).values()
                             for w in (cat.get("ban") or []) if w]
        except Exception as exc:  # noqa: BLE001 — 词库坏不炸管线, 空表=只靠提示层
            logger.warning("[compliance] 画面禁区词库加载失败: %s", exc)
            _visual_cache = []
    return _visual_cache


def visual_substitutes() -> dict[str, str]:
    """国家指代表 (config/compliance_visual.json substitutes, 带缓存).

    0917 用户令: 表现国家概念禁旗徽地图, 用央视同款指代物 (美国=白头鹰/中东=骆驼)。
    表是宪法提示的拼装源 — 改表即 DIRECTOR_SYS 同步 (director2 模块加载时拼入)。
    """
    global _subs_cache
    if _subs_cache is None:
        try:
            data = json.loads(_VISUAL_PATH.read_text(encoding="utf-8"))
            raw = data.get("substitutes") or {}
            _subs_cache = {str(k): str(v) for k, v in raw.items() if not str(k).startswith("_")}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[compliance] 国家指代表加载失败: %s", exc)
            _subs_cache = {}
    return _subs_cache


_platform_neg_cache: list[str] | None = None


def platform_bans_negative() -> list[str]:
    """K2 负面提示词注入表 (1008 规层 R2): 英文高频平台违禁词.

    零 LLM 成本 — cfg=2.0 负面提示词通道已有, 直接拼接.
    """
    global _platform_neg_cache
    if _platform_neg_cache is None:
        try:
            data = json.loads(_VISUAL_PATH.read_text(encoding="utf-8"))
            _platform_neg_cache = [str(w) for w in (data.get("platform_bans_negative_prompt") or []) if w]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[compliance] 负面提示词表加载失败: %s", exc)
            _platform_neg_cache = []
    return _platform_neg_cache


# 1008 规层: 平台限流/禁止类目键 (compliance_visual.json v2 新增的 8 类)
PLATFORM_CATEGORIES = ("tobacco_alcohol", "gambling", "violence_gore", "sexual_content",
                       "drug_content", "financial_risk", "minor_safety", "religious_symbols")


def visual_platform_labels() -> list[str]:
    """平台类目的中文标签 (道层 forbidden 行拼装用, 一行搞定不占注意力)."""
    try:
        data = json.loads(_VISUAL_PATH.read_text(encoding="utf-8"))
        cats = data.get("categories") or {}
        return [str((cats.get(k) or {}).get("label") or k) for k in PLATFORM_CATEGORIES]
    except Exception:  # noqa: BLE001
        return []


# 1008 规层: 画面文本安全替代表 (法/术出口覆写用, auto_fix_banned 同款思路)。
# 有安全替代词的覆写; 无替代的剔除该词 (剔除交给 sanitize_visual_text 兜底段)。
# 平台类物品没有"视觉孪生"可用 (孪生=换个花样违规), 只给叙事级替身。
_VISUAL_SOFT_FIX = {
    # 政治类 (与 director2.auto_fix_banned 同款, 孪生验证过)
    # 1015 口径修正: 裸「地图」除名 (禁的是中国地图; 藏宝图/示意地图合法 —
    # 裸词替换正是 s51 棋盘→赌博四连杀 saga 的起点)
    "招牌": "灯牌", "红旗": "旗座", "军装": "制服", "警服": "制服",
    "世界地图": "世界棋盘", "中国地图": "立体沙盘", "领导人": "主讲人", "政治人物": "人物",
    "标语牌": "信息板", "横幅": "装饰带", "告示牌": "指示板",
    # 烟酒 (平台限流): 饮酒→茶是成熟替身; 烟无安全孪生 → 剔除
    "喝酒": "喝茶", "饮酒": "饮茶", "酒精": "饮品", "啤酒": "麦茶",
    "白酒": "清茶", "酒瓶": "茶壶", "醉酒": "微醺",
    # 赌博 (平台禁止): 棋族替身
    "扑克牌": "棋牌", "骰子": "棋子", "老虎机": "弹珠机", "赌场": "游戏厅",
    "赌博": "对弈", "赌桌": "棋桌", "筹码": "棋子", "彩票": "奖券",
}


def sanitize_visual_text(text: str) -> tuple[str, list[str]]:
    """画面文本违禁词净化 (1008 规层 法/术出口共用, 零 LLM).

    命中: 有替代词的覆写 (吸烟场景变喝茶场景), 无替代的剔除该词。
    返回 (净化后文本, 命中词表); 上游凭命中表记日志/标记, 不阻断。
    """
    if not text:
        return text, []
    hits: list[str] = []
    out = text
    for bad, good in _VISUAL_SOFT_FIX.items():
        if bad in out:
            hits.append(bad)
            out = out.replace(bad, good)
    for w in visual_banned():
        if w and w in out:  # 已映射的词替换后不再出现; 未映射的剔除
            hits.append(w)
            out = out.replace(w, "")
    if hits and out != text:
        import re as _re
        out = _re.sub(r"[，,、；;]{2,}", "，", out)  # 剔除残留的双标点
        out = out.strip("，。、；; ")
    return out, hits


def check_visual(text: str) -> list[str]:
    """画面禁区检测: keyframe/motion/文字层等画面描述文本的命中词列表."""
    if not text:
        return []
    return [w for w in visual_banned() if w in text]


def visual_constitution_prompt() -> str:
    """画面宪法拼装块 (0923 用户令"宪法进每个角落"): 所有视觉生成 LLM 入口统一注入.

    源头规避 — 圣经角色卡/试镜镜/风格提案/重调/分镜规划产 prompt 时就带着禁区与指代物,
    下游 VLM/OCR 守门只是兜底。改 compliance_visual.json 即此处同步。"""
    bans = visual_banned()
    subs = visual_substitutes()
    if not bans:
        return ""
    lines = ["【画面宪法 · 硬性禁区 (生成任何画面描述/角色look/道具时必须规避, 违者抖音不给流量)】"]
    for cat_name, cat in ((_VISUAL_PATH and json.loads(_VISUAL_PATH.read_text(encoding="utf-8")).get("categories") or {}).items() if _VISUAL_PATH.exists() else []):
        note = (cat.get("note") or "")[:60]
        ban = "、".join((cat.get("ban") or [])[:12])
        if ban:
            lines.append(f"- {cat_name}: 禁 {ban}" + (f" ({note})" if note else ""))
    if subs:
        rows = "；".join(f"{k}＝{v}" for k, v in subs.items() if not str(k).startswith("_"))
        lines.append(f"- 国家/民族概念指代物 (禁旗徽地图, 用指代物): {rows}")
    return "\n".join(lines) + "\n"
