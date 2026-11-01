# -*- coding: utf-8 -*-
"""军规单一真源 (1101 U1 · AUDIT-20261101 A1 根治) — 运镜/文字路由/禁词的唯一代码源。

四套 LLM 人格 (director2.DIRECTOR_SYS / DIRECTOR_SYS_STRUCT / scene_writer.WRITER_SYS /
_enhance_pass1-2) 与确定性执法 (h3 词表 / director2.enforce_camera_span) 全部从本模块
取词表与条款文本。人格字面量里放 ``@@CAMERA_LAW@@`` 占位符, 常量定义后 .replace()
注入 (同 _substitution_block 拼接惯例)。改军规 = 改本模块 + 军规文档
(docs/design/运镜军规-1017定稿.md, 改军规先改文档)。

单源纪律: MOVE_WORDS 被 h3._CAM_MOVE_KINDS (需英文 kind 映射) 与
director2.enforce_camera_span (剥词闸) 消费 — 词表只在这里改;
tests/test_law_propagation.py 断言三处一致, 防再断链 (1101 教训)。
"""
from __future__ import annotations

# ── 词表 (运镜六词: 提示词枚举 / h3 渲染触发 / 剥词闸 同源) ──────────
MOVE_WORDS: tuple[str, ...] = ("左摇", "右摇", "上摇", "下摇", "拉近", "拉远")


def cam_move_min_span() -> float:
    """运镜槽长下限 (宪法 duration 表, 0.05 容差同 H3_MAX_SPAN 口径)。

    幅度天花板=时长 (1024 ep6 三连实锤: <6s 槽运镜幅度出不来)。"""
    from .constitution import duration_table
    return round(float(duration_table().get("cam_move_min_span", 6.0)) - 0.05, 2)


# ── 条款文本 (人格注入用; 独立成段的完整规则, 各人格自带引语) ─────────
CAMERA_LAW_BULLET = (
    "camera 点名运镜仅限场景揭示/规模揭示时刻且本镜槽 ≥6s (幅度天花板=时长, <6s 出不来, "
    "1024 实测), 可点名六词之一: " + "/".join(MOVE_WORDS) + " — 单镜单运镜、大幅配慢速、"
    "揭示物(锚)写进动作层末拍; 本场含 ≥6s 槽时至少 1 镜点名, 每场至多 2 镜 (1101 双向配额); "
    "其余一律 固定机位/极轻微推拉; 横移/跟拍/平移追踪仍全禁 (1024 B-CAM1 解锁)。"
    "系统落盘闸会确定性剥除 <6s 槽的运镜词。"
)

# 输出契约 camera 枚举 (DIRECTOR_SYS / STRUCT 的 JSON 规格同串)
CAMERA_ENUM = "空拍|平视|低角度|俯瞰|特写|缓推|固定|" + "|".join(MOVE_WORDS)

# 输出契约用的紧凑单行版 (writer_blocks 等运行时拼接)
CAMERA_LAW_CONTRACT = (
    "camera 默认 固定/机位词 (平视/低角度/俯瞰/特写/空拍/缓推); 仅 ≥6s 槽的场景揭示/"
    "规模揭示镜可点名 " + "/".join(MOVE_WORDS) + " 之一 (每场 1-2 镜, 单镜单运镜, "
    "大幅配慢速, 揭示物写进 motion 末拍)"
)

# 文字三路由 (1101 U6 事件字统一出口定稿口径; WRITER_SYS 契约与军规 §6 同文)
TEXT_ROUTE_CHANNEL = (
    "人物镜 (r2v 工位) 由视频内渲艺术字, 其余镜由装配层渲金色大字 (字体保真零乱码) "
    "— 1021 H3 镜内渲字下线后的统一出口"
)
TEXT_ROUTE_LAW = (
    "文字三路由 (每个文字元素三选一, 绝不混用): ①H3 事件字→申报 h3_text_event 字段 "
    f"(≤4字 + at_s + zone, 画面句在落点留白) — {TEXT_ROUTE_CHANNEL}; "
    "②K2 图内字→画面句写「空白载体」+ 申报 in_image_text (系统二段合成, 禁「写着XX」); "
    "③花字/长句→text_layer 后期叠加。motion 散文永不描述文字。"
)

# ── motion 禁词 (军规 §2 横移族; _enhance_pass2 / 确定性闸 共用) ──────
MOTION_BAN_ZH: tuple[str, ...] = ("横移", "跟拍", "摇镜", "平移")
MOTION_BAN_EN_RE = r"(?i)\b(pan|panning|tracking|truck)\b"
