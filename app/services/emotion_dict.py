# -*- coding: utf-8 -*-
"""统一情绪字典 — 全链路 (文稿 → 音频 → 导演 → 画面) 共享的情绪语言.

情绪标签由 P5 标注 (段落+情绪+强度档), 各链路查本字典:
  - TTS 层    → 8维向量 + 情绪基准α (再乘强度档系数 × 音色系数)
  - 导演 _intent.py → 视觉意图
  - 画面搜索  → 搜索关键词

8维顺序: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]

设计依据 (docs/guides/tts_emotion_experiments.md):
  - qwen 0.6B 自动识别不可靠 → LLM 标情绪为主
  - 情绪级自然 α 不同 (实验): 反感/愤怒 0.3-0.4, 高兴/惊讶 0.6
  - α 上限 0.65 保音色 (大学教授音色 0.8 漂移, 0.6 安全)
  - 音色只是参考坐标: 音色系数系统映射, 不做决定性工程
"""
from __future__ import annotations

from dataclasses import dataclass

# 8 维向量顺序 (与 infer_v2.py normalize_emo_vec 一致)
# 向量需满足 normalize: 乘 bias 后 sum ≤ 0.8
EMOTION_DIMS = ["happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]

# 强度档 → 档位号 (1-6). 兼容 弱/中/强 (弱=2, 中=4, 强=6).
def _strength_to_int(value: str) -> int:
    v = str(value).strip().lower()
    mapping = {
        "弱": 2, "低": 2, "weak": 2, "low": 2,
        "中": 4, "medium": 4, "mid": 4,
        "强": 6, "高": 6, "strong": 6, "high": 6,
        "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    }
    n = mapping.get(v, 4)
    return max(1, min(7, n))


# α 坡度 (2026-08-13 实验定稿): 0.30~0.60, 0.05 步进.
# 用户听觉验证: 低强度段(叙述/身份)用 惊讶0.3 替代"平静" → 整篇惊讶基调, 不平淡(平静会让人退出).
# 惊讶 0.3 不拖慢语速 (惊讶向量非低落); 峰顶 0.6 高亢. 强度档 1-7: 1→0.30 ... 7→0.60.
ALPHA_MIN = 0.30
ALPHA_MAX = 0.60
ALPHA_STEP = 0.05

# 音色系数 (仅作参考坐标, 大学教授音色 = 1.0 基准; 平/沙哑音色需更高 α)
TIMBRE_FACTOR = 1.0


@dataclass(frozen=True)
class EmotionDef:
    """一个情绪的全链路定义."""
    key: str
    vector: list[float]          # 8 维向量
    base_alpha: float            # 情绪基准 α
    visual_intent: str           # 导演视觉意图
    search_terms: list[str]      # 画面搜索词


# 核心情绪集 (P5 白名单) — 基于实验: 平静/严肃/愤怒/惊讶/高兴
EMOTIONS: dict[str, EmotionDef] = {
    "calm": EmotionDef(
        key="calm",
        vector=[0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.80],
        base_alpha=0.4,
        visual_intent="沉稳/冷峻",
        search_terms=["城市", "建筑", "街道", "日常"],
    ),
    "serious": EmotionDef(
        key="serious",
        vector=[0.00, 0.40, 0.00, 0.00, 0.00, 0.10, 0.00, 0.30],
        base_alpha=0.4,
        visual_intent="紧张/严肃",
        search_terms=["芯片", "机房", "会议", "数据", "厂房"],
    ),
    "angry": EmotionDef(
        key="angry",
        vector=[0.00, 0.80, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
        base_alpha=0.4,
        visual_intent="张力/对立",
        search_terms=["封锁", "制裁", "冲突", "数据滥用", "垄断"],
    ),
    "surprised": EmotionDef(
        key="surprised",
        vector=[0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.70, 0.10],
        base_alpha=0.6,
        visual_intent="爆点/冲击",
        search_terms=["特写", "震撼", "反差", "爆点", "意外"],
    ),
    "happy": EmotionDef(
        key="happy",
        vector=[0.70, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.10],
        base_alpha=0.6,
        visual_intent="升华/上扬",
        search_terms=["成果", "光", "前景", "丰收", "突破"],
    ),
    # 2026-08-14: 补 confident (P4 白名单/7层锚点版段首标签用, 原 EMOTIONS 缺此 key 致 KeyError)
    "confident": EmotionDef(
        key="confident",
        vector=[0.40, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.40],
        base_alpha=0.5,
        visual_intent="确信/有力",
        search_terms=["实力", "底气", "突破", "胜局", "成果"],
    ),
    # 2026-09-03: 补 melancholic (读书线共情段) — P5 prompt 一直枚举它但字典缺定义,
    # 标了也被 normalize 降级 calm。怅然=低回陪伴不是悲伤 (sad 只占 0.10)。
    "melancholic": EmotionDef(
        key="melancholic",
        vector=[0.00, 0.00, 0.10, 0.00, 0.00, 0.70, 0.00, 0.00],
        base_alpha=0.4,
        visual_intent="共情/低回",
        search_terms=["夜色", "窗", "雨", "背影", "独处"],
    ),
}

# 情绪中文名 (P5 标注用)
EMOTION_CN = {
    "calm": "平静", "serious": "严肃", "angry": "愤怒",
    "surprised": "惊讶", "happy": "高兴", "confident": "确信",
    "melancholic": "怅然",
}
# 中→英解析 (2026-08-25 修复): P5 输出中文情绪名, 而 EMOTIONS 字典 key 是英文 —
# 此前无人转换, tts_service 的 resolve_emotion('惊讶') KeyError 被 except 吞掉,
# 全部情绪段静默丢弃 → TTS 恒为整篇 calm。别名表覆盖 LLM 常见同义输出。
CN_TO_KEY = {v: k for k, v in EMOTION_CN.items()}
CN_TO_KEY.update({
    "沉稳": "calm", "冷静": "calm", "镇定": "calm",
    "认真": "serious", "凝重": "serious", "揭秘": "serious",
    "震惊": "surprised", "意外": "surprised",
    "开心": "happy", "喜悦": "happy", "欢快": "happy", "升华": "happy",
    "生气": "angry", "恼火": "angry",
    "忧郁": "melancholic", "伤感": "melancholic", "低回": "melancholic",
})


def normalize_emotion_key(name: str) -> str:
    """中文/英文情绪名 → EMOTIONS 字典 key。已是合法 key 原样返回; 中文查别名表;
    未知名降级 'calm' (宁可无情绪, 不可 KeyError 整段丢弃)。"""
    if name in EMOTIONS:
        return name
    return CN_TO_KEY.get(str(name).strip(), "calm")


def resolve_emotion(
    emotion_key: str,
    strength: str = "中",
    timbre_factor: float = TIMBRE_FACTOR,
) -> dict:
    """按情绪 + 强度档 + 音色系数, 解析出全链路参数.

    Args:
        emotion_key: 情绪标签 (calm/serious/angry/surprised/happy)
        strength: 强度档 (1-6 或 弱/中/强) — α 坡度 0.35~0.60, 0.05 步进
        timbre_factor: 音色系数 (默认 1.0, 平/沙哑音色可调大)

    Returns:
        {vector, alpha, visual_intent, search_terms}
    """
    emo = EMOTIONS[emotion_key]
    n = _strength_to_int(strength)
    alpha = ALPHA_MIN + (n - 1) * ALPHA_STEP  # 1→0.35 ... 6→0.60
    alpha = max(ALPHA_MIN, min(ALPHA_MAX, alpha * timbre_factor))
    return {
        "vector": list(emo.vector),
        "alpha": round(alpha, 3),
        "visual_intent": emo.visual_intent,
        "search_terms": list(emo.search_terms),
    }


def parse_strength(value: str) -> str:
    """归一化强度档: 弱/中/强 (容错 1/2/3, 低/中/高, weak/medium/strong)."""
    v = str(value).strip().lower()
    mapping = {
        "弱": "弱", "低": "弱", "1": "弱", "weak": "弱", "low": "弱",
        "中": "中", "2": "中", "medium": "中", "mid": "中",
        "强": "强", "高": "强", "3": "强", "strong": "强", "high": "强",
    }
    return mapping.get(v, "中")


if __name__ == "__main__":
    # 冒烟测试
    for k in EMOTIONS:
        for s in ("弱", "中", "强"):
            r = resolve_emotion(k, s)
            print(f"{k}/{s}: α={r['alpha']} vec_sum={sum(r['vector']):.2f}")
