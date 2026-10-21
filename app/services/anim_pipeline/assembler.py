# -*- coding: utf-8 -*-
"""器层: 确定性组装 — 零 LLM, 零 token (1007 v3 §七, 1008 P6 落地).

道法术器最后一环: LLM 只在术层填过空 (action/scene_detail/atmosphere),
本模块把 法层结构参数 + 术层创意槽 + 道层查表 确定性拼成 K2 画面句与
H3 anim 骨架 — 同样的输入永远产出同样的 prompt (可回放/可审计)。

输入:
  法 = shooting_script.json 的 scene.shots[i] (size/angle/lighting/blocking/props)
  术 = shot.creative_slots (action/scene_detail/atmosphere)
  道 = 圣经 JSON (characters.look / props.look / character_policy)
输出:
  image_prompt_zh / keyframe_zh  (K2 画面句, 下游 k2.py 零改动直接吃)
  keyframe_en / anim.opening_desc / anim.beats (H3 骨架, 下游 h3.py 零改动)

CLI: python -m app.services.anim_pipeline.assembler --book <书名> --ep 2 [--dry]
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from typing import Any

from . import shots as shots_mod

logger = logging.getLogger(__name__)

# ── 结构参数 → 短语映射 (法层枚举 → 中文镜头语言; 器层字典, 改表即全链生效) ──

SIZE_MAP = {
    "ELS": "辽阔大全景", "LS": "全景", "MLS": "中全景",
    "MS": "中景", "MCU": "近景", "CU": "特写", "ECU": "大特写",
}
ANGLE_MAP = {
    "birds_eye": "俯瞰视角", "high_angle": "高角度俯视", "slight_high": "微俯视",
    "eye_level": "平视", "slight_low": "微仰视", "low_angle": "仰视", "dutch": "斜角构图",
    "over_shoulder": "过肩视角",
}
POSITION_MAP = {
    "center": "位于画面中央", "center_lower_third": "位于画面中央偏下",
    "left_third": "位于画面左三分之一", "right_third": "位于画面右三分之一",
    "left": "位于画面左侧", "right": "位于画面右侧",
}
SIZE_EN = {
    "ELS": "extreme wide establishing shot", "LS": "wide shot", "MLS": "medium-long shot",
    "MS": "medium shot", "MCU": "medium close-up", "CU": "close-up", "ECU": "extreme close-up",
}
ANGLE_EN = {
    "birds_eye": "bird's-eye view", "high_angle": "high angle", "slight_high": "slightly high angle",
    "eye_level": "eye level", "slight_low": "slightly low angle", "low_angle": "low angle",
    "over_shoulder": "over-the-shoulder",
    "dutch": "dutch angle",
}

_PHYSICAL_LOCK = ("the camera viewpoint, the main structural elements and layout of "
                  "Picture 1")

_MAX_PROMPT = 300  # Krea2 容量红线 (200-280 字 + 余量, 超长先裁道具再裁细节)

_SIZE_ORDER = ["ELS", "LS", "MLS", "MS", "MCU", "CU", "ECU"]
_MICRO_DIR = ("左", "右", "后", "侧")  # 插值镜微位加方位 (确定性变奏, 与法层去重同池)

# 1019 锚变奏 (A6 13 镜同锚实锤): 概念域 fallback 的共享世界锚 + 场级光效
# 让 K2 出图几乎一致 — 景别词权重被长锚稀释, ·左/·右尾缀是字符级差异。
# 两轴确定性变奏 (零 LLM, 同输入同输出, 不破坏场连续性 — 锚照旧全量保留):
# ① 机位/构图语轮转 (镜头语言, 不与 angle 冲突); ② 锚分句聚焦轮换 (本镜聚焦)。
_CAM_VARIANTS = (
    "前景虚化物框住主体",
    "浅景深主体清晰背景融为光斑",
    "长焦压缩空间远近贴近",
    "广角贴近边缘纵深拉伸",
    "越肩机位视线引向画面深处",
    "贴地低机位地面纹理作前景",
    "侧后方四十五度机位",
    "主体落在黄金分割点",
    "对角线构图路径贯穿画面",
    "框架式构图门洞枝干圈住主体",
    "镜头缓缓推近",
    "镜头横移跟随",
)


def _anchor_focus(anchor: str, seq: int) -> str:
    """锚分句聚焦 (确定性轮换): 取锚第 (seq-1)%n 分句作本镜焦点强调."""
    import re as _re
    clauses = [c.strip(" 、") for c in _re.split(r"[，,、]", str(anchor or "")) if c.strip()]
    if len(clauses) < 2:
        return ""
    return clauses[(seq - 1) % len(clauses)]


def _anchor_head(anchor: str) -> str:
    """锚首句 (场景定位句) — 特写族尾注用 (1019 B: 特写不带全景环境)."""
    import re as _re
    cs = [c.strip(" 、") for c in _re.split(r"[，,、]", str(anchor or "")) if c.strip()]
    return cs[0] if cs else ""


def _scene_seq_map(doc: dict[str, Any]) -> dict[str, int]:
    """场内时间序 (t_start 排名) — 锚变奏轮转基准; 首镜(0)立锚不动."""
    out: dict[str, int] = {}
    by_arc: dict[str, list] = {}
    for s in doc.get("shots") or []:
        by_arc.setdefault(str(s.get("arc_id") or ""), []).append(s)
    for arc, ss in by_arc.items():
        for i, s in enumerate(sorted(ss, key=lambda x: float(x.get("t_start") or 0))):
            out[str(s.get("shot_id"))] = i
    return out


def _expand_fa_shots(fa_shots: list[dict], n_doc: int) -> list[dict]:
    """法层 F 镜 → doc N 镜的确定性展开 (1008 电影感修).

    比例映射 j*F//N 在 N>F 时必产生连续同槽 (N=12,F=5 → 前3镜连吃 ELS)。
    尺寸游走插值: 沿阶梯从 fa[k] 走到 fa[k+1], 步长=距离/剩余步, 每步必变;
    已到目标还有步 → 前后振荡 (±1), 最后一步必落 anchor (收束保住)。
    重复槽的微位加方位后缀。"""
    F = len(fa_shots)
    if F == 0:
        return []
    if n_doc <= F:
        return [dict(s) for s in fa_shots[:n_doc]]

    def _idx(sz: str) -> int:
        return _SIZE_ORDER.index(sz) if sz in _SIZE_ORDER else 3  # MS 缺省

    anchors = [round(k * (n_doc - 1) / (F - 1)) for k in range(F)] if F > 1 \
        else [n_doc - 1]
    _HI = len(_SIZE_ORDER) - 1
    sizes: list[int] = [_idx(str(fa_shots[0].get("size") or "MS"))]
    for k in range(1, F):
        target = _idx(str(fa_shots[k].get("size") or "MS"))
        m = (anchors[k] if k < F - 1 else n_doc - 1) + 1 - len(sizes)
        for t in range(max(1, m)):
            rem = max(1, m - t)
            dist = target - sizes[-1]
            if rem == 1:
                nxt = target                      # 最后一步: 落 anchor
            elif dist == 0:
                nxt = sizes[-1] + (1 if t % 2 == 0 else -1)  # 已到: 振荡
            elif rem == 2 and abs(dist) == 1:
                # 借位: 先过 target 再回落; 阶梯边界处往远侧借 (跳两格再回)
                sign = 1 if dist > 0 else -1
                nxt = target + sign
                if not (0 <= nxt <= _HI):
                    nxt = target - 2 * sign
            elif abs(dist) >= rem:
                step = -(-abs(dist) // rem)       # ceil: 远距离大步
                nxt = sizes[-1] + step * (1 if dist > 0 else -1)
            else:
                nxt = sizes[-1] + (1 if dist > 0 else -1)  # 慢走, 留余步振荡
            # 阶梯边界: 钳位若造成"原地不动" → 翻方向
            clamped = max(0, min(_HI, nxt))
            if clamped == sizes[-1]:
                clamped = max(0, min(_HI, sizes[-1] + (1 if nxt <= sizes[-1] else -1)))
            sizes.append(clamped)
    # 长度守卫 (锚点取整可能致微差): 不足→绕末锚振荡, 超出→截到末锚
    while len(sizes) < n_doc:
        step = 1 if (n_doc - len(sizes)) % 2 == 1 else -1
        cand = max(0, min(_HI, sizes[-1] + step))
        if cand == sizes[-1]:
            cand = max(0, min(_HI, sizes[-1] - step))
        sizes.append(cand)
    sizes = sizes[:n_doc]
    sizes[-1] = _idx(str(fa_shots[F - 1].get("size") or "MS"))
    if n_doc > 1 and sizes[-2] == sizes[-1]:  # 截断可能造出末两镜同尺寸
        for d in (1, -1, 2, -2):
            if 0 <= sizes[-1] + d <= _HI:
                sizes[-2] = sizes[-1] + d
                break

    out: list[dict] = []
    for j in range(n_doc):
        shot = dict(fa_shots[min(F - 1, j * F // n_doc)])
        shot["size"] = _SIZE_ORDER[sizes[j]]
        if j not in anchors:  # 插值镜: 微位加方位 (锚点镜保持法层原值; 已带方位不叠)
            blk = dict(shot.get("blocking") or {})
            mp = str(blk.get("micro_position") or "").strip()
            if mp and "·" not in mp:
                blk["micro_position"] = f"{mp}·{_MICRO_DIR[(j - 1) % len(_MICRO_DIR)]}"
            shot["blocking"] = blk
        out.append(shot)
    return out


def _temp_class(temp: str) -> str:
    """色温 → 冷/中性/暖 (5800K → 冷调)."""
    try:
        k = float("".join(c for c in str(temp) if c.isdigit()) or 4500)
    except ValueError:
        k = 4500
    return "冷调" if k >= 5000 else "暖调" if k < 4000 else "中性调"


def _light_phrase(light: dict[str, Any]) -> str:
    """法层光影 dict → 中文光影短语 (零翻译, 直接拼).

    1009 用户令 (拆书导演模型): 只出**光效语言** (方向/色温/光比),
    灯具实体 (key_source: LED台灯/面板灯) 一律不入画 — 灯具×12 镜实锤,
    想要灯只能经术层 scene_detail。"""
    direction = str(light.get("key_direction") or "").strip()
    temp = str(light.get("temp") or "").strip()
    parts = [p for p in (direction,) if p]
    if temp:
        parts.append(f"色温{temp}({_temp_class(temp)})")
    if light.get("fill_ratio"):
        parts.append(f"补光比{light['fill_ratio']}")
    return "，".join(parts)


def _light_en(light: dict[str, Any]) -> str:
    # 1009: 灯具实体同禁 (同 _light_phrase); 方向词保留原文 (H3 容忍双语混排)
    direction = str(light.get("key_direction") or "").strip()
    temp = str(light.get("temp") or "").strip()
    en = direction or "soft natural light"
    if temp:
        en += f", {temp} {_temp_class(temp)} color"
    return en


def _clean_cut(s: str, limit: int) -> str:
    """按标点边界截断 (硬切会留 '三个物理拨' 这类断词)."""
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for sep in ("，", "。", "、", "；", ", ", " "):
        i = cut.rfind(sep)
        if i > limit // 2:
            return cut[:i]
    return cut


def _char_look(name: str, bible: dict[str, Any]) -> str:
    """道层角色查表: blocking.character → 圣经 look (精确名 → 子串; 无匹配=空).

    空匹配不回落 character_policy — 那是策略文本不是造型 (硬塞会截断成病句);
    法层生成时已注入圣经角色名单, 无匹配说明法层用了自造名, 渲染层只认名。"""
    for c in bible.get("characters") or []:
        if str(c.get("name") or "") == name:
            return str(c.get("look") or "")
    for c in bible.get("characters") or []:
        n = str(c.get("name") or "")
        if n and (n in name or name in n):
            return str(c.get("look") or "")
    return ""


def _prop_phrase(props_visible: list[str], bible: dict[str, Any]) -> str:
    """法层道具表 → 道具短语: props_visible 条目 "Time_Ledger(空)" → 道层 look 注入.

    ref 精确匹配圣经 props[].ref; look 按标点边界截 ~40 字 (容量红线);
    无匹配时保留原文。"""
    bibles = {str(p.get("ref") or ""): str(p.get("look") or "") for p in (bible.get("props") or [])}
    out: list[str] = []
    for item in (props_visible or [])[:3]:  # 最多 3 道具 (单焦点容量法)
        s = str(item).strip()
        if not s:
            continue
        ref = s.split("(", 1)[0].strip()
        look = bibles.get(ref, "")
        out.append(f"{s}（{_clean_cut(look, 40)}）" if look else s)
    return "，".join(out)


def _char_for_register(shot: dict[str, Any], blocking: dict[str, Any],
                       bible: dict[str, Any] | None = None) -> str:
    """实景域人物来源按寄存器重定 (1009 MTV 手法):

    pain=观众替身 / case=reg_ref(有卡带卡, 泛化人物=空) /
    cite=reg_ref(作者) / 无寄存器旧镜=blocking.character (原链宽进)。
    旧法层 blocking.character 38/40 镜写德鲁克 — 若照读, 寄存器围栏形同虚设。
    1010 ID-079 主体: 替身不再裸奔 — 角色台'观众替身'卡优先 (带 look 入画),
    无卡退圣经 era_anchor 时代锚 (服饰单品段) — 治 ep2 A1/A2 古代服饰。"""
    register = str(shot.get("register") or "").strip()
    ref = str(shot.get("reg_ref") or "").strip()
    if register == "pain_point":
        nm, _lk = _standin(bible or {})
        return nm
    if register == "case_narrative":
        return "" if ref in ("", "泛化人物") else ref
    if register == "author_cite":
        return ref or str(blocking.get("character") or "").strip()
    return str(blocking.get("character") or "").strip()


def _standin(bible: dict[str, Any]) -> tuple[str, str]:
    """观众替身 (pain_point 主体) → (名, look): 角色台替身卡优先, 无卡退圣经
    era_anchor (取冒号后的单品段)。两者皆无 → ("", "") 保持原裸奔行为 (宽进)。"""
    for c in bible.get("characters") or []:
        nm = str(c.get("name") or "")
        if nm and ("替身" in nm or "泛化职场" in nm or "观众" in nm):
            return nm, str(c.get("look") or "")
    era = str(bible.get("era_anchor") or "").strip()
    if era:
        # 1017 W2: era_anchor 解析归一 (h3.parse_era_anchor 单口; 惰性导入避环)
        from .h3 import parse_era_anchor
        seg = parse_era_anchor(era, segment_after_colon=True)
        return "职场人物", _clean_cut(seg, 40)
    return "", ""


def _quota_person_inject(doc: dict[str, Any], ss: dict[str, Any], bible: dict[str, Any],
                         by_sid: dict[str, tuple], arc_world: dict[str, str],
                         only: set[str] | None = None) -> int:
    """1013 配额刚性 (用户拍板 选项2): 装配后复量人物在场, 欠额场在概念镜
    (metaphor/recap/contrast) 重掷带 person_inject=True 的画面句 — 注入
    "Q版替身(圣经 look)与隐喻物件互动"子句, 补到每场 ≥⌈n/4⌉ 为止。

    与 final_gate.zone_quota_issues 同一把尺 (词表+度量去假阳), 注入词面含
    "人物"保终审命中。候选按 metaphor→recap→contrast 优先, 场内按时间均匀
    铺开 (人不在场头扎堆); only=单镜集时仅在该集内选候选 (计数仍全场口径)。

    1014 一把尺: 注入帽=分区配额 (宪法 scene_min_present) — 旧版帽死在
    ⌈n/4⌉, 与终产分区闸 (hook/mid 0.6) 两把尺错位, 确定性补人永远到不了
    验收线 (ep4 A6 16镜补到4即停 vs 需≥10, ×2 重试同死实锤)。"""
    from . import constitution as CST
    from .config import load as _load
    from .director2 import _PERSON_W, _has_person_words  # 延迟导入防环
    words = list(_PERSON_W)
    words += [str(c.get("name") or "").strip() for c in (bible.get("characters") or [])
              if str(c.get("name") or "").strip()]

    def _cnt(s) -> bool:
        return _has_person_words(str(s.get("keyframe_zh") or ""), words)

    by_arc: dict[str, list] = {}
    for s in doc.get("shots") or []:
        by_arc.setdefault(str(s.get("arc_id") or ""), []).append(s)
    if not (_standin(bible)[1] or "").strip():
        return 0  # 无替身 look 可注入 (宽进, 终审软警告照报)
    try:
        _tc = float(_load().brand_card.opening_sec)
    except Exception:  # noqa: BLE001
        _tc = 0.0
    n = 0
    _seq = _scene_seq_map(doc)  # 1019: 锚变奏场内序 (注入镜重掷同款变奏口径)
    for a, arc_shots in sorted(by_arc.items()):
        # 计数口径与终产闸同尺: 品牌卡 + 占位镜豁免 (不进分子也不进分母)
        eff = [s for s in arc_shots
               if not s.get("brand_card") and not s.get("_kf_missing")]
        if not eff:
            continue
        buckets: dict[str, list] = {}
        for s in eff:
            buckets.setdefault(CST.zone_of(s, doc, _tc), []).append(s)
        arc_picks: list = []
        for z, ss in sorted(buckets.items()):
            frac = CST.zone_value("person_quota", "scene_min_present", z) or 0.25
            need = len(ss) if frac >= 1 else max(1, math.ceil(len(ss) * frac))
            have = sum(1 for s in ss if _cnt(s))
            short = need - have
            if short <= 0:
                continue
            prio = {"metaphor": 0, "recap": 1, "contrast": 2}
            cands = sorted(
                (s for s in ss
                 if prio.get(str(s.get("register") or ""), 9) < 9 and not _cnt(s)
                 and str(s.get("writer_source") or "") != "writer"  # 1013 P0: writer 镜不二次改写
                 and (only is None or str(s.get("shot_id")) in only)),
                key=lambda s: (prio.get(str(s.get("register")), 9), float(s.get("t_start") or 0)))
            if not cands:
                continue
            if len(cands) <= short:
                picks = list(cands)
            else:  # 均匀铺开: 等距取样去重
                step = len(cands) / short
                _seen: set[str] = set()
                picks = []
                for i in range(short):
                    c = cands[min(len(cands) - 1, int(i * step))]
                    if c["shot_id"] not in _seen:
                        _seen.add(c["shot_id"])
                        picks.append(c)
            arc_picks.extend(picks)
            if picks:
                logger.info("[assembler] 场%s[%s] 欠额 %d→注入 %s (帽=分区配额 %.0f%%)",
                            a, z, short, "/".join(s["shot_id"] for s in picks), frac * 100)
        for s in arc_picks:
            pair = by_sid.get(str(s.get("shot_id")))
            if not pair:
                continue
            fa_shot, fa_scene = pair
            wp = arc_world.get(str(s.get("arc_id") or ""), "")
            prompt = assemble_k2_prompt(s, fa_shot, fa_scene, bible,
                                        world_phrase=wp, person_inject=True,
                                        scene_seq=_seq.get(str(s.get("shot_id")), 0))
            s["image_prompt_zh"] = prompt
            s["keyframe_zh"] = prompt
            s.pop("_kf_missing", None)
            s["person_injected"] = True  # 页面/终审可辨 (人检: 注入镜构图影响)
            n += 1
    return n


def assemble_k2_prompt(shot: dict[str, Any], fa_shot: dict[str, Any],
                       fa_scene: dict[str, Any], bible: dict[str, Any],
                       world_phrase: str = "",
                       person_inject: bool = False,
                       scene_seq: int = 0) -> str:
    """确定性组装 K2 画面句 — 零 LLM.

    次序 = 已验证的 Krea2 权重结构: 景别角度 → 场空间·微位 → 光影 → 环境 →
    角色look+位置 → 动作 → 道具 → 细节 → 氛围 (主体前置, 风格头尾由 k2.build_prompt 注)。
    1008 电影感: 场空间锚 + 微位每镜换 — 空间连续, 镜头语言变奏。
    1009 拆书导演模型: 按主体寄存器选模板 — metaphor/contrast/recap 锚**概念世界**
    (world_phrase, 无实景无人物look无灯具); case/pain/cite 锚实景走原链。
    寄存器由规层 registers.assign_registers 钉标; 旧镜无 register 退原链 (宽进)。
    1019 锚变奏: scene_seq=场内时间序 — 概念域首镜立锚, 后续镜机位语+锚分句
    聚焦确定性轮转 (同锚 13 镜出图几乎一致实锤); 实景域有 position/action
    天然变奏不在此列。"""
    slots = shot.get("creative_slots") or {}
    blocking = fa_shot.get("blocking") or {}
    light = fa_scene.get("lighting") or {}
    register = str(shot.get("register") or "").strip()

    size = SIZE_MAP.get(str(fa_shot.get("size") or "MS"), "中景")
    angle = ANGLE_MAP.get(str(fa_shot.get("angle") or "eye_level"), "平视")
    location = str(fa_scene.get("location") or "").strip()
    micro = str(blocking.get("micro_position") or "").strip()
    char_name = _char_for_register(shot, blocking, bible)
    # 1010 ID-079: 替身 look 直取 (_standin 的 era_anchor 路径不在 characters 表,
    # _char_look 查不到; 有卡时两者一致)
    _sub_nm, _sub_look = _standin(bible) if register == "pain_point" else ("", "")
    look = _clean_cut((_sub_look if register == "pain_point" else "")
                      or _char_look(char_name, bible), 70)
    position = POSITION_MAP.get(str(blocking.get("position") or ""), "")

    detail = str(slots.get("scene_detail") or "").strip()
    action = str(slots.get("action") or "").strip()
    atmo = str(slots.get("atmosphere") or "").strip()

    if register in ("metaphor", "contrast", "recap"):
        # 概念域: 概念世界是主体, 隐喻过程是动作 — 无实景location/人物look/道具注入
        # (道具在概念世界里已是隐喻形态, world 映射短语自带; 双注入=域混杂)
        parts: list[str] = [f"{size}，{angle}"]
        anchor = world_phrase or location  # 无世界注册时退法层location (宽进)
        _foc = _anchor_focus(anchor, scene_seq) if scene_seq > 0 else ""
        # 1019 B 特写族裁锚 (用户令 A+B): CU/ECU 带全景锚 = 特写被远景内容稀释,
        # K2 每镜都画全景 (构图同质根因) — 聚焦句升画面主体, 全锚降级为尾注场景定位
        _cu = str(fa_shot.get("size") or "").strip().upper() in ("CU", "ECU")
        if scene_seq > 0:  # 1019: 首镜立锚, 后续镜机位变奏
            parts.append(_CAM_VARIANTS[scene_seq % len(_CAM_VARIANTS)])
        if anchor:
            if _cu and _foc:
                parts.append(f"画面主体: {_foc}")
            else:
                parts.append(anchor + (f"·{micro}" if micro else ""))
                if _foc:
                    parts.append(f"本镜聚焦: {_foc}")
        if register == "contrast":
            parts.append("左右同屏对比构图")
        lp = _light_phrase(light)
        if lp:
            parts.append(lp)
        if _cu and anchor:
            _head = _anchor_head(anchor)
            if _head:
                parts.append(f"（场景: {_head}）")
        if detail:
            parts.append(detail)
        # 1013 配额刚性 (用户拍板): 欠额场概念镜注入替身互动子句 — 位置在 detail
        # 后、action 前 (角色 look → 动作, 同实景域次序); 词面含"人物"保终审同尺命中
        if person_inject:
            _in_nm, _in_look = _standin(bible)
            if _in_look:
                parts.append(f"{(_in_nm or '职场人物').strip()}"
                             f"（{_clean_cut(_in_look, 70)}）与画面隐喻物件互动"
                             + (f"，{position}" if position else ""))
        if action:
            parts.append(action)  # 隐喻过程推进 (沙粒正漏下/大石留在筛面)
        if atmo:
            parts.append(f"氛围{atmo}")
        text = "，".join(p for p in parts if p) + "。"
        # 容量红线: 世界短语/detail 超长先裁 (他书世界库 LLM 产物可能超长)
        if len(text) > _MAX_PROMPT and detail:
            parts = [detail[:40] if p == detail else p for p in parts]
            text = "，".join(p for p in parts if p) + "。"
        if len(text) > _MAX_PROMPT:
            text = _clean_cut(text, _MAX_PROMPT) + "。"
        return text

    # 实景域 (case/pain/cite/无寄存器旧镜): 原链
    parts = [f"{size}，{angle}"]
    if location:
        parts.append(f"{location}" + (f"·{micro}" if micro else ""))
    lp = _light_phrase(light)
    if lp:
        parts.append(lp)
    if detail:
        parts.append(detail)
    char_seg = (f"{char_name}（{look}）" if look else char_name) if char_name else ""
    if char_seg:
        parts.append(char_seg + position)
    if action:
        parts.append(action)
    props = _prop_phrase(fa_shot.get("props_visible") or [], bible)
    if props:
        parts.append(props)
    if atmo:
        parts.append(f"氛围{atmo}")

    text = "，".join(p for p in parts if p) + "。"
    # 容量红线: 超长先裁道具(通常最长)再裁细节 — 保主体与动作
    if len(text) > _MAX_PROMPT and props:
        parts[parts.index(props) if props in parts else -1] = str(
            (fa_shot.get("props_visible") or [""])[0])
        text = "，".join(p for p in parts if p) + "。"
    if len(text) > _MAX_PROMPT and detail:
        parts = [detail[:40] if p == detail else p for p in parts]
        text = "，".join(p for p in parts if p) + "。"
    return text


def _motion_zh_from_slots(shot: dict[str, Any], fa_shot: dict[str, Any],
                          fa_scene: dict[str, Any], bible: dict[str, Any],
                          world_phrase: str = "") -> str:
    """1013 夜修 (用户令: K2/H3 两层一体): motion_zh 器层同源派生.

    旧路 = STRUCT 组级 motion 克隆直传 — 组 motion 自带私货实体 (灯塔/悬浮巨书
    实锤) 与器层画面句不同源, 页面两层读起来是两个世界。此函数与
    assemble_k2_prompt 吃同一份 槽/寄存器/法层, 实体集构造性一致。
    文字事件不在此写 (_ensure_motion_text 下游回填); 军规口径: 固定机位极轻微
    推近, 无横移/跟拍/摇镜词。"""
    slots = shot.get("creative_slots") or {}
    blocking = fa_shot.get("blocking") or {}
    register = str(shot.get("register") or "").strip()
    action = str(slots.get("action") or "").strip()
    atmo = str(slots.get("atmosphere") or "").strip()
    micro = str(blocking.get("micro_position") or "").strip()
    if register in ("metaphor", "contrast", "recap") and world_phrase:
        anchor = world_phrase + (f"·{micro}" if micro else "")
        subj = f"{anchor}里的隐喻过程"
    else:
        nm = _char_for_register(shot, blocking, bible)
        _sub_nm, _sub_look = _standin(bible) if register == "pain_point" else ("", "")
        look = _clean_cut((_sub_look if register == "pain_point" else "")
                          or _char_look(nm, bible), 70)
        subj = f"{nm}（{look}）" if (nm and look) else (nm or "画面主体")
    out = f"主体：{subj}。" + (f"{action}。" if action else "")
    if atmo:
        out += f"环境微动与氛围：{atmo}。"
    out += "运镜固定，仅允许极轻微推近，无横移跟拍。"
    return out[:600]


def assemble_h3_anim(shot: dict[str, Any], fa_shot: dict[str, Any],
                     fa_scene: dict[str, Any], bible: dict[str, Any],
                     world_phrase: str = "") -> dict[str, Any]:
    """确定性组装 H3 anim 骨架 — 零 LLM.

    opening_desc/beats 与组装后的 K2 画面句同源 (中英双语同步 1006 规则的结构性
    根治: 同一份槽值拼两种语言, 不可能再漂移)。duration 取镜时间窗。
    1009 寄存器: 概念域 (metaphor/contrast/recap) 场景锚 = 概念世界短语。"""
    slots = shot.get("creative_slots") or {}
    light = fa_scene.get("lighting") or {}
    blocking = fa_shot.get("blocking") or {}
    register = str(shot.get("register") or "").strip()

    size_en = SIZE_EN.get(str(fa_shot.get("size") or "MS"), "medium shot")
    angle_en = ANGLE_EN.get(str(fa_shot.get("angle") or "eye_level"), "eye level")
    action = str(slots.get("action") or "").strip()
    detail = str(slots.get("scene_detail") or "").strip()
    atmo = str(slots.get("atmosphere") or "").strip()
    char_name = str(blocking.get("character") or "").strip()
    location = str(fa_scene.get("location") or "").strip()
    micro = str(blocking.get("micro_position") or "").strip()

    if register in ("metaphor", "contrast", "recap") and world_phrase:
        scene_en = " ".join(x for x in (world_phrase, micro) if x)
        char_name = ""  # 概念域无定妆人物 — 主体是世界实体, 动作是隐喻过程
    else:
        scene_en = " ".join(x for x in (location, micro) if x)
        char_name = _char_for_register(shot, blocking, bible)
        if register == "pain_point":
            char_name = "a busy office worker"  # 观众替身泛化指称 (H3 英文骨架)
    opening = f"{size_en}"
    if scene_en:
        opening += f", scene: {scene_en}"
    opening += f", {_light_en(light)}: {char_name} {action}".strip()
    if detail:
        opening += f", {detail}"
    try:
        dur = round(float(shot.get("t_end") or 0) - float(shot.get("t_start") or 0), 2)
    except (TypeError, ValueError):
        dur = 0.0
    dur = dur or 4.0

    motion = (f"Subject and task: {char_name} {action}. "
              f"Camera holds {angle_en}, only a very slight push-in allowed. ")
    if atmo:
        motion += f"Ambient mood: {atmo}. "
    motion += "Objects already in frame may move naturally; flat colors stay stable."

    prev_anim = shot.get("anim") or {}
    return {
        "duration_s": dur,
        "opening_desc": opening[:400],
        "physical_lock": str(prev_anim.get("physical_lock") or _PHYSICAL_LOCK),
        "beats": [{"t_start": 0, "t_end": dur, "motion": motion[:500]}],
        "ending": str(prev_anim.get("ending") or "hold"),
        "screen_exception": str(prev_anim.get("screen_exception") or ""),
    }


def fa_index(doc: dict[str, Any], ss: dict[str, Any]) -> dict[str, tuple[dict, dict]]:
    """shot_id → (法层 shot, 法层 scene) — 比例映射对位.

    1008 修 (ep2 实锤: 法层每场5镜 vs doc 装包4/5/8/6/10/12镜 — 索引对位
    超出法层镜数的镜全落空): 法层定义的是节奏 (定场→叙事→收束), doc 实际
    镜数由音频装包决定, 两者永远不等。doc 第 j/N 镜 → 法层第 j*F//N 镜 —
    首镜必对 fa[0] (定场), 末镜必对 fa[F-1] (收束), 中段按比例吃节奏。"""
    out: dict[str, tuple[dict, dict]] = {}
    for scene in ss.get("scenes") or []:
        arc = str(scene.get("arc_id") or "")
        fa_shots = scene.get("shots") or []
        if not fa_shots:
            continue
        doc_ids = [str(s.get("shot_id")) for s in doc.get("shots") or []
                   if str(s.get("arc_id") or "") == arc]
        N = len(doc_ids)
        expanded = _expand_fa_shots(fa_shots, N)  # 相邻镜必变 (尺寸插值+微位方位)
        for sid, fa_shot in zip(doc_ids, expanded):
            out[sid] = (fa_shot, scene)
    return out


def assemble_episode(doc: dict[str, Any], ss: dict[str, Any], bible: dict[str, Any],
                     only: set[str] | None = None, dry: bool = False,
                     persist: bool = True) -> int:
    """整集组装: 有创意槽 + 法层对位的镜 → 覆写 image_prompt_zh/keyframe_zh/keyframe_en/anim.

    已有生成产物 (img_done/approved/anim_done) 的镜跳过 — 磨合成果不冲掉,
    重规划该镜时自然走新组装。返回组装镜数。
    persist=False (1014): 管线内嵌调用 (scene_writer._fallback_scene 重试网内)
    不落盘 — 落盘权在规划过闸之后; 旧版中间态带病落盘, 续跑「有镜即 done」
    判定够不着 → 全局闸无限同错 (ep4 A6 死锁实锤)。CLI/端点直调默认 True 不变。
    """
    by_sid = fa_index(doc, ss)
    # 1009 寄存器: 每弧概念世界短语 (metaphor/contrast/recap 的场景锚)
    from .registers import world_visual
    arc_world = {str(a.get("arc_id") or ""): world_visual(doc, str(a.get("visual_world") or ""))
                 for a in doc.get("arcs") or []}
    _seq = _scene_seq_map(doc)  # 1019: 锚变奏场内序 (doc 顺序非时间序, 按 t_start 排名)
    n = 0
    n_default = 0
    for shot in doc.get("shots") or []:
        sid = str(shot.get("shot_id"))
        if only is not None and sid not in only:
            continue
        if shot.get("brand_card") or shot.get("status") in ("img_done", "approved", "anim_done"):
            continue
        # 1013 P0 阶段2: 单写者镜免覆写 — 五件套是最终产物, 器层只服务 fallback 场
        # (多写者互相稀释的根源; writer_audit 断言 writer 镜非旁路)。
        if str(shot.get("writer_source") or "") == "writer":
            continue
        pair = by_sid.get(sid)
        if not pair:
            continue
        if not shot.get("creative_slots"):
            # 1009 P1 三级降级链第三级: 术层没填上的镜 → 寄存器默认槽直装
            # (STRUCT 宪法下 plan_scene 不再产 keyframe, 跳过 = 无首帧)。
            # 打标 slots_source=default 供人检/巡检; 有真槽时清标防旧账。
            from .registers import default_slots
            shot["creative_slots"] = default_slots(str(shot.get("register") or ""))
            shot["slots_source"] = "default"
            n_default += 1
        else:
            shot.pop("slots_source", None)
        fa_shot, fa_scene = pair
        wp = arc_world.get(str(shot.get("arc_id") or ""), "")
        prompt = assemble_k2_prompt(shot, fa_shot, fa_scene, bible, world_phrase=wp,
                                    scene_seq=_seq.get(sid, 0))
        # 1017 W6 军规闸: 器层产出同过统一执法 (长度+脸向, ruleset 单一源)
        from . import ruleset as _rs
        from . import k2 as _k2
        _kf, _fx = _rs.enforce_keyframe(prompt, _k2._person_in(prompt, str(doc.get('book_title') or '')))
        if _fx:
            prompt = _kf
        shot["image_prompt_zh"] = prompt
        shot["keyframe_zh"] = prompt
        shot["keyframe_en"] = ""  # EN 由 anim.opening_desc 承担, 清旧值防双语漂移
        # 1013 夜修 (两层一体): motion_zh 同源派生 — 组级克隆 motion 私货实体根治
        shot["motion_zh"] = _motion_zh_from_slots(shot, fa_shot, fa_scene, bible,
                                                  world_phrase=wp)
        # 1013 夜修: 覆写即清占位标 — 组模板章已废, 旧 _kf_missing 残留会误导
        # 装配后终审 (person_quota_issues) 把真画面句当占位豁免
        shot.pop("_kf_missing", None)
        shot["anim"] = assemble_h3_anim(shot, fa_shot, fa_scene, bible, world_phrase=wp)
        # 1022 批7: 组装后自愈回填 — 外层 assemble 二次组装会把 _fallback_scene
        # 批4 的回填再次清空 (A1 keyframe_en 0/8 实锤); 在 anim 生成后本层回填,
        # 所有路径一次修净 (writer 镜在循环开头已豁免, 不受影响)
        _en = str((shot.get("anim") or {}).get("opening_desc") or "").strip()
        if _en:
            shot["keyframe_en"] = _en
        # 1008 P8: 法层参数钉在镜上 (页面每镜卡片灰标签: ELS·鸟瞰·窗光)
        # 1008 电影感: location (场空间) + micro (微位) 一并钉标
        # 1009 寄存器: register/reg_ref 一并钉标 (页面灰标签 + 校验层消费)
        light = fa_scene.get("lighting") or {}
        shot["fa"] = {
            "size": str(fa_shot.get("size") or ""),
            "angle": str(fa_shot.get("angle") or ""),
            "location": str(fa_scene.get("location") or "")[:12],
            "micro": str((fa_shot.get("blocking") or {}).get("micro_position") or "")[:10],
            "mood": str(light.get("mood") or ""),
            "light": str(light.get("key_source") or "")[:12],
            "props": [str(p) for p in (fa_shot.get("props_visible") or [])[:3]],
            "register": str(shot.get("register") or ""),
            "reg_ref": str(shot.get("reg_ref") or "")[:12],
        }
        shot.pop("_enhanced1", None)
        shot.pop("_enhanced2", None)
        n += 1
        if dry:
            print(f"--- {sid} ({fa_shot.get('size')}/{fa_shot.get('angle')}) ---")
            print(prompt)
    if n and not dry:
        # 1013 配额刚性 (用户拍板): 组装后复量, 欠额场的概念镜重掷带替身注入 —
        # 与 director2.person_quota_issues 同一把尺, 注入词面含"人物"保命中
        n_inj = _quota_person_inject(doc, ss, bible, by_sid, arc_world, only=only)
        if n_inj:
            logger.info("[assembler] 配额刚性注入 %d 镜 (Q版替身×隐喻物件互动)", n_inj)
        if persist:
            shots_mod.save(doc)
        logger.info("[assembler] %d 镜确定性组装 ✓ (器层零 LLM%s)",
                    n, "" if persist else ", 未落盘 (调用方过闸后落)")
        if n_default:
            logger.warning("[assembler] ⚠ %d/%d 镜走默认槽 (slots_source=default, 人检): %s",
                           n_default, n,
                           "/".join(str(s.get("shot_id")) for s in doc.get("shots") or []
                                    if s.get("slots_source") == "default")[:120])
        # 1008 P7: 校验报告带 doc/bible 刷新 (术层覆盖/组装覆盖/道具生命周期)
        try:
            from .shooting_script import save as save_ss
            from .shooting_script import validate_report
            ss["validation"] = validate_report(ss, doc=doc, bible=bible)
            save_ss(doc["book_title"], int(doc.get("ep") or 0), ss)
        except Exception as exc:  # noqa: BLE001 — 报告刷新失败不回滚组装
            logger.warning("[assembler] 校验报告刷新失败: %s", exc)
        # 1009 P4 收尾 (设计 §6.1): 可重放断言 — 组装后审计"非器层写者"
        try:
            from .video_qc import writer_audit
            _wa = writer_audit(doc, ss=ss, bible=bible)
            if _wa:
                logger.warning("[writer-audit] ⚠ %d 镜旁路写者 (人检): %s",
                               len(_wa), "; ".join(_wa[:4]))
                ss.setdefault("validation", {})["writer_audit"] = _wa
                from .shooting_script import save as save_ss2
                save_ss2(doc["book_title"], int(doc.get("ep") or 0), ss)
        except Exception as exc:  # noqa: BLE001 — 审计失败不阻断
            logger.warning("[assembler] writer_audit 失败: %s", exc)
    return n


def _cli() -> None:
    ap = argparse.ArgumentParser(description="器层确定性组装 (K2 画面句 + H3 骨架)")
    ap.add_argument("--book", required=True)
    ap.add_argument("--ep", type=int, required=True)
    ap.add_argument("--only", help="逗号分隔 shot_id 白名单")
    ap.add_argument("--dry", action="store_true", help="只打印不落盘")
    a = ap.parse_args()

    from app.config import load_config, set_config
    set_config(load_config())
    logging.basicConfig(level=logging.INFO)

    from .shooting_script import load as load_ss
    doc = shots_mod.load_doc(a.book, a.ep)
    ss = load_ss(a.book, a.ep)
    if not ss:
        raise SystemExit("无 shooting_script.json — 先跑规划产法层")
    bible = json.loads((shots_mod.anim_output_root() / "_资产" / f"圣经_{a.book}.json")
                       .read_text(encoding="utf-8"))
    only = set(a.only.split(",")) if a.only else None
    # 1009 寄存器: CLI 直跑也先钉标 (零LLM幂等) — 未钉镜退实景原链会让围栏失效
    from .registers import assign_registers
    assign_registers(doc, bible)
    n = assemble_episode(doc, ss, bible, only=only, dry=a.dry)
    print(f"\n[assembler] 组装 {n} 镜" + (" (dry)" if a.dry else ""))


if __name__ == "__main__":
    _cli()
