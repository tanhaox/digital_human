# -*- coding: utf-8 -*-
"""ruleset — 生图提示词军规机器单一源 (1017 W6, 用户令).

因果律 (用户定谳): K2 严格执行提示词, 提示词烂则怎么生都错 — 质量闸必须在
提示词产出层, 不在出图读图层。本模块是军规的机器可读形态 (人读真源:
docs/design/运镜军规-1017定稿.md), writer 轨与器层轨**同读此一份**:

  KEYFRAME_MAX        画面句长度帽 (Krea2 容量红线, 超长稀释主体权重)
  FACE_WORDS          脸向合规词表 (人物镜画面句必须命中其一)
  FACE_INJECT_CLAUSE  缺脸向时的自动注入条款
  enforce_keyframe()  统一执法: 句界截断 + 脸向注入 (确定性, 毫秒, 零 LLM)

挂点: assembler.assemble_episode (器层产出) / director2._build_doc 与
plan_shot (writer 产出) — 两轨落盘前同过一闸; scene_writer 契约与
creative_slots 填槽提示从此常量取词面 (规则只写一处)。"""
from __future__ import annotations

import re

# ── 画面句长度 (Krea2 容量红线: assembler 300 帽实证; plan_shot 曾无帽产 559 字) ──
KEYFRAME_MAX = 320

# ── 脸向律 (1017 无脸根治: 不写=krea2 默认背镜看道具, A1/A3/A4 整弧实锤) ──
FACE_WORDS = ("面向镜头", "朝向镜头", "回望镜头", "正脸", "侧脸", "面朝", "仰面", "转头看向镜头")
FACE_INJECT_CLAUSE = "人物面部朝向镜头方向，五官清晰可见"

_SENT_SPLIT = re.compile(r"[。；;]")


def has_face_word(kf: str) -> bool:
    return any(w in kf for w in FACE_WORDS)


def enforce_keyframe(kf: str, person: bool) -> tuple[str, list[str]]:
    """统一执法 (确定性): 长度句界截断 + 人物镜脸向注入。

    返回 (修正后文本, 已施加的修正名列表) — 调用方记日志/台账。"""
    fixes: list[str] = []
    text = str(kf or "").strip()
    if not text:
        return text, fixes
    # ① 长度: 先剥已注条款重排? 简化 — 句界截断保主干 (前段=景别/空间/光/主体)
    if len(text) > KEYFRAME_MAX:
        cut, rest = "", ""
        for seg in _SENT_SPLIT.split(text):
            cand = (cut + ("。" if cut else "") + seg).strip("。")
            if len(cand) + 1 > KEYFRAME_MAX:
                break
            cut = cand
        if cut:
            text = cut + "。"
            fixes.append(f"len>{KEYFRAME_MAX}截断")
    # ② 脸向: 人物镜缺词 → 注入条款 (追加句, 截断后仍有 ~30 字余量)
    if person and not has_face_word(text):
        if len(text) + len(FACE_INJECT_CLAUSE) + 1 > KEYFRAME_MAX + 40:
            text = text.rstrip("。") + "。" + FACE_INJECT_CLAUSE  # 脸向优先于长度余量
        else:
            text = text.rstrip("。") + "，" + FACE_INJECT_CLAUSE + "。"
        fixes.append("face-inject")
    return text, fixes
