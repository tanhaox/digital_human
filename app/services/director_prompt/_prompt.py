"""Director prompt — 用户提示词装配 (导演 LLM 输入构建).

build_director_prompt 是编排函数 (压线 ≤70 不拆): 无出镜裁剪 → 画幅 →
catalog_mode 判定 → 词表/全量 JSON → 选词约束 → timing → 管线约束。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.config import get_config
from app.schemas import get_video_format_spec
from app.services.director_prompt._constants import (
    HARD_DIMENSIONS,
    LOCATION_VALUES,
    ORIENTATION_VALUES,
    PEOPLE_VALUES,
    SOFT_DIMENSIONS,
    VISUAL_DIRECTOR_V2_PATH,
    VOCABULARY_PACK_PATH,
)
from app.services.director_prompt._catalog import mock_material_catalog
from app.services.director_prompt._strip_host import strip_host_mode
from app.services.director_prompt._vocabulary import load_vocabulary_pack

logger = logging.getLogger(__name__)

__all__ = ["build_director_prompt", "load_director_prompt"]


# ── 管线映射: 标签 → (中文名, 工作流列表) ──
# "P线 在线素材" (2026-09-04): P 线不再只有 Pexels — evidence_image 证据图
# 同走 pipeline p, 标签改为覆盖两类在线素材 (提示词管线约束块自动含)。
_PIPELINE_MAP: dict[str, tuple[str, list[str]]] = {
    "c": ("C线 ComfyUI", ["host", "mixed_host_broll"]),
    "p": ("P线 在线素材", ["broll_pexels", "evidence_image"]),
    "h": ("H线 HuggingFace", ["hf_chart", "hf_title"]),
}


def load_director_prompt() -> str:
    """Load the v2 director system prompt from disk."""
    if VISUAL_DIRECTOR_V2_PATH.exists():
        return VISUAL_DIRECTOR_V2_PATH.read_text(encoding="utf-8").strip()
    return (
        "你是一位资深财经短视频视觉导演。请为每句口播输出精确的画面方案，"
        "最终输出纯 JSON 数组，每个元素包含 line_id/text/visual_type/intensity/emotion 字段。"
    )


def _format_frame_spec(video_format: str | None) -> str:
    """Return the frame-format block injected into the director prompt.

    横/竖/方 三种画幅给 LLM, 让它按画幅选 broll 构图词 (2026-08-01).
    """
    spec = get_video_format_spec(video_format)
    fmt = video_format or "portrait"
    lines = [
        f"\n\n## 补充输入4：本片画幅规格（硬性，选择 broll 构图时必须遵守）",
        f"当前画幅: {fmt}（{spec['label']}），输出分辨率 {spec['width']}x{spec['height']}。",
    ]
    if fmt == "portrait":
        lines.append("- 竖屏（当前画幅）: broll 素材必须是竖向构图(如高层楼宇、竖版街道、人物全身), 禁止横向宽景; keywords 用竖向主体词。")
        lines.append("- 横屏: broll 素材是横向宽景构图(城市天际线/公路/大海), keywords 用横向场景词。")
    elif fmt == "landscape":
        lines.append("- 横屏（当前画幅）: broll 素材必须是横向宽景构图(如城市天际线、公路、大海), 禁止竖向特写; keywords 用横向场景词(如 skyline/landscape)。")
        lines.append("- 竖屏: broll 素材是竖向构图(高层楼宇/街道/人物全身), keywords 用竖向主体词。")
    else:
        lines.append("- 方形（当前画幅）: broll 素材构图居中均衡, 主体不要贴边; keywords 用居中主体词。")
    lines.append(f"当前画幅是 **{fmt}**，所有 broll_pexels 的 keywords 必须选与该画幅匹配的构图词。")
    return "\n".join(lines)


def _pipeline_substitution_lines(
    forbidden_workflows: list[str],
    enabled_pipelines: set[str],
) -> list[str]:
    """替代方案指引 — 按被禁工作流 + 仍启用管线组合出可替代视觉."""
    lines: list[str] = []
    if "host" in forbidden_workflows and "mixed_host_broll" in forbidden_workflows:
        if "p" in enabled_pipelines:
            # 首帧禁静态卡 (2026-09-04 用户裁决): 开场让位实拍冲击画面
            lines.append("- 开场首 slot 用 broll_pexels 冲击画面 (禁 hf 字幕卡开场); 收尾用 broll_pexels 情感画面或 hf_title 来源卡")
            if "h" in enabled_pipelines:
                lines.append("- hf_chart/hf_title 用于正文数据段与尾部来源卡, 不做第 1 个 slot")
        elif "h" in enabled_pipelines:
            lines.append("- 开场/收尾用 hf_chart 替代 host 出镜 (P 线也禁用时的兜底)")
        else:
            lines.append("- 开场/收尾用 broll_local 替代 (本地素材兜底)")
    if "broll_pexels" in forbidden_workflows:
        if "c" in enabled_pipelines:
            lines.append("- 需要空镜时用 mixed_host_broll 替代 broll_pexels")
        elif "h" in enabled_pipelines:
            lines.append("- 需要空镜时用 hf_chart 替代 broll_pexels")
        else:
            lines.append("- 需要空镜时用 broll_local 替代")
    if "hf_chart" in forbidden_workflows and "hf_title" in forbidden_workflows:
        if "c" in enabled_pipelines:
            lines.append("- 数据可视化/标题卡用 host 出镜替代")
        else:
            lines.append("- 数据可视化/标题卡用 broll_local 替代")
    return lines


def _build_pipeline_constraint_block(enabled_pipelines: set[str] | None) -> str:
    """Build a constraint block telling the LLM which pipelines are disabled."""
    if enabled_pipelines is None:
        return ""  # 全部启用, 不需要约束

    disabled: list[str] = []
    forbidden_workflows: list[str] = []
    for tag, (label, wfs) in _PIPELINE_MAP.items():
        if tag not in enabled_pipelines:
            disabled.append(f"- {label} → 禁止工作流: {', '.join(wfs)}")
            forbidden_workflows.extend(wfs)

    if not disabled:
        return ""

    lines = [
        "\n\n## 管线限制（硬性约束 — 必须遵守！）",
        f"以下管线已被用户禁用，**绝对不能**为这些管线生成 slot:",
        *disabled,
        "",
        "**替代方案指引**:",
    ]
    lines.extend(_pipeline_substitution_lines(forbidden_workflows, enabled_pipelines))
    return "\n".join(lines)


def _format_evidence_pool_block(pool: list[dict[str, Any]] | None) -> str:
    """证据图池注入 (2026-09-05): 不注入则 LLM 永远当"素材包无截图"处理 —
    全库仅 1 个 evidence slot 实锤。池内容 + 硬规则 (claim 抄图内数字) 一起给,
    情绪稿口播不念数字也能靠 claim 对齐 gate/匹配器 (两者都数字优先)。"""
    if not pool:
        return (
            "\n\n## 补充输入6：证据图池\n"
            "本片素材包无合格证据图 → **evidence_image 一律禁用**，"
            "数据段用 hf_chart / broll_pexels。"
        )
    lines = [
        "\n\n## 补充输入6：证据图池（本片素材包的真实截图 — evidence_image 专用）",
        f"本片素材包有 {len(pool)} 张合格证据图（VLM 已核：图表/榜单/跑分，无重水印）：",
    ]
    for i, p in enumerate(pool, start=1):
        nums = " ".join(str(n) for n in (p.get("numbers") or [])[:6])
        src = (p.get("source_media") or "").strip()
        lines.append(
            f"- E{i} [{p.get('kind')}|q{p.get('quality')}] {p.get('desc_zh', '')[:50]}"
            f"{' | 图上数字: ' + nums if nums else ''}{' | 源: ' + src if src else ''}"
        )
    lines += [
        "",
        "使用规则（硬性）：",
        "- 口播段讲到**跑分/基准/榜单/价格对比**时——**即使口播没念具体数字**——"
        "只要上表有图能佐证该段，优先选 `evidence_image`（真实截图说服力 > hf_chart > broll）。",
        "- 该 slot 的 `claim` 必须写「主题词 + 数字」：文字图从上表抄一个图内核心数字"
        "（如 \"GPT-6 Astra 在ARC-AGI-3基准跑分99.9%接近满分\"）；实物图（kind=photo）"
        "从口播抄数字。`keywords` 含上表主题词——系统按 claim 数字+关键词从池内自动选图，"
        "数字不对齐会降级 broll。",
        "- 实物图（kind=photo，如芯片/汽车/产品照）可佐证产品/硬件相关数据段；"
        "与文字图都匹配时优先文字图（数字重合力更强）。",
        "- 段落与所有图内容都不沾边时禁止硬凑，用 broll_pexels。",
    ]
    return "\n".join(lines)


def _format_vocabulary_constraint_block() -> str:
    """词表包模式下追加的选词硬约束（broll_local 全维度词表选词; broll_pexels keywords 自由）。

    解耦 (2026-08-09): broll_local 的 9 维度 + keywords 从词表包选词;
    broll_pexels 的 keywords 是**自由画面搜索词**, 不受词表限制 (专有名词/
    实时事件词如 特朗普/伊朗 允许), 仅 9 维度若输出仍需从词表包对应分组取
    ——9 维度只供本地素材优先碰撞用, 不参与 Pexels 下载搜索。
    """
    return (
        "\n\n## 补充输入2说明：关键词词表包选词规则（硬性）\n"
        "素材库以**按维度分组的词表包**提供（而非全量清单），每个维度 1~N 个可检索词。\n"
        f"- 硬维度（{ '、'.join(HARD_DIMENSIONS) }）：选词必须严格取该维度枚举值，用于本地硬过滤。\n"
        f"- 软维度（{ '、'.join(SOFT_DIMENSIONS) }）：从词表包对应分组选词。\n"
        f"- **broll_local 的 params 必须输出全部 9 个维度**"
        f"（{ '、'.join(HARD_DIMENSIONS) } + { '、'.join(SOFT_DIMENSIONS) }），"
        "每维度最少 1-2 个词（scenes/shot_types 最少 1 个；location/orientation/people/tone/motion_level/content_density/time_of_day 最少 1 个），keywords 也从词表包 keywords 分组取。\n"
        "- **broll_pexels 的 keywords 是自由画面搜索词**，不受词表限制：可用词表外词（专有名词/实时事件/具体画面，如 特朗普、伊朗、雨后街道），第 1 个必须是全局主体关键词（规则5）；location/people 可选硬维度用于定向；**必须同时输出 9 维度**（scenes/shot_types/tone/motion_level/content_density/time_of_day，词从词表包对应分组取，与 broll_local 同格式），供本地素材优先碰撞命中——9 维度不参与 Pexels 下载搜索，不会限制在线下载；缺 9 维度则本地碰撞直接跳过（P 线全部下载）。\n"
        "- hard 维度只允许枚举值"
        f"（location: {'/'.join(LOCATION_VALUES)}，orientation: {'/'.join(ORIENTATION_VALUES)}，people: {'/'.join(PEOPLE_VALUES)}）。\n"
        "- 词不得互相冲突：location 为 domestic 的 slot 不得再出现 foreign 词；全片地域语义必须与口播一致（讲中国产业链绝不允许 foreign）。\n"
        "- **本地匹配规则（硬性）**: 门槛维 scenes/shot_types/tone 命中率 ≥75%（3 中 ≥2）才算符合；加分维 motion_level/content_density/time_of_day 命中加分、不排除。location 命中不了 domestic 时才允许放宽到 foreign（系统会自动标记，不静默）。选词不全或词不在包内会导致本地碰撞不达标，下游自动转 broll_pexels 在线下载。\n"
    )


def build_director_prompt(
    script_text: str,
    segment_timings: list[dict[str, Any]],
    material_catalog: dict[str, Any] | None = None,
    video_format: str | None = None,
    script_title: str | None = None,
    enabled_pipelines: set[str] | None = None,
    visual_intent: list[dict[str, Any]] | None = None,
    visual_theme: str | None = None,
    evidence_pool: list[dict[str, Any]] | None = None,
) -> str:
    """Assemble user prompt for the director LLM.

    Args:
        enabled_pipelines: 启用的管线集合 (e.g. {"c","p","h"}). None=全部启用.
        visual_intent: 可选, 稿件结构意图 (钩子/预埋/回收/呼吸点/金句 → 建议画面情绪).
            从爆品改造稿的结构标记提取, 让导演按"观众实际听到的新稿 + 结构意图"配画面, 而非盲配.
        visual_theme: 可选, 人物级视觉主题 (persona.visual_theme, 如科技/地缘场景词).
            导演按此选全局主体关键词, 替代默认地域词.
        evidence_pool: 可选, 素材包合格证据图池 (collect_evidence_pool 产物).
            注入为补充输入6 — P 线禁用时不注入 (约束块已禁 evidence_image).
    """
    prompt = load_director_prompt()
    # 无出镜模式: C 线禁用 (用户只开 P/H 或全关) 时, 直接在系统提示词层移除 host 规则
    # 注: strip_host_mode 会改变前缀 — 两种模式各自的缓存前缀依然稳定
    if enabled_pipelines is not None and "c" not in enabled_pipelines:
        prompt = strip_host_mode(prompt)

    # ── 前缀缓存 (2026-08-26): 静态模板(21KB)原样作前缀, 不再中部 replace —
    # 旧版三处占位符替换(标题/口播/素材清单都在模板中部)令前缀从第一字符就变,
    # DeepSeek 自动前缀缓存恒 miss(实测 cached_tokens=0)。动态内容全部 append
    # 到尾部【动态输入】区, 同一静态前缀跨任务复用 → 输入费用降 ~60%。
    dynamic_parts: list[str] = []
    dynamic_parts.append(f"\n\n# 【动态输入】(本条消息末尾, 编号对应上方输入0-4说明)\n\n"
                         f"## 输入0:原文标题\n{(script_title or '（无标题）').strip()}")
    dynamic_parts.append(f"\n\n## 输入1:口播脚本(全文, 每行一句, 含 || 停顿标记)\n{script_text.strip()}")

    # 画幅上下文 (2026-08-01): 导演规划必须知道当前横/竖/方, 才能按画幅选构图词
    dynamic_parts.append(_format_frame_spec(video_format))

    # 输入2 (ID-034): catalog_mode 判定。config 未加载(脚本直接调用)时用默认 "vocabulary";
    # 显式 "full" 才注入全量 catalog, 其余(默认/未知)一律走词表包。
    catalog_mode = "vocabulary"
    try:
        if get_config().defaults.director_catalog_mode == "full":
            catalog_mode = "full"
    except Exception:
        pass  # config 未加载 → 保持默认 vocabulary

    if catalog_mode == "vocabulary":
        pack = load_vocabulary_pack()
        # 词表包文件缺失(首次部署/尚未打标) → fallback 全量 catalog, 保证 prompt 仍可用
        if pack is None:
            logger.warning(
                "catalog_mode=vocabulary but pack missing (%s), falling back to full catalog",
                VOCABULARY_PACK_PATH,
            )
            catalog = material_catalog or mock_material_catalog()
            catalog_json = json.dumps(catalog, ensure_ascii=False, indent=2)
        else:
            catalog_json = json.dumps(pack, ensure_ascii=False, indent=2)
    else:
        catalog = material_catalog or mock_material_catalog()
        catalog_json = json.dumps(catalog, ensure_ascii=False, indent=2)
    dynamic_parts.append(f"\n\n## 输入2:素材库清单(JSON)\n{catalog_json}")

    # 词表包模式下补充选词约束（全维度 + 每维度 ≥1-2 词）
    if catalog_mode == "vocabulary":
        dynamic_parts.append(_format_vocabulary_constraint_block())

    # 证据图池注入 (2026-09-05): P 线启用才注 (禁用时约束块已封 evidence_image,
    # 注入反而诱导 LLM 规划被闸门降级的 slot)
    if enabled_pipelines is None or "p" in enabled_pipelines:
        dynamic_parts.append(_format_evidence_pool_block(evidence_pool))

    # ── 输入3 段视图 (2026-08-26 分段先行): 代码把句预聚成候选段(12~25s 时长窗),
    # LLM 面对的是 ~20 个段的组合决策而非 83 句逐句规划 — v4-pro 对逐句×44禁令
    # 的 reasoning 失控(实测 13.5K~24.4K)的直接解法。slot 通常引用整段的全部 S;
    # 开场卡/标题卡等特殊短 slot 可只引段内 1-2 句; 相邻段可合并。
    blocks = _aggregate_segments(segment_timings)
    block_lines = []
    for b in blocks:
        preview = b["preview"]
        block_lines.append(
            f"{b['no']} | {b['start']:.2f}-{b['end']:.2f}s | {b['s_from']}-{b['s_to']} | {preview}"
        )
    compact_lines = [
        f"S{i:03d}|{float(t.get('start', 0)):.2f}-{float(t.get('end', 0)):.2f}"
        for i, t in enumerate(segment_timings, start=1)
    ]
    dynamic_parts.append(
        "\n\n## 输入3:段落视图(候选段)与句编号\n"
        "每个 slot 的 start/end 必须落在真实时间范围内, 不要超出音频总时长。\n"
        "slot 用 segment_refs 引用句编号(S 编号, 如 [\"S001\",\"S002\",\"S003\",\"S004\"]), "
        "禁止复写口播文本——系统按编号自动填回。\n"
        "默认:一个候选段 = 一个 slot(引用该段全部 S); 开场卡/标题卡可只引段内前 1-2 句; "
        "节奏需要时可合并相邻段。句级时间表附后(对齐 start/end 用)。\n\n"
        "【候选段】(段号|起止|句范围|内容预览)\n" + "\n".join(block_lines)
        + "\n\n【句级时间表】\n" + "\n".join(compact_lines)
    )

    # 管线约束 (2026-08-03): 告诉 LLM 哪些管线禁用, 避免生成无效 slot
    constraint = _build_pipeline_constraint_block(enabled_pipelines)
    if constraint:
        dynamic_parts.append(constraint)

    # 视觉意图注入 (2026-08-11): 把爆品改造稿的结构意图传给导演, 让它按
    # "观众实际听到的新稿 + 每段意图"配画面, 而非盲配. 这是视觉层"导演盲盒"的解法.
    if visual_intent:
        intent_lines = [
            "\n\n## 补充输入4：每段的视觉意图（稿件结构标记 → 建议画面情绪）",
            "这是对口播稿的结构分析。请你配画面时严格呼应这些意图：",
            "- 钩子/冲击段 → 画面要硬、有冲击力/悬念感，禁止蓝天白云/风景空镜",
            "- 争议/预埋段 → 画面要有张力/暗调/暗示，配合埋伏笔的悬念感",
            "- 回收/升华段 → 画面要收束/提升/光明",
            "- 呼吸点 → 画面要放松/留白，让观众缓口气",
            "- 金句/结论 → 画面要突出/定格，可配大字",
            "",
            "各段意图（按时间顺序）：",
        ]
        for item in visual_intent:
            start = item.get("start_sec", "?")
            intent = item.get("intent", "body")
            desc = item.get("desc", "")
            intent_lines.append(f"- [{start}s] {intent}: {desc}")
        dynamic_parts.append("\n".join(intent_lines))

    # 人物级视觉主题注入 (2026-08-11): persona.visual_theme 决定全局主体关键词方向
    # (老谭聊科技 → server room/datacenter; 老谭聊地缘 → world map/geopolitics)
    if visual_theme:
        dynamic_parts.append(
            "\n\n## 补充输入5：人物视觉主题（全局主体关键词方向）\n"
            f"当前账号的视觉主题是：**{visual_theme}**\n"
            "全局主体关键词（规则5.1）必须从该主题取场景词，禁止用地域/城市词替代。"
        )

    return prompt + "".join(dynamic_parts)


def _aggregate_segments(
    segment_timings: list[dict[str, Any]],
    min_sec: float = 12.0,
    max_sec: float = 25.0,
) -> list[dict[str, Any]]:
    """句预聚成候选段 (2026-08-26 分段先行): 贪心时长窗 12~25s。

    聚合规则: 累积句子至 ≥min_sec 即收段; 单句超 max_sec 独立成段; 段绝不跨
    max_sec(超长句后强制截断)。预览取段首 28 字 + 段尾 22 字(主题词/数字高发区)。
    """
    blocks: list[dict[str, Any]] = []
    cur: list[tuple[int, dict[str, Any]]] = []
    cur_start = 0.0

    def _flush() -> None:
        nonlocal cur, cur_start
        if not cur:
            return
        texts = [str(t.get("text", "")).strip() for _, t in cur]
        joined = "".join(texts)
        preview = (joined[:28] + ("…" + joined[-22:] if len(joined) > 52 else "")) or "（空）"
        blocks.append({
            "no": f"B{len(blocks) + 1:02d}",
            "start": float(cur[0][1].get("start", 0)),
            "end": float(cur[-1][1].get("end", 0)),
            "s_from": f"S{cur[0][0] + 1:03d}",
            "s_to": f"S{cur[-1][0] + 1:03d}",
            "preview": preview,
        })
        cur = []

    for idx, t in enumerate(segment_timings):
        dur = float(t.get("end", 0)) - float(t.get("start", 0))
        if not cur:
            cur_start = float(t.get("start", 0))
        cur.append((idx, t))
        cur_dur = float(t.get("end", 0)) - cur_start
        if cur_dur >= min_sec or dur >= max_sec:
            _flush()
    _flush()
    return blocks
