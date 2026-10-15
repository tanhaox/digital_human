"""视频素材标签推断 — 基于英文 query 关键词映射, 零 token 成本.

规则来源: 与 Pexels API 数据对齐. Pexels 不返回标签/分类/地理位置/人物信息,
所有维度均在素材下载时从搜索 query 自动推断.
"""
from __future__ import annotations

from dataclasses import dataclass


ORIENTATION_PORTRAIT = "portrait"
ORIENTATION_LANDSCAPE = "landscape"

SOURCE_TYPE_FOOTAGE = "footage"
SOURCE_TYPE_CREATIVE = "creative"

LOCATION_DOMESTIC = "domestic"
LOCATION_FOREIGN = "foreign"

PEOPLE_YES = "people"
PEOPLE_NONE = "none"

PREFERENCE_LIKE = "like"
PREFERENCE_NEUTRAL = "neutral"
PREFERENCE_DISLIKE = "dislike"

SCENE_CITY = "城市"
SCENE_NATURE = "自然"
SCENE_BUSINESS = "商业"
SCENE_TECH = "科技"
SCENE_FINANCE = "财经"
SCENE_LIFESTYLE = "生活"
SCENE_FOOD = "美食"
SCENE_MEDICAL = "医疗"
SCENE_EDUCATION = "教育"
SCENE_INDUSTRIAL = "工业"

SHOT_AERIAL = "航拍"
SHOT_ARCHITECTURE = "建筑"
SHOT_TRAFFIC = "交通"
SHOT_PORTRAIT = "人像"
SHOT_CLOSEUP = "特写"
SHOT_EMPTY = "空镜"


@dataclass(frozen=True)
class TagTaxonomy:
    """标签体系与关键词映射表."""

    # 创意素材关键词(命中则 source_type=creative)
    creative_keywords: frozenset[str] = frozenset(
        [
            "animation",
            "animated",
            "motion graphics",
            "motion design",
            "3d render",
            "cgi",
            "cg",
            "visual effects",
            "vfx",
            "abstract",
            "futuristic",
            "digital art",
            "ai generated",
            "generative",
            "neon",
            "loop",
            "background loop",
            "intro",
            "title",
            "transition",
        ]
    )

    # 国内关键词
    domestic_keywords: frozenset[str] = frozenset(
        [
            "china",
            "chinese",
            "beijing",
            "shanghai",
            "shenzhen",
            "guangzhou",
            "hangzhou",
            "chengdu",
            "xian",
            "great wall",
            "chinese city",
        ]
    )

    # 场景关键词映射
    scene_keywords: dict[str, frozenset[str]] = None  # type: ignore[assignment]

    # 镜头类型关键词映射
    shot_keywords: dict[str, frozenset[str]] = None  # type: ignore[assignment]

    # 人物存在关键词
    people_keywords: frozenset[str] = frozenset(
        [
            "people",
            "person",
            "man",
            "woman",
            "crowd",
            "portrait",
            "worker",
            "doctor",
            "student",
            "family",
            "face",
        ]
    )

    def __post_init__(self) -> None:
        # dataclass(frozen=True) 中通过 object.__setattr__ 设置默认值
        if self.scene_keywords is None:
            object.__setattr__(
                self,
                "scene_keywords",
                {
                    SCENE_CITY: frozenset(
                        ["city", "urban", "downtown", "skyline", "street", "streets", "metropolis", "cityscape"]
                    ),
                    SCENE_NATURE: frozenset(
                        [
                            "nature",
                            "forest",
                            "mountain",
                            "ocean",
                            "sea",
                            "beach",
                            "river",
                            "lake",
                            "sunset",
                            "landscape",
                        ]
                    ),
                    SCENE_BUSINESS: frozenset(
                        ["business", "office", "corporate", "meeting", "handshake", "startup"]
                    ),
                    SCENE_TECH: frozenset(
                        [
                            "technology",
                            "tech",
                            "computer",
                            "coding",
                            "software",
                            "ai",
                            "robot",
                            "data",
                            "digital",
                        ]
                    ),
                    SCENE_FINANCE: frozenset(
                        [
                            "finance",
                            "stock",
                            "money",
                            "banking",
                            "investment",
                            "trading",
                            "market",
                            "economy",
                        ]
                    ),
                    SCENE_LIFESTYLE: frozenset(
                        [
                            "lifestyle",
                            "home",
                            "family",
                            "daily",
                            "walking",
                            "shopping",
                            "cafe",
                            "relaxation",
                        ]
                    ),
                    SCENE_FOOD: frozenset(
                        ["food", "restaurant", "cooking", "chef", "meal", "coffee", "bakery"]
                    ),
                    SCENE_MEDICAL: frozenset(
                        ["medical", "hospital", "doctor", "healthcare", "medicine", "clinic"]
                    ),
                    SCENE_EDUCATION: frozenset(
                        ["education", "school", "university", "student", "classroom", "learning"]
                    ),
                    SCENE_INDUSTRIAL: frozenset(
                        [
                            "industry",
                            "factory",
                            "manufacturing",
                            "industrial",
                            "machine",
                            "construction",
                            "logistics",
                        ]
                    ),
                },
            )
        if self.shot_keywords is None:
            object.__setattr__(
                self,
                "shot_keywords",
                {
                    SHOT_AERIAL: frozenset(["aerial", "drone", "bird eye", "top view", "overhead"]),
                    SHOT_ARCHITECTURE: frozenset(
                        ["architecture", "building", "skyscraper", "bridge", "house", "interior"]
                    ),
                    SHOT_TRAFFIC: frozenset(
                        ["traffic", "car", "road", "highway", "train", "airport", "port", "ship"]
                    ),
                    SHOT_PORTRAIT: frozenset(
                        ["portrait", "face", "person", "people", "man", "woman", "crowd", "worker"]
                    ),
                    SHOT_CLOSEUP: frozenset(["closeup", "macro", "detail", "texture"]),
                },
            )


# 模块级默认分类法
TAXONOMY = TagTaxonomy()


def _tokenize(query: str) -> list[str]:
    """将 query 拆分为小写词组, 保留空格分隔的连续词用于匹配多词关键词."""
    lowered = query.lower().strip()
    if not lowered:
        return []
    return lowered.split()


def _matches_any(query_lower: str, keywords: frozenset[str]) -> bool:
    """判断 query 是否命中任一关键词(支持多词关键词)."""
    for kw in keywords:
        if kw in query_lower:
            return True
    return False


def _collect_matches(query_lower: str, keyword_map: dict[str, frozenset[str]]) -> list[str]:
    """返回所有命中的维度标签列表."""
    return [label for label, kws in keyword_map.items() if _matches_any(query_lower, kws)]


def infer_orientation(width: int, height: int) -> str:
    """根据宽高比推断方向."""
    if width <= 0 or height <= 0:
        return ORIENTATION_LANDSCAPE
    return ORIENTATION_PORTRAIT if height > width else ORIENTATION_LANDSCAPE


def infer_tags(query: str, width: int, height: int, taxonomy: TagTaxonomy | None = None) -> dict:
    """从 query 推断完整标签字典.

    Args:
        query: 原始搜索 query(通常为英文).
        width: 视频宽.
        height: 视频高.
        taxonomy: 自定义分类法, 默认使用全局 TAXONOMY.

    Returns:
        dict 包含: orientation, source_type, location, scenes, shot_types, people
    """
    tax = taxonomy or TAXONOMY
    query_lower = query.lower().strip()

    scenes = _collect_matches(query_lower, tax.scene_keywords)
    shot_types = _collect_matches(query_lower, tax.shot_keywords)

    # 人物推断
    people = PEOPLE_YES if _matches_any(query_lower, tax.people_keywords) else PEOPLE_NONE

    # 空镜: 无人 + 没有强主体镜头类型(人像/特写/交通)
    strong_subject_shots = {SHOT_PORTRAIT, SHOT_CLOSEUP, SHOT_TRAFFIC}
    if people == PEOPLE_NONE and not any(s in shot_types for s in strong_subject_shots):
        shot_types = list(dict.fromkeys([*shot_types, SHOT_EMPTY]))

    return {
        "orientation": infer_orientation(width, height),
        "source_type": SOURCE_TYPE_CREATIVE if _matches_any(query_lower, tax.creative_keywords) else SOURCE_TYPE_FOOTAGE,
        "location": LOCATION_DOMESTIC if _matches_any(query_lower, tax.domestic_keywords) else LOCATION_FOREIGN,
        "scenes": scenes,
        "shot_types": shot_types,
        "people": people,
    }


def generate_asset_no(db_session, prefix: str = "V") -> str:
    """生成按日自增的唯一素材编号.

    格式: V{YYYYMMDD}-{4位自增}, 例如 V20260802-0001.
    使用数据库 SELECT MAX(asset_no) 保证单日内连续递增.

    Args:
        db_session: SQLAlchemy Session.
        prefix: 编号前缀.

    Returns:
        新的 asset_no 字符串.
    """
    from datetime import date

    from sqlalchemy import func

    from ..models import VideoAsset

    today = date.today().strftime("%Y%m%d")
    pattern = f"{prefix}{today}-%"
    max_row = (
        db_session.query(func.max(VideoAsset.asset_no))
        .filter(VideoAsset.asset_no.like(pattern))
        .scalar()
    )
    next_seq = 1
    if max_row:
        try:
            next_seq = int(max_row.split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_seq = 1
    return f"{prefix}{today}-{next_seq:04d}"


__all__ = [
    "TagTaxonomy",
    "TAXONOMY",
    "infer_tags",
    "infer_orientation",
    "generate_asset_no",
    # 常量导出
    "ORIENTATION_PORTRAIT",
    "ORIENTATION_LANDSCAPE",
    "SOURCE_TYPE_FOOTAGE",
    "SOURCE_TYPE_CREATIVE",
    "LOCATION_DOMESTIC",
    "LOCATION_FOREIGN",
    "PEOPLE_YES",
    "PEOPLE_NONE",
    "PREFERENCE_LIKE",
    "PREFERENCE_NEUTRAL",
    "PREFERENCE_DISLIKE",
    "SCENE_CITY",
    "SCENE_NATURE",
    "SCENE_BUSINESS",
    "SCENE_TECH",
    "SCENE_FINANCE",
    "SCENE_LIFESTYLE",
    "SCENE_FOOD",
    "SCENE_MEDICAL",
    "SCENE_EDUCATION",
    "SCENE_INDUSTRIAL",
    "SHOT_AERIAL",
    "SHOT_ARCHITECTURE",
    "SHOT_TRAFFIC",
    "SHOT_PORTRAIT",
    "SHOT_CLOSEUP",
    "SHOT_EMPTY",
]
