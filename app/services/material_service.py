# -*- coding: utf-8 -*-
"""素材聚合服务  — 七层洗稿的多源素材包.

链路: 批量抓取 URL → 七层覆盖审计 (LLM JSON) → 智谱定向补搜 →
洗稿时整包按层注入上下文. 解决 7 层模板单篇原文喂不饱 L3~L6
(背景/参数/实测/商业) 的字数塌陷.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..models import MaterialItem, MaterialPackage
from .boost_service import _call, _extract_json
from .url_fetcher import fetch_url
from .zhipu_search import ZhipuSearchError, ZhipuUnavailableError, zhipu_web_search

logger = logging.getLogger(__name__)

__all__ = [
    "SEVEN_LAYERS",
    "build_audit_input",
    "audit_package",
    "collect_gap_queries",
    "build_material_context_block",
    "batch_fetch_urls",
    "search_and_ingest",
]

# 与 config/laotan-tech_7layer_anchored.txt 对齐: (层号, 层名, 字数, 信息需求)
SEVEN_LAYERS: list[tuple[str, str, str, str]] = [
    ("L1", "极速钩子", "40~50字", "一个具体的反直觉结果/翻车画面/意外数据"),
    ("L2", "身份+现象反差", "80~100字", "核心矛盾「所有人以为A→发生了B」"),
    ("L3", "背景纵深", "200~230字", "过去事件注脚(仅1~2句)+一个可贯穿全段的战略概念"),
    ("L4", "硬实力底牌·三张牌", "250~280字", "≥3个含具体数据的技术参数/亮点"),
    ("L5", "实测修罗场", "300~330字", "两个成功任务+一个失败任务的具体表现+「关掉XX后成功」反转操作"),
    ("L6", "商业降维打击", "250~280字", "价格对比具体倍数/比例+一笔普通人的账+商业操作细节及意图"),
    ("L7", "价值观收割", "250~280字", "对普通人的具体影响+思维转变(过去靠什么/未来靠什么)"),
]

# 地缘/国际版七层 (2026-08-16 用户方案: 建稿勾选赛道, 审计走对应层集)。
# 与 config/laotan-geo.txt 六模块+素材拓展引擎(A背景纵深/B横向对照/C花边)对齐:
# L6 由"商业降维打击"换成"横向对照与花边"——地缘稿几乎必有对照/花边需求而非商业数字。
GEO_LAYERS: list[tuple[str, str, str, str]] = [
    ("L1", "极速钩子", "40~50字", "一个具体的反差事实/惊人之语/意外动作"),
    ("L2", "身份+现象反差", "80~100字", "核心矛盾「双方各说各话/表面A实际B」"),
    ("L3", "背景纵深", "200~230字", "该事件的历史沿革/同类前科/条约与部署沿革 ≥2条"),
    ("L4", "硬事实牌·部署与数据", "250~280字", "≥3个具体事实: 部署动向/时间线/军力数字/官方表态原话"),
    ("L5", "一线细节与人物故事", "300~330字", "现场具体细节/当事人故事/一线人员处境(有画面感的真人真事)"),
    ("L6", "横向对照与花边", "250~280字", "可类比的其他事件/国家做法对照+一条调节奏的掌故花边"),
    ("L7", "价值观收割", "250~280字", "专家观点/趋势判断+对普通人的具体影响(安全/经济/生活)"),
]

_LAYER_IDS = [lid for lid, *_ in SEVEN_LAYERS]  # 两套层集共用 L1~L7 编号, 下游全兼容
# 洗稿注入时分桶的层 (L1/L2/L7 原文已覆盖, 不单独建桶)
_BUCKETED_LAYERS = ("L3", "L4", "L5", "L6")

_AUDIT_PROMPT = """你是一名短视频素材完备性审计员。给定一篇主稿和多条补充素材，洗稿模板要求按七层结构产出约1400~1550字口播稿。请逐层审计素材集合能否支撑该层写作。

【判定标准 — 只看事实素材，不看写作手法】
covered=true 当且仅当该层写作所需的**事实性素材**已在素材集合中齐备（按下方每层清单）。"提炼""贯穿""比喻""反差"是写作手法，由撰稿人完成，不要求素材里出现。
各层事实清单：
L3 背景纵深：过去同类事件/历史数据/行业背景事实 ≥2 条（可作注脚）
L4 硬实力底牌：主题相关的具体数据/参数/技术细节 ≥3 个
L5 实测修罗场：关于主题的实际测试/操作/演练过程 ≥1 组（含过程与结果）
L6 商业降维打击：金额/价格/成本/罚款/商业动作的具体数字 ≥2 处
L1/L2/L7 通常主稿自身即可覆盖。

【内容类型适配 — 先判断 applicable 再判 covered】
applicable 只取决于**主稿内容类型本身**，与补充素材多少无关。逐层标准：
- L4: 主题能凑出 ≥3 个具体数字就适用（统计/报告类稿件的数字就是"三张牌"，不要求是产品参数）
- L5: 需要存在关于主题的真实操作/测试/演练过程可供叙述，纯新闻统计类通常不适用
- L6: 需要存在金额/成本/价格/罚款类数字，没有就不适用
- L1/L2/L3/L7 对任何内容都适用
applicable=false 的层：covered 填 false、gaps 写"本篇内容不适用该层（原因）"、search_queries 给空数组。

只输出 JSON，不要输出其他文字：
{
  "layers": {
    "L1": {"applicable": true, "covered": true, "evidence": "素材中一句话证据(≤60字, 没有则空串)", "gaps": "缺口描述(≤60字, covered=true可为空)", "search_queries": ["仅当applicable且缺料: 1~2条中文新闻检索词, 每条≤70字符"]},
    "L2": {}, "L3": {}, "L4": {}, "L5": {}, "L6": {}, "L7": {}
  },
  "item_tags": {"1": ["L3","L4"], "2": ["L5"]},
  "summary": "一句话总体判断(≤50字, 若有不适用的层请点名)"
}
item_tags: 把每条编号素材标注到它最能支撑的层(可多标); 编号对应输入里的【素材N】。

search_queries 写法（重要，检索词质量直接决定补搜有效性）:
- 你在为编辑生成「拿去搜索引擎找素材」的检索词。必须用大众/新闻语言，锚定主稿的具体事件、公司、人物、数字。
- 禁止模板术语入检索词——"认知压制""换计价单位""三张牌""修罗场""战略概念""翻车""反转""阳谋""降维打击"都是内部行话。
- 禁止抽象泛化词（如"数据分析""安全策略"这类万金油词）。
- 好例子（主稿=数据泄露创新高）: "2025 数据泄露 事件 盘点"、"数据泄露 平均损失 IBM 报告"
- 坏例子: "数据泄露 防护 测试 翻车 现场 实录"（行话堆砌，检索无效）"""

# 地缘版审计 prompt (2026-08-16): 层集换成 GEO_LAYERS, 事实清单与 applicable 规则按地缘内容重写
_AUDIT_PROMPT_GEO = """你是一名短视频素材完备性审计员。给定一篇主稿和多条补充素材，地缘/国际赛道洗稿模板（六模块+背景纵深/横向对照/花边拓展引擎）要求产出约1800~2500字口播稿。请逐层审计素材集合能否支撑该层写作。

【判定标准 — 只看事实素材，不看写作手法】
covered=true 当且仅当该层写作所需的**事实性素材**已在素材集合中齐备（按下方每层清单）。"提炼""贯穿""比喻""反差"是写作手法，由撰稿人完成，不要求素材里出现。
各层事实清单：
L3 背景纵深：该事件/冲突的历史沿革、同类前科、条约与部署沿革事实 ≥2 条
L4 硬事实牌：部署动向/时间线/军力与预算数字/官方表态原话 ≥3 个
L5 一线细节与人物故事：现场具体细节、当事人或一线人员的故事 ≥1 组（有画面感的真人真事）
L6 横向对照与花边：可类比的其他事件或国家做法 ≥1 条 + 可调节奏的掌故花边 ≥1 条（花边须真实可考）
L1/L2/L7 通常主稿自身即可覆盖。

【内容类型适配 — 先判断 applicable 再判 covered】
applicable 只取决于**主稿内容类型本身**，与补充素材多少无关。逐层标准：
- L4: 主题能凑出 ≥3 个具体事实（部署/日期/数字/表态原话）就适用
- L5: 需要存在现场细节/人物故事可供叙述，纯政策声明类通常不适用
- L6: 地缘稿几乎总有可类比事件，通常适用；但纯双边技术性协议（无对照价值）不适用
- L1/L2/L3/L7 对任何内容都适用
applicable=false 的层：covered 填 false、gaps 写"本篇内容不适用该层（原因）"、search_queries 给空数组。

只输出 JSON，不要输出其他文字：
{
  "layers": {
    "L1": {"applicable": true, "covered": true, "evidence": "素材中一句话证据(≤60字, 没有则空串)", "gaps": "缺口描述(≤60字, covered=true可为空)", "search_queries": ["仅当applicable且缺料: 1~2条中文新闻检索词, 每条≤70字符"]},
    "L2": {}, "L3": {}, "L4": {}, "L5": {}, "L6": {}, "L7": {}
  },
  "item_tags": {"1": ["L3","L4"], "2": ["L5"]},
  "summary": "一句话总体判断(≤50字, 若有不适用的层请点名)"
}
item_tags: 把每条编号素材标注到它最能支撑的层(可多标); 编号对应输入里的【素材N】。

search_queries 写法（重要，检索词质量直接决定补搜有效性）:
- 你在为编辑生成「拿去搜索引擎找素材」的检索词。必须用大众/新闻语言，锚定主稿的具体事件、国家、人物、舰名、数字。
- 禁止模板术语入检索词——"背景纵深""横向对照""花边""修罗场""铁律"都是内部行话。
- 禁止抽象泛化词（如"国际局势""军事动态"这类万金油词）。
- 好例子（主稿=霍尔木兹海峡事件）: "林肯号航母 部署天数"、"伊朗 阿曼 霍尔木兹 协议"
- 坏例子: "海峡 局势 分析 深度"（泛化词，检索无效）"""


def _audit_prompt_for(track: str) -> str:
    return _AUDIT_PROMPT_GEO if (track or "tech") == "geo" else _AUDIT_PROMPT

# 长度控制: 审计输入 / 素材块 (单条降 1200, 总量 40000 — 38+ 条素材不截断)
_AUDIT_TOTAL_CAP = 40000
_ARTICLE_CAP = 12000
_ITEM_AUDIT_CAP = 1200
_CONTEXT_TOTAL_CAP = 12000
_ITEM_CONTEXT_CAP = 800  # 注入单条只喂关键事实, 不喂整篇 (实验结论: 全文注入稀释主题)
_PER_LAYER_LIMIT = 3     # 每层最多注入条数 (精选不堆量)

# 模板行话黑名单: LLM 偶发把内部术语混进检索词, 代码层硬过滤
_QUERY_JARGON = (
    "认知压制", "换计价单位", "三张牌", "修罗场", "战略概念", "翻车",
    "反转", "阳谋", "降维打击", "硬实力底牌", "价值观收割", "极速钩子",
    "身份接管", "现象反差", "背景纵深", "商业操作细节",
)


def _clean_query(q: str) -> str:
    """删检索词里的行话; 删完为空则丢弃."""
    cleaned = q
    for word in _QUERY_JARGON:
        cleaned = cleaned.replace(word, " ")
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) >= 6 else ""  # 清完太短视为无效


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…(截断)"


def build_audit_input(
    article_text: str,
    items: list[MaterialItem],
    track: str = "tech",
    *,
    prev_audit: dict[str, Any] | None = None,
) -> tuple[str, dict[str, int]]:
    """审计 prompt + 主稿 + 编号素材 → (prompt, item_id→编号映射).

    增量模式 : prev_audit 带 item_numbers 时 — 已审过的 item 只给单行
    (编号|标题|已有层), 只有新 item 给全文 → 补搜轮 prompt 从 ~40K 降到 <8K;
    编号沿用上一轮 (新 item 从 max+1 续), item_tags 编号跨轮稳定。
    返回映射供调用方回填 layer_tags (修复旧版按位置反推在 fetch_ok 翻转时错位)。
    """
    prev_nums: dict[str, int] = {}
    prev_tags: dict[str, Any] = {}
    if prev_audit:
        prev_nums = {str(k): int(v) for k, v in (prev_audit.get("item_numbers") or {}).items()}
        prev_tags = prev_audit.get("item_tags") or {}
    next_n = (max(prev_nums.values()) + 1) if prev_nums else 1
    numbering: dict[str, int] = {}

    parts = [_audit_prompt_for(track), "", "【主稿】", _truncate(article_text, _ARTICLE_CAP)]
    used = sum(len(p) for p in parts)
    fresh_lines: list[str] = []
    known_lines: list[str] = []
    for item in items:
        iid = str(item.id)
        if iid in prev_nums:
            n = prev_nums[iid]
            tags = prev_tags.get(str(n)) or []
            known_lines.append(f"{n}|{item.title or item.media or ''}|已有层={','.join(tags) or '无'}")
        else:
            n = next_n
            next_n += 1
            label = item.title or item.media or (item.source_url or "")[:60] or f"素材{n}"
            fresh_lines.append(f"\n\n【素材{n}】({item.source_type}|{label})\n" + _truncate(item.raw_text, _ITEM_AUDIT_CAP))
        numbering[iid] = n

    if known_lines:
        parts.append("\n\n【已审素材(上一轮已覆盖, 仅列编号与已有层 — 本轮不必重标)】\n" + "\n".join(known_lines))
    used += sum(len(x) for x in fresh_lines)
    for chunk in fresh_lines:
        if used > _AUDIT_TOTAL_CAP:
            break
        parts.append(chunk)
    if fresh_lines:
        parts.append("\n\n【本轮新增素材到此结束】item_tags 只输出新增素材的编号, 已审素材沿用其已有层。")
    else:
        parts.append("\n\n【素材集合到此结束，请输出审计JSON】")
    return "".join(parts), numbering


def _normalize_layer(raw: Any) -> dict[str, Any]:
    """单层审计结果规整, 字段异常回退默认."""
    raw = raw if isinstance(raw, dict) else {}
    queries = raw.get("search_queries")
    if not isinstance(queries, list):
        queries = []
    return {
        "applicable": bool(raw.get("applicable", True)),
        "covered": bool(raw.get("covered")),
        "evidence": str(raw.get("evidence") or "")[:120],
        "gaps": str(raw.get("gaps") or "")[:120],
        "search_queries": [str(q).strip()[:70] for q in queries if str(q).strip()][:2],
    }


def audit_package(
    article_text: str, items: list[MaterialItem], prev_audit: dict[str, Any] | None = None,
    track: str = "tech",
) -> dict[str, Any] | None:
    """七层覆盖审计. LLM JSON 输出 → 规整 dict; 解析失败返回 None.

    track : tech=科技七层 / geo=地缘七层(L6=横向对照与花边), 建稿勾选驱动。
    prev_audit: 上一轮审计 (补搜/加素材后重审时传入).
    - prompt 注入上一轮 applicable 判定, 要求 LLM 保持一致;
    - 代码层强制锁定 applicable = 上一轮值 (LLM 判定在补搜后偶发翻转
      "绿→不适用", 素材变多不该改变内容类型是否适用)。首轮判定为准,
      需重置走「新建素材包」。
    """
    ok_items = [it for it in items if it.raw_text and it.raw_text.strip()]
    prompt, numbering = build_audit_input(article_text, ok_items, track=track, prev_audit=prev_audit)
    incremental = bool((prev_audit or {}).get("item_numbers"))
    if prev_audit and isinstance(prev_audit.get("layers"), dict):
        prev_lines = []
        for lid in _LAYER_IDS:
            pl = prev_audit["layers"].get(lid) or {}
            if pl:
                prev_lines.append(
                    f"{lid}: applicable={'true' if pl.get('applicable', True) else 'false'}"
                    + (f", 上轮covered={'true' if pl.get('covered') else 'false'}, 上轮证据={str(pl.get('evidence') or '')[:40]}" if incremental else "")
                )
        if prev_lines:
            prompt += (
                "\n\n【上一轮判定 — applicable 必须与上一轮完全一致】\n"
                + "\n".join(prev_lines)
                + "\n(applicable 只取决于主稿内容类型, 补充素材变化不影响它; 本轮基于新增素材重判 covered/evidence/gaps/search_queries"
                + ("; 新增素材可能让上轮缺口变已覆盖" if incremental else "")
                + ")"
            )
    # 重试 ×3 (2026-08-16 实测: 补搜后重审偶发 LLM JSON 解析失败 → 包卡 failed,
    # 用户被灰按钮困死; 审计是结构化输出, 一次失败重跑比让人重按划算)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            raw = _call(prompt, json_mode=True, max_tokens=8000, model="pro", temperature=0.2,
                        enable_thinking=True)  # 2026-08-22: 大 prompt 必须 thinking 才不空响应;
                        # thinking 的 reasoning 会占 ~5-8K tokens, max_tokens 3000 被耗尽 → content 空
            data = _extract_json(raw)
            break
        except Exception as exc:
            last_exc = exc
            logger.warning("[material] audit LLM attempt %d failed: %s", attempt + 1, exc)
            data = None
    if data is None:
        if last_exc is not None:
            logger.warning("[material] audit LLM call failed after 3 attempts")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("layers"), dict):
        return None
    layers_raw = data["layers"]
    if not all(lid in layers_raw for lid in _LAYER_IDS):
        return None
    prev_layers = (prev_audit or {}).get("layers") or {}
    normalized: dict[str, dict[str, Any]] = {}
    for lid in _LAYER_IDS:
        layer = _normalize_layer(layers_raw[lid])
        if lid in prev_layers and prev_layers[lid]:
            layer["applicable"] = bool(prev_layers[lid].get("applicable", True))  # 锁定
        normalized[lid] = layer
    item_tags_raw = data.get("item_tags")
    item_tags = {}
    if isinstance(item_tags_raw, dict):
        for key, tags in item_tags_raw.items():
            if isinstance(tags, list):
                valid = [t for t in (str(x).strip() for x in tags) if t in _LAYER_IDS]
                if valid:
                    item_tags[str(key)] = valid
    # 增量合并 (2026-08-25): 本轮 item_tags 只收新编号, 旧编号沿用上轮
    # (新 item 从 max+1 续号, 新编号集合可精确判定); 全量模式(无 prev)行为不变。
    prev_tags = (prev_audit or {}).get("item_tags") or {}
    if incremental:
        new_numbers = {str(n) for n in numbering.values()} - {
            str(v) for v in (prev_audit.get("item_numbers") or {}).values()
        }
        merged = {k: v for k, v in prev_tags.items()}
        merged.update({k: v for k, v in item_tags.items() if k in new_numbers})
        item_tags = merged
    round_no = int((prev_audit or {}).get("round") or 0) + 1
    return {
        "layers": normalized,
        "item_tags": item_tags,
        "summary": str(data.get("summary") or "")[:100],
        "item_numbers": numbering,
        "round": round_no,
    }


def collect_gap_queries(audit_json: dict[str, Any] | None,
                        research_hints: list[str] | None = None) -> list[str]:
    """缺口层 search_queries 汇总: 仅 applicable 层; 行话过滤、去重、≤70字、cap 6 条.

    research_hints : 解构层 research 资料清单并入补搜候选池 —
    评论层挖出的"编辑该调研什么"直接变成可勾选补搜词 (此前只躺在展示面板).
    带「调研·」前缀区分来源, 计入总 cap.
    """
    if not audit_json and not research_hints:
        return []
    seen: set[str] = set()
    out: list[str] = []
    layers = (audit_json or {}).get("layers") or {}
    for lid in _LAYER_IDS:
        layer = layers.get(lid) or {}
        if not layer.get("applicable", True):
            continue  # 不适用的层不补搜
        if layer.get("covered") and not layer.get("gaps"):
            continue
        for q in layer.get("search_queries") or []:
            q = _clean_query(str(q).strip()[:70])
            if q and q not in seen:
                seen.add(q)
                out.append(q)
    # 解构层调研清单并入 (去重后仍受总 cap 约束)
    for hint in research_hints or []:
        q = _clean_query(str(hint).strip()[:64])
        if q and q not in seen:
            seen.add(q)
            out.append(f"调研·{q}")
    return out[:8]


def _item_header(item: MaterialItem) -> str:
    label = item.title or "无题"
    src = item.source_url or ""
    media = item.media or ""
    if src:
        return f"[来源: {media or '网络'}|{src}] {label}"
    return f"[来源: {media or '手动粘贴'}] {label}"


def build_material_context_block(items: list[MaterialItem], audit_json: dict[str, Any] | None) -> str:
    """洗稿注入块 — 精选制 .

    只注入审计标注到 L3~L6 的素材, 每层最多 3 条 (配料表不是自助餐):
    - search 素材: 必须有审计层标注才入, 未归类 = 噪音, 全部丢弃
    - manual/url 素材: 用户手动加的, 最多 2 条兜底 (用户意图优先)
    - 单条截 800 字 (只喂关键事实, 不喂整篇水文)
    """
    ok_items = [it for it in items if it.raw_text and it.raw_text.strip()]
    if not ok_items:
        return ""
    item_tags = (audit_json or {}).get("item_tags") or {}
    buckets: dict[str, list[tuple[str, MaterialItem]]] = {lid: [] for lid in _BUCKETED_LAYERS}
    manual_fallback: list[tuple[str, MaterialItem]] = []
    for i, item in enumerate(ok_items, start=1):
        tags = item_tags.get(str(i)) or []
        placed = False
        for tag in tags:
            if tag in buckets:
                buckets[tag].append((str(i), item))
                placed = True
        # 手动加的素材即使未标注也保留 (用户明确想喂的); 搜索噪音不保留
        if not placed and item.source_type in ("manual", "url"):
            manual_fallback.append((str(i), item))

    header = (
        "【以下是编辑精选的补充素材（按层归档，只含关键事实）。"
        "写作时按层取用，数字与细节以素材为准；未覆盖的层不要编造。】"
    )
    parts = [header]
    used = len(header)
    layer_names = {lid: name for lid, name, _, _ in SEVEN_LAYERS}

    def _emit(bucket: list[tuple[str, MaterialItem]], title: str, limit: int) -> None:
        nonlocal used
        if not bucket:
            return
        section = f"\n◆ {title}"
        for _, item in bucket[:limit]:  # 每层限量, 精选不堆量
            chunk = f"\n- {_item_header(item)}\n  {_truncate(item.raw_text, _ITEM_CONTEXT_CAP)}"
            section += chunk
        if used + len(section) > _CONTEXT_TOTAL_CAP:
            return
        parts.append(section)
        used += len(section)

    for lid in _BUCKETED_LAYERS:
        _emit(buckets[lid], f"第{lid[1]}层·{layer_names[lid]}可用素材", limit=_PER_LAYER_LIMIT)
    _emit(manual_fallback, "编辑手动补充素材", limit=2)

    # 审计缺口提示: 让模型明确知道哪些层没料/不适用, 别硬编
    gaps: list[str] = []
    layers = (audit_json or {}).get("layers") or {}
    for lid in _LAYER_IDS:
        layer = layers.get(lid) or {}
        if not layer.get("applicable", True):
            name = layer_names[lid]
            gaps.append(f"{lid} {name}: 该层不适用本篇内容，用已有素材按最接近的角度改写，不要编造")
        elif not layer.get("covered") or layer.get("gaps"):
            name = layer_names[lid]
            gap_text = layer.get("gaps") or "素材不足"
            gaps.append(f"{lid} {name}: {gap_text}")
    tail = "\n【审计缺口提示】" + ("；".join(gaps) if gaps else "七层素材全部覆盖")
    parts.append(tail)
    return "".join(parts)[:_CONTEXT_TOTAL_CAP + 200]


def batch_fetch_urls(urls: list[str]) -> list[dict[str, Any]]:
    """并行抓取多 URL (ThreadPool max_workers=4, 先例 video_tagging jobs).

    单条失败不中断, 返回 [{url, ok, title, raw_text, error}] 按输入顺序.
    """
    results: dict[int, dict[str, Any]] = {}

    def _fetch(idx_url: tuple[int, str]) -> tuple[int, dict[str, Any]]:
        idx, url = idx_url
        try:
            # 证据图管线①: 顺手提取候选图 (零额外请求)
            r = fetch_url(url, with_images=True)
            return idx, {
                "url": url,
                "ok": bool(r.get("ok")),
                "title": r.get("title") or "",
                "raw_text": r.get("raw_text") or "",
                "error": r.get("error") or "",
                "images": r.get("images") or [],
            }
        except Exception as exc:  # 单条失败不拖垮整批
            return idx, {"url": url, "ok": False, "title": "", "raw_text": "", "error": str(exc), "images": []}

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="material") as pool:
        futures = [pool.submit(_fetch, (i, u)) for i, u in enumerate(urls)]
        for fut in as_completed(futures):
            idx, res = fut.result()
            results[idx] = res
    return [results[i] for i in range(len(urls))]


def search_and_ingest(
    db, package: MaterialPackage, queries: list[str], *, count: int | None = None
) -> int:
    """智谱补搜并入包: 按 link 去重. 额度/到期/key 未配置 → 致命中断提示;
    其余单条失败 (网络/业务码) log 后继续. 返回新入库条数.
    """
    from ..config import get_config

    cfg_count = count or get_config().zhipu.count
    existing_links = {it.source_url for it in package.items if it.source_url}
    # 同源近似去重 key: 同媒体 + 标题前 10 字 (如"关注！数据安全新动态"4月/5月系列只留一条)
    existing_dedupe = {
        ((it.media or "").split(" ")[0], (it.title or "")[:10]) for it in package.items
    }
    added = 0
    for q in queries:
        try:
            results = zhipu_web_search(q, count=cfg_count)
        except ZhipuUnavailableError:
            raise  # 功能不可用 (key/额度/到期) → 中断整轮, material_error 明确提示
        except ZhipuSearchError as exc:
            logger.warning("[material] search failed for %r, skip: %s", q, exc)
            continue
        for r in results:
            link = r.get("link") or ""
            if link and link in existing_links:
                continue
            existing_links.add(link)
            text = r.get("content") or ""
            if not text.strip():
                continue
            media = (r.get("media") or "").strip()
            if r.get("publish_date"):
                media = f"{media} {r['publish_date']}".strip()
            title = (r.get("title") or "").strip()
            dedupe_key = (media, title[:10])
            if dedupe_key in existing_dedupe:
                continue  # 同源系列水文, 留一条够了
            existing_dedupe.add(dedupe_key)
            db.add(MaterialItem(
                package_id=package.id,
                source_type="search",
                title=title or None,
                source_url=link or None,
                media=media[:128] or None,
                raw_text=text,
                fetch_ok=True,
                char_count=len(text),
                search_query=q[:256],
            ))
            added += 1
    return added
