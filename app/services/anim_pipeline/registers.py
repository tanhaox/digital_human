# -*- coding: utf-8 -*-
"""规层: 句型打标 + 主体寄存器钉标 (拆书导演模型 1009 定稿, 零 LLM).

讲座图文导演 (MTV 手法·用户令): 文稿句型已写好每一镜的主体 —
品牌句/引用句/案例句/互动句/预告句确定性识别, 其余默认概念句 → 隐喻镜。
德鲁克被结构性钉死在引用句配额内 (作者=歌手客串, 电影画面优先),
不靠 LLM 配额自律, 围栏全在本层与校验层 (确定性)。

寄存器表见 docs/design/拆书导演模型-句型寄存器-定稿.md §三。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 寄存器枚举 (法/术/器/校验四方共用 vocab)
METAPHOR = "metaphor"        # 概念隐喻镜 (默认) — 概念世界实体在发生
PAIN = "pain_point"          # 痛点镜 — 钩子区 "你" 在困境 (观众替身, 泛化人物)
CASE = "case_narrative"      # 案例镜 — 案例人物 mini-drama (影视语法在此适用)
CITE = "author_cite"         # 作者镜 — 德鲁克+书页 (作者唯一合法寄存器)
BRAND = "host_brand"         # 品牌镜 — 老谭 (品牌卡链, 现状不动)
CONTRAST = "contrast"        # 对比镜 — 双立场同屏 (互动句)
RECAP = "recap"              # 展馆镜 — 工具陈列推进/预告 (总结/预告句)

# 句型规则 (关键词命中即判; 顺序 = 优先级, 先案例后引用: "德鲁克举例子" 是案例非引用)
_CASE_PAT = re.compile(
    r"举个例子|举了.{0,6}例子|举例子|一个.{0,8}的例子|某公司|某银行|一位高管|当年的我|曾经有")
_CITE_PAT = re.compile(
    r"书里原话|原话|在书里强调|书里强调|书中强调|书里指出|书中写道|书里写道|"
    r"德鲁克在书里|德鲁克认为|德鲁克说|德鲁克强调|作者认为")
_INTERACT_PAT = re.compile(r"评论区|你站哪边|留言|说说你的|投一票|弹幕|扣个")
_RECAP_PAT = re.compile(
    r"下一集|下期|下一件|预告|完整行动册|行动册|今晚就动手|分三步|"
    r"第[0-9一二三四五六七八九]+集等你|总结一下")


def tag_text(text: str) -> str | None:
    """单段文本句型判定 — 返回 tag 或 None (None=概念句, 默认隐喻镜).

    品牌句复用 shots.is_brand_text 口径 (与品牌卡链同一套 match_rules,
    口径分裂 = 品牌镜双判打架)。
    """
    if not text or not str(text).strip():
        return None
    from .shots import is_brand_text, norm_for_match
    if is_brand_text(norm_for_match(str(text))):
        return "brand"
    if _CASE_PAT.search(text):
        return "case"
    if _CITE_PAT.search(text):
        return "cite"
    if _INTERACT_PAT.search(text):
        return "interact"
    if _RECAP_PAT.search(text):
        return "recap"
    return None


_TAG2REG = {
    "brand": BRAND,
    "cite": CITE,
    "case": CASE,
    "interact": CONTRAST,
    "recap": RECAP,
}


# 1009 P1 三级降级链 (设计 §3.3): 术层填空失败镜的器层默认槽 — 寄存器语义的
# 一句话动作 (画面仍合法可生成, 打标 slots_source=default 供人检/审计)。
# plan_scene 已不产 keyframe (STRUCT 宪法), 无默认槽 = 无首帧。
_DEFAULT_ACTIONS = {
    METAPHOR: "概念过程正推进到下一刻",
    CONTRAST: "两侧对比的动态正在展开",
    RECAP: "工具站点依次点亮推进",
    PAIN: "人物在困境中停下抬头",
    CASE: "案例人物正做手头的事",
    CITE: "作者合上书页抬起眼",
    BRAND: "讲者递出工具",
}


def default_slots(register: str) -> dict[str, str]:
    """器层默认槽 (降级链第三级) — action 一句 + 空 detail/atmosphere."""
    return {"action": _DEFAULT_ACTIONS.get(register or "", "主体动作推进一刻"),
            "scene_detail": "", "atmosphere": ""}


def world_map(doc: dict[str, Any]) -> dict[str, str]:
    """shots.json world_library → {世界名: 视觉短语} (器层/校验共用).

    条目形如 "时间筛网: 多层金属筛网=删减过滤机制, 粗筛留大石/细筛漏沙粒=砍无效留核心"。
    视觉短语 = 各映射段 "=" 左侧 (可画的实体与过程), 丢右侧的概念名 —
    K2 要画的是筛网和沙, 不是"删减过滤机制"。
    """
    out: dict[str, str] = {}
    for item in doc.get("world_library") or []:
        s = str(item)
        name, _, body = s.partition(":")
        name = name.strip()
        if not name:
            continue
        segs = [seg.split("=", 1)[0].strip() for seg in body.split(",")]
        visual = "，".join(seg for seg in segs if seg)
        out[name] = visual or name
    return out


def world_visual(doc: dict[str, Any], world_name: str) -> str:
    """世界名 → 视觉短语; 未注册时退世界名本身 (宽进, 校验层另把对齐关)。"""
    wm = world_map(doc)
    if world_name in wm:
        return wm[world_name]
    for k, v in wm.items():  # 前缀/包含宽松匹配 (立意层世界名偶有±一字漂移)
        if world_name in k or k in world_name:
            return v
    return world_name


def assign_registers(doc: dict[str, Any], bible: dict[str, Any] | None = None,
                     only: set[str] | None = None) -> int:
    """逐镜钉主体寄存器 (shot.register + shot.reg_ref) — 规层零 LLM.

    规则: 品牌→host_brand / 引用→author_cite / 案例→case_narrative /
    互动→contrast / 总结预告→recap / 钩子区概念句→pain_point / 其余→metaphor。
    reg_ref: metaphor=本弧 visual_world; cite=句中点名(缺省=引用句实证推断的作者);
    case=句中圣经角色名(排除作者, 无则泛化人物); pain=观众替身。
    幂等: 已有 register 的镜跳过 (only 可强制重钉)。返回钉标数。
    """
    bible_names = [str(c.get("name") or "") for c in (bible or {}).get("characters") or []]
    bible_names = [n for n in bible_names if n]
    wm_keys = list(world_map(doc).keys())
    # 作者判定: 引用句点名实证 (他书角色卡顺序不保证作者首位, characters[0] 裸信
    # = 案例句仍会吸走作者名)。引用句型命中的句子里出现频次最高的圣经角色 = 作者;
    # 无引用句实证才退 characters[0]。
    from collections import Counter as _Cnt
    _cite_names: _Cnt[str] = _Cnt()
    for s in doc.get("shots") or []:
        tx = str(s.get("narration") or "")
        if _CITE_PAT.search(tx):
            for nm in bible_names:
                if nm in tx:
                    _cite_names[nm] += 1
    author = _cite_names.most_common(1)[0][0] if _cite_names else \
        (bible_names[0] if bible_names else "")
    n = 0
    for shot in doc.get("shots") or []:
        sid = str(shot.get("shot_id") or "")
        if only is not None and sid not in only:
            continue
        if shot.get("register") and only is None:
            continue
        narration = str(shot.get("narration") or "")
        tag = tag_text(narration)
        reg = _TAG2REG.get(tag or "")
        if reg is None:
            # 默认反转: 钩子区概念句 = 痛点镜 ("你"在困境), 其余 = 隐喻镜
            reg = PAIN if str(shot.get("zone") or "").startswith("hook") else METAPHOR

        reg_ref = ""
        if reg in (METAPHOR, CONTRAST, RECAP):  # 概念域: reg_ref = 本弧概念世界
            arc = next((a for a in doc.get("arcs") or []
                        if str(a.get("arc_id")) == str(shot.get("arc_id"))), {})
            reg_ref = str(arc.get("visual_world") or "").strip()
        elif reg == CITE:
            # 引用镜主体=作者 (句中点名优先, 缺省首角色)
            reg_ref = next((nm for nm in bible_names if nm in narration), "")
            if not reg_ref:
                reg_ref = author or "作者"
        elif reg == CASE:
            # 案例镜主体=案例人物 — 作者只是转述人 ("德鲁克举例子，一位高管…"),
            # 吸走作者名 = 围栏失守 (s53 实锤): 排除作者后取句中圣经角色, 无则泛化
            cand = [nm for nm in bible_names if nm in narration and nm != author]
            reg_ref = cand[0] if cand else "泛化人物"
        elif reg == PAIN:
            reg_ref = "观众替身"

        shot["register"] = reg
        shot["reg_ref"] = reg_ref
        n += 1

    if n:
        # 概念世界注册表对账: 本弧 visual_world 未入 world_library → 校验层会 WARN,
        # 这里日志可见 (立意层世界名漂移时第一现场)
        for arc in doc.get("arcs") or []:
            vw = str(arc.get("visual_world") or "").strip()
            if vw and wm_keys and not any(vw in k or k in vw for k in wm_keys):
                logger.warning("[registers] %s visual_world「%s」不在 world_library — "
                               "隐喻镜将退世界名入画", arc.get("arc_id"), vw)
        logger.info("[registers] %d 镜寄存器钉标 ✓ (零 LLM)", n)
    return n



def author_of(doc: dict[str, Any], bible: dict[str, Any] | None = None) -> str:
    """作者判定 (1013 P0 抽公共): 引用句点名实证 (句中频次最高的圣经角色),
    无实证退 characters[0]。围栏 (作者 look 只准引用镜) / r2v 路由 / writer_audit 共用。"""
    bible = bible or {}
    bible_names = [str(c.get("name") or "") for c in bible.get("characters") or []]
    bible_names = [n for n in bible_names if n]
    from collections import Counter as _C
    cites: _C[str] = _C()
    for s in doc.get("shots") or []:
        tx = str(s.get("narration") or "")
        if _CITE_PAT.search(tx):
            for nm in bible_names:
                if nm in tx:
                    cites[nm] += 1
    if cites:
        return cites.most_common(1)[0][0]
    return bible_names[0] if bible_names else ""

def register_mix(doc: dict[str, Any], arc_id: str) -> str:
    """一场的句型分布摘要 (法层 DP 输入用): "概念×5/案例×2/引用×1"。

    DP 拿它判定本场主寄存器 → location 选域 (概念场选概念世界, 案例场选实景)。
    """
    from collections import Counter
    cnt: Counter[str] = Counter()
    for s in doc.get("shots") or []:
        if str(s.get("arc_id")) != str(arc_id):
            continue
        reg = str(s.get("register") or "")
        if not reg:
            tag = tag_text(str(s.get("narration") or ""))
            reg = _TAG2REG.get(tag or "") or (
                PAIN if str(s.get("zone") or "").startswith("hook") else METAPHOR)
        cnt[reg] += 1
    zh = {METAPHOR: "概念", PAIN: "痛点", CASE: "案例", CITE: "引用",
          BRAND: "品牌", CONTRAST: "互动", RECAP: "总结"}
    return "/".join(f"{zh.get(k, k)}×{v}" for k, v in cnt.most_common()) or "概念×0"


def arc_register_mix(arc: dict[str, Any]) -> str:
    """立意层弧 (尚无 doc 镜) 的句型分布 — 打标 carrier_lines + 钩子窗判定.

    法层生成时用 (arcs 骨架阶段); 权威钉标仍是 assign_registers (分镜后逐镜)。
    """
    from collections import Counter
    cnt: Counter[str] = Counter()
    for line in arc.get("carrier_lines") or []:
        tag = tag_text(str(line))
        cnt[_TAG2REG.get(tag or "", METAPHOR)] += 1
    try:  # 钩子窗内 (前60s) 的概念句按痛点镜计 — DP 需知道开场是"你"的困境
        if float(arc.get("t_start") or 0) < 60:
            for k in (METAPHOR,):
                if k in cnt:
                    cnt[PAIN] = cnt[PAIN] + cnt[k]
                    del cnt[k]
    except (TypeError, ValueError):
        pass
    zh = {METAPHOR: "概念", PAIN: "痛点", CASE: "案例", CITE: "引用",
          BRAND: "品牌", CONTRAST: "互动", RECAP: "总结"}
    return "/".join(f"{zh.get(k, k)}×{v}" for k, v in cnt.most_common()) or "概念×0"
