"""Director prompt — 无出镜模式: C 线禁用时把 host 强制规则从系统提示词裁剪掉.

核心问题 (2026-08-07): visual_director_v2.txt 是"数字人优先"的导演系统,
约 40 处写死 host/老陈出镜 (规则1/2/2.5/3/4.3/4.4、自检清单、示例、注意
事项)。之前只靠 _build_pipeline_constraint_block 注入"管线限制"约束块——
那只是压在系统提示词上的一道"软封印", LLM 面对写死的 host 规则时封印极易
失效, 导致即便用户只开 P/H 线仍规划出 host slot。

本次修复在系统提示词这一层直接按 C 线禁用移除 host 内容, 让"无出镜"成为
不可违背的系统规则而非提示块。此函数按 visual_director_v2.txt 的实际章节
结构做整节替换 (比逐行正则稳健, 换行/中文引号不影响)。
"""
from __future__ import annotations

import re

from app.services.director_prompt._constants import _NO_HOST_DEMO

__all__ = ["strip_host_mode"]


# ── 规则表替换常量 (无出镜版) — 替换内容逐字保留 ──
_RULE1_TABLE = (
    "| 条件 | visual_type | 理由 |\n"
    "|---|---|---|\n"
    "| 包含\"大家好\"/\"我是老陈\"/\"下期见\"等开场结尾词 | `hf_title` | 标题卡建立画面识别 |\n"
    "| 包含\"评论区聊聊\"/\"您觉得呢\"等互动提问 | `hf_title` | 提问卡承接互动 |\n"
    "| 包含金句/结论/观点性陈述 | `hf_title` / `broll_pexels` | 大字标题+画面传递观点 |\n"
    "| 包含具体数字、百分比、金额、年份 | `broll_pexels` | 用真实画面做数据背景 |\n"
    "| 引用外部事件、历史案例、他国情况 | `broll_pexels` | 需要画面建立场景 |\n"
    "| 核心结论 + 关键数据同时出现 | `hf_chart` | 图表承载数据,信息密度最大 |\n"
    "| 一般叙述/过渡/补充说明 | `broll_pexels` | **默认首选**, 真实画面观感最佳 |"
)
_RULE2_TABLE = (
    "| 规则 | 要求 |\n"
    "|---|---|\n"
    "| **`hf_*` 不得相邻** | 任何两个 `hf_chart`/`hf_title` slot 不能紧挨着, 中间必须插入 `broll_*` |\n"
    "| **`broll_*` 为主力** | 全片以 `broll_pexels` 为主画面, `hf_*` 做节奏点缀 |\n"
    "| **无出镜模式(硬性)** | 本片为无出镜(无数字人)模式, **严禁规划任何出镜 slot** |\n"
    "| `broll_*` / `hf_*` 连续不超过 4 行 | 连续 4 行以上素材,画面单调 |\n"
    "| 每段开头第 1 个 slot 用 `broll_*` 或 `hf_*` | 建立段落感 |\n"
    "| 整段最后一个 slot 用 `hf_title` 或 `broll_*` | 收束感,给观众消化时间 |\n"
    "| 全场高潮点(最核心数据/最强金句)必须用 `hf_chart` | 信息密度拉到最大 |\n"
    "| 开场首 slot：`broll_pexels` 冲击画面（**禁 hf 字幕卡/空镜开场**, 2026-09-04）→ `hf_title` → `broll_*` | 建立连接→制造好奇→抛出悬念 |"
)
_RULE3_TABLE = (
    "| 信息层级 | 视觉类型 |\n"
    "|---|---|\n"
    "| 轻信息(观点/金句/互动) | `hf_title` / `broll_pexels` |\n"
    "| 中信息(单一数据/单一事实) | `broll_local` / `broll_pexels` / `hf_chart` |\n"
    "| 重信息(多数据对比/因果逻辑/核心结论) | `hf_chart` |"
)


def _remove_host_rows(prompt: str) -> str:
    """步骤 1-2: 删掉 host / mixed_host_broll 两行与 params 字段约定."""
    prompt = re.sub(r"^\| `host` \| `host` \|.*\|\n", "", prompt, flags=re.M)
    prompt = re.sub(r"^\| `mixed_host_broll` \| `mixed_host_broll` \|.*\|\n", "", prompt, flags=re.M)

    prompt = re.sub(r"\*\*host\*\*: `\{.*?\}`\n\n", "", prompt, flags=re.S)
    prompt = re.sub(r"\*\*mixed_host_broll\*\*:.*?\n\n", "", prompt, flags=re.S)
    return prompt


def _replace_rule_tables(prompt: str) -> str:
    """步骤 3-7: 规则1/2/2.5/3/4.3/4.4 整节替换 (整表换无出镜版)."""
    prompt = re.sub(
        r"## 规则1:画面类型判断\(visual_type\)\n\n\| 条件.*?(?=\n## 规则2：节奏控制)",
        "\n## 规则1:画面类型判断(visual_type)\n\n" + _RULE1_TABLE,
        prompt, flags=re.S,
    )
    prompt = re.sub(
        r"## 规则2：节奏控制（硬性约束）\n\n\| 规则.*?(?=\n## 规则2\.5)",
        "\n## 规则2：节奏控制（硬性约束）\n\n" + _RULE2_TABLE,
        prompt, flags=re.S,
    )
    prompt = re.sub(
        r"## 规则2\.5：机位分配.*?(?=\n## 规则3:信息密度匹配)",
        "\n## 规则2.5：机位分配（已废弃 — 本片无出镜）\n\n无出镜模式下不需要 host 机位。`broll_*` / `hf_*` 不输出 camera 字段。",
        prompt, flags=re.S,
    )
    prompt = re.sub(
        r"## 规则3:信息密度匹配\n\n\| 信息层级.*?(?=\n## 规则4:素材选择策略)",
        "\n## 规则3:信息密度匹配\n\n" + _RULE3_TABLE,
        prompt, flags=re.S,
    )
    prompt = prompt.replace(
        "### 4.3 清单中该 category 不存在 → 改用 `broll_pexels`(再降级为 host)\n按 broll_pexels → broll_local → host 的优先级降级。宁可让老陈出镜,也不配错画面。",
        "### 4.3 清单中该 category 不存在 → 改用 `broll_pexels`(再降级为 broll_local/hf_chart)\n按 broll_pexels → broll_local → hf_chart 的优先级降级。本片无出镜,宁可配标题卡,也不插入出镜。",
    )
    prompt = re.sub(
        r"### 4\.4 混合场景的素材选择.*?(?=\n## 规则5:全局画面一致性)",
        "### 4.4 重信息场景的素材选择\n核心结论/数据高潮用 `hf_chart` 承载, 配合 `broll_pexels` 数据背景画面\n→ 选择画面对比度高、信息密度大的素材\n→ 优先选航拍/大场景/图表,避免选择人物特写",
        prompt, flags=re.S,
    )
    return prompt


def _replace_demo_and_checks(prompt: str) -> str:
    """步骤 8-10: 输出示例整块替换 + 自检清单删除 + 注意事项替换."""
    prompt = re.sub(
        r"# 输出示例\n\n```json.*?```\n",
        "# 输出示例\n\n" + _NO_HOST_DEMO + "\n",
        prompt, flags=re.S,
    )

    for line in [
        "- [ ] `host` 连续 slot 是否超过 3 个?",
        "- [ ] 每 5 个 slot 至少 1 次 `host`?",
        "- [ ] 第一个 slot 是否是 `host`?",
        "- [ ] 最后一个 slot 是否是 `host`?",
        "- [ ] 核心数据/最强金句 slot 是否用了 `mixed_host_broll`?",
        "- [ ] 开场前 3 个 slot 是否是 `host → broll_* → host`?",
        "- [ ] **是否有两个 `host` slot 紧挨着？**（不允许）",
    ]:
        prompt = prompt.replace(line + "\n", "")

    prompt = prompt.replace(
        "2. **素材不足用 host 补**:宁可多让老陈说话,也不配错画面。",
        "2. **素材不足用 hf_title 补**:宁可让标题卡承接,也不插入出镜。",
    )
    prompt = prompt.replace(
        "3. **mixed_host_broll 要克制**:全篇不超过 3 处,只在最核心的位置使用。",
        "3. **画面要克制**:全片不留纯黑屏,用 hf_title/broll 交替填充。",
    )
    prompt = prompt.replace(
        "4. **保持节奏感**:`host` 和 `broll_*` 交替进行,就像对话中的\"你说我听,我看你说\"。",
        "4. **保持节奏感**:`broll_*` 和 `hf_*` 交替进行,画面随口播推进。",
    )
    return prompt


def _final_cleanup(prompt: str) -> str:
    """步骤 11-12: 核心能力描述替换 + 兜底删除残留 host 行."""
    prompt = prompt.replace(
        "懂得出镜、素材、混合三种画面类型的交替频率,让观众既不疲劳也不走神。",
        "懂得画面素材与动态图表卡片的交替频率,让观众既不疲劳也不走神。",
    )

    # 兜底: 删除任何残留含 host 的行 (broll_* / hf_* 均不含该词)
    prompt = re.sub(r"^.*\b(?:host|mixed_host_broll)\b.*\n", "", prompt, flags=re.M)
    prompt = re.sub(r"\n{3,}", "\n\n", prompt)
    prompt = prompt.replace("老陈出镜", "画面呈现")
    return prompt.strip()


def strip_host_mode(prompt: str) -> str:
    """按章节结构移除数字人 host 强制规则, 返回无出镜版系统提示词."""
    prompt = _remove_host_rows(prompt)
    prompt = _replace_rule_tables(prompt)
    prompt = _replace_demo_and_checks(prompt)
    prompt = _final_cleanup(prompt)
    return prompt
