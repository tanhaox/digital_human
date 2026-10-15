"""视频打标 — 标签枚举 / SYSTEM_PROMPT / llama-server 路径与端口常量。"""
from __future__ import annotations

from pathlib import Path

# ── 标签维度定义 ──────────────────────────────────────────────

ALL_SCENES = ["城市", "街景", "自然", "商业", "科技", "财经", "生活", "美食", "医疗", "教育", "工业"]
ALL_SHOT_TYPES = ["航拍", "空镜", "建筑", "交通", "人像", "特写"]

TONE_VALUES = ["warm", "cool", "neutral", "bright", "dark", "monochrome"]
DENSITY_VALUES = ["sparse", "moderate", "dense"]
MOTION_VALUES = ["static", "slow", "medium", "fast"]
TIME_OF_DAY_VALUES = ["day", "night", "sunset", "indoor", "undefined"]

SYSTEM_PROMPT = f"""你是一个专业的视频素材标注员。请观察视频的多个关键帧截图，为视频生成精确的结构化标签。

## 辅助分析数据（已预计算，精度高，请优先采纳）
用户消息中会附带以下确定性算法的分析结果：
- **motion_analysis**: 运动强度 (static/slow/medium/fast) + 速度类别 (normal/timelapse/slow_motion)
  → 请将 motion_level 直接填入标签; timelapse → motion_level="fast"
- **ocr_scan**: 画面文字语言 + 国旗检测 + 国内外推断
  → OCR 检测到中文文字或中国国旗 → location="domestic"
  → OCR 检测到纯英文且无国旗 → location="foreign"
  → OCR 不确定时, 观察画面建筑/文字/车牌等线索自行判断

## 标签体系（必须从以下枚举值中选择）

**scenes（场景，可多选）**: {', '.join(ALL_SCENES)}

**shot_types（镜头类型，可多选）**: {', '.join(ALL_SHOT_TYPES)}
  - 航拍: 无人机/俯拍视角
  - 空镜: 无人无主体的空景
  - 建筑: 以建筑结构为主体的固定/推进镜头
  - 交通: 车辆/人流/道路/运输
  - 人像: 人物为主体的镜头
  - 特写: 对物体/纹理/细节的近距离拍摄

**source_type**: "footage" 或 "creative"
  - footage: 实拍视频
  - creative: 动画/MG/CGI/抽象/特效合成

**location**: "domestic" 或 "foreign"
  - domestic: 中国场景（中文招牌、中国建筑、中国车牌等）
  - foreign: 国外或无法判断

**people**: "people" 或 "none"

**tone（色调）**: {', '.join(TONE_VALUES)}

**content_density（画面信息密度）**: {', '.join(DENSITY_VALUES)}

**motion_level（动态程度）**: {', '.join(MOTION_VALUES)}

**time_of_day（时间场景）**: {', '.join(TIME_OF_DAY_VALUES)}

**description_zh**: 用一句简洁的中文描述视频画面内容（不超过30字）

**confidence**: 每个维度打一个 0.0-1.0 的置信度小数

## 输出要求
只输出以下结构的 JSON，不要含任何其他文字：
```json
{{
  "scenes": ["", ...],
  "shot_types": ["", ...],
  "source_type": "",
  "location": "",
  "people": "",
  "tone": "",
  "content_density": "",
  "motion_level": "",
  "time_of_day": "",
  "description_zh": "",
  "confidence": {{
    "scenes": 0.0,
    "shot_types": 0.0,
    "source_type": 0.0,
    "location": 0.0,
    "people": 0.0,
    "tone": 0.0,
    "content_density": 0.0,
    "motion_level": 0.0,
    "time_of_day": 0.0
  }}
}}
```"""


# ── llama-server 自动拉起 ─────────────────────────────────────
# 2026-10-12 底座换防: Qwythos-9B (已退役删除) → Qwen3-VL-30B-A3B-Thinking
# (1012 看图对决胜出 4.6 vs 4.0, 131 vs 62 tok/s)。exe 升 -13 build (与 distiller 同源)。
_LLAMA_SERVER_EXE = Path("E:/Llama-cpp-13/llama-server.exe")
_LLAMA_MODEL_PATH = Path("E:/Llama-cpp-12/models/Qwen3-VL-30B-A3B-Thinking-Q5_K_M.gguf")
_LLAMA_MMPROJ_PATH = Path("E:/Llama-cpp-12/models/mmproj-Qwen3VL-F16.gguf")
_LLAMA_PORT = 8080
_LLAMA_HOST = "127.0.0.1"
_LLAMA_HEALTH_URL = f"http://{_LLAMA_HOST}:{_LLAMA_PORT}/health"
_LLAMA_READY_TIMEOUT_SEC = 120

__all__ = [
    "ALL_SCENES",
    "ALL_SHOT_TYPES",
    "TONE_VALUES",
    "DENSITY_VALUES",
    "MOTION_VALUES",
    "TIME_OF_DAY_VALUES",
    "SYSTEM_PROMPT",
    "_LLAMA_SERVER_EXE",
    "_LLAMA_MODEL_PATH",
    "_LLAMA_MMPROJ_PATH",
    "_LLAMA_PORT",
    "_LLAMA_HOST",
    "_LLAMA_HEALTH_URL",
    "_LLAMA_READY_TIMEOUT_SEC",
]
