"""Director prompt — 词表包常量 / 清洗词表 / 静态示例.

常量集中于一处, 供 _vocabulary / _prompt / _strip_host 模块共享.
"""
from app.config import PROJECT_ROOT

VISUAL_DIRECTOR_V2_PATH = PROJECT_ROOT / "config" / "visual_director_v2.txt"

# ── 词表包 (Vocabulary Pack) — ID-034 ─────────────────────────────
# 导演关键词动态包：不把全量素材库塞进 prompt（~26KB→120s 超时），
# 而是构建一个可更新的"词表包"（~385 词/~3KB），DeepSeek 从包里按全维度
# 选词，本地再用词碰撞本地素材库。词表不写死 —— AI 打标完成/素材导入/
# 手动脚本都会重建本包。
#
# 维度分级 (2026-08-12 门槛重构):
#   - 硬维度 location/orientation/people —— 必须全中, 否则排除。
#     location 走 C 折中: strict 无候选才放宽 foreign 并标记。
#   - 门槛维 scenes/shot_types/tone —— 命中率 ≥75% (3 中 ≥2) 才算符合,
#     否则本地降级转 broll_pexels 下载
#   - 加分维 motion_level/content_density/time_of_day —— 命中加分, 不排除
VOCABULARY_PACK_REL = "data/vocabulary_pack.json"
VOCABULARY_PACK_PATH = PROJECT_ROOT / VOCABULARY_PACK_REL

# 硬维度枚举（词表包与碰撞共用）
HARD_DIMENSIONS = ("location", "orientation", "people")
# 门槛维 (必须有): 画面主体 + 决定性情绪
GATE_DIMENSIONS = ("scenes", "shot_types", "tone")
# 加分维 (不参与门槛)
BONUS_DIMENSIONS = ("motion_level", "content_density", "time_of_day")
# 软维度枚举 (门槛维 + 加分维)
SOFT_DIMENSIONS = GATE_DIMENSIONS + BONUS_DIMENSIONS
# 维度词量上下限：每维度最少 1-2 个词（已确认）, 上限防 prompt 膨胀
MIN_WORDS_PER_DIM = 1
MAX_WORDS_PER_DIM = 40

# 硬维度取值约束 → 只允许输出枚举内值, 防 DeepSeek 乱造词
LOCATION_VALUES = ("domestic", "foreign")
ORIENTATION_VALUES = ("portrait", "landscape", "square")
PEOPLE_VALUES = ("people", "none")

# shot_types 基础枚举 (2026-09-02 用户令重构):
#   - "空镜"退役 — 语义≈无人, 与硬维度 people=none 同义双轨, 是"无人素材
#     使用率畸高"的根因 (存量已同步剥离, 无人语义由 people 独占)
#   - 扩充真实镜头语言 (远景~特写/俯拍/跟拍/延时…); 交通/建筑属内容类,
#     因存量大量已标保留兼容
BASE_SHOT_TYPES = (
    "远景", "全景", "中景", "近景", "特写", "人像",
    "航拍", "俯拍", "跟拍", "延时", "交通", "建筑",
)

# 词表清洗：定向剔除的脏词（脚本残留 ||、方位/国家词、噪音）
# (2026-09-02: city/street/traffic/building/crowd 移出 — 正当画面词被误杀,
#  keywords 组系统性贫瘠, 间接加剧导演对 shot_types 的依赖)
_CLEANUP_PREFIXES = ("||", "[", "]", "{", "}", "(", ")")
_CLEANUP_RAW = frozenset({
    "vertical", "wide", "east", "west", "western", "in", "up", "us", "list", "back",
    "game", "wake", "generation", "strategy", "chain", "support", "truth", "question",
    "analysis", "pressure", "risk", "determination", "war", "stock", "history",
    "progress", "comparison", "alignment", "diplomacy", "manipulation", "exploitation",
    "achievement", "warning", "competition", "production", "supply", "industry",
    "official", "document", "island", "people", "world", "market",
    "price", "busy", "urban",
    "usa", "russia", "taiwan", "shanghai", "china", "yeltsin",
})
# 额外定向剔除的国家/城市/方位词（地域语义已由 location 硬维度承载, 词表里不重复）
_CLEANUP_EXTRA = frozenset({
    "japan", "japanese", "korea", "america", "american", "united", "states",
    "china", "chinese", "beijing", "shanghai", "guangzhou", "hong", "kong",
    "taiwan", "taipei", "russia", "russian", "moscow", "europe", "european",
    "germany", "german", "france", "french", "uk", "britain", "british",
    "india", "indian", "australia", "canada", "north", "south", "east",
    "west", "central", "coastal", "northern", "southern", "eastern", "western",
})
# 英文停用词/泛化词（挤占词表容量, 且碰撞时会造成大量假命中）
_CLEANUP_EN_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "they", "their", "them",
    "he", "she", "his", "her", "we", "our", "you", "your", "all", "any",
    "not", "but", "so", "if", "then", "also", "very", "more", "most",
})
# 中文弱词（单字/标点/语气词, 无画面检索价值）
_CLEANUP_WEAK_CN = frozenset({
    "的", "了", "在", "是", "与", "和", "及", "或", "之", "对", "中", "为",
    "有", "都", "很", "也", "还", "这", "那", "个", "上", "下", "间", "后",
    "前", "内", "外", "一", "两", "三", "一", "并", "等", "将", "从", "向",
})

# 脏词合并（包含表），用于兜底清除其余语义弱词
_CLEANUP_WORDS = _CLEANUP_RAW | _CLEANUP_EXTRA | _CLEANUP_EN_STOP | _CLEANUP_WEAK_CN

# 无出镜模式下替换输出示例的完整 JSON (2026-08-07)
_NO_HOST_DEMO = """```json
{
  "video_title": "刚刚,海关公布:上半年进口10.74万亿,增长22.1%",
  "slots": [
    {
      "slot_index": 0,
      "start_sec": 0.00,
      "end_sec": 4.20,
      "segment_refs": ["S001"],
      "visual_type": "broll_pexels",
      "workflow": "broll_pexels",
      "params": {
        "keywords": ["china", "city", "street", "people", "traffic"],
        "category": "城市街景",
        "intensity": "low",
        "emotion": "opening"
      }
    },
    {
      "slot_index": 1,
      "start_sec": 4.20,
      "end_sec": 7.50,
      "segment_refs": ["S002"],
      "visual_type": "hf_title",
      "workflow": "hf_title",
      "params": {"render_config": {"title": "上半年进口 10.74 万亿", "subtitle": "增长 22.1%"}, "intensity": "low", "emotion": "opening"}
    },
    {
      "slot_index": 2,
      "start_sec": 7.50,
      "end_sec": 11.00,
      "segment_refs": ["S003"],
      "visual_type": "broll_pexels",
      "workflow": "broll_pexels",
      "params": {
        "keywords": ["supermarket", "shelf", "imported", "goods"],
        "category": "超市货架",
        "intensity": "medium",
        "emotion": "rising"
      }
    },
    {
      "slot_index": 3,
      "start_sec": 11.00,
      "end_sec": 16.40,
      "segment_refs": ["S004"],
      "visual_type": "hf_chart",
      "workflow": "hf_chart",
      "params": {
        "render_config": {
          "chart_type": "pie_chart",
          "data": [
            {"label": "进口额(万亿元)", "value": 10.74},
            {"label": "增长(%)", "value": 22.1}
          ],
          "label": "进口额(万亿元)",
          "unit": "万亿",
          "growth": "22.1%",
          "color_scheme": "黑金"
        },
        "intensity": "high",
        "emotion": "rising"
      }
    },
    {
      "slot_index": 4,
      "start_sec": 16.40,
      "end_sec": 21.80,
      "segment_refs": ["S005"],
      "visual_type": "broll_pexels",
      "workflow": "broll_pexels",
      "params": {
        "keywords": ["china", "port", "shipping", "containers"],
        "category": "港口",
        "intensity": "high",
        "emotion": "climax"
      }
    },
    {
      "slot_index": 5,
      "start_sec": 21.80,
      "end_sec": 25.50,
      "segment_refs": ["S006"],
      "visual_type": "hf_title",
      "workflow": "hf_title",
      "params": {"render_config": {"title": "评论区聊聊", "subtitle": "您买到便宜进口货了吗"}, "intensity": "low", "emotion": "closing"}
    },
    {
      "slot_index": 6,
      "start_sec": 25.50,
      "end_sec": 30.00,
      "segment_refs": ["S007"],
      "visual_type": "hf_title",
      "workflow": "hf_title",
      "params": {"render_config": {"title": "关注我,看懂财经", "subtitle": "下期见"}, "intensity": "low", "emotion": "closing"}
    }
  ]
}
```"""
