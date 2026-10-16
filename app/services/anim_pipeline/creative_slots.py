# -*- coding: utf-8 -*-
"""术层: 创作槽填充 — LLM 只填 action/scene_detail/atmosphere, 不写结构参数.

道法术器架构 (1007 v3 定稿):
  法层 (shooting_script.py) 已定: size/angle/lighting/blocking/props/transition
  术层 (本文件): 根据法层模板 + 口播段落, LLM 填三个创意槽
  器层 (assembler.py): 确定性组装 K2/H3 prompt

输出: 每镜的创意槽值, 存入 shots.json 的 creative_slots 字段.

1009 P2 批量化 (设计 §4.1): 一场一调用 (47 次串行 → 6-8 次) — system 固定
(逐镜参数全在 user 位, 前缀缓存可命中), 批内动作承接, 跨场承接由代码传入
上场末镜。批量失败/缺项 → 该场退逐镜串行 (老路径保留为降级模式)。

"""
from __future__ import annotations

import logging
from typing import Any

from . import shots as shots_mod

logger = logging.getLogger(__name__)

# 术层 LLM 的 system prompt — 只做创意填空, 不做结构决策
# 1008 修 (电影感): 输入带 场空间/微位/前镜action/视线链 → 动作连续, 不并列换画
# 1009 修 (拆书导演模型): 按主体寄存器分支任务描述 — 概念镜填"隐喻过程推进到
# 哪一刻" (不是人物动作); 实景镜保持人物动作链。主体锚 reg_ref 随寄存器下发。
_CREATIVE_SYS = """你是画面描述师。根据口播段落和已定的镜头参数, 填写三个创意槽。

已定参数 (不要重复描述, 渲染层自动注入):
- 景别: {size}
- 角度: {angle}
- 光影: {lighting}
- 主体锚: {reg_task}
- 场空间: {location} (全场统一, 你的细节必须在此空间内)
- 本镜微位: {micro_position}
- 道具: {props}
- 前一镜动作: {prev_action}
- 视线承接: {eyeline}

你只填三个槽:
[action] (≤20字): {action_task}
[scene_detail] (≤30字): 微位上有什么环境细节 (道具/光线效果/背景物件; 只能在场空间内变奏, 禁换空间)
[atmosphere] (≤10字): 情绪氛围 (2-3个词)

规则:
1. 不写景别/角度/光影/服装/皮肤 — 已由其他层决定
2. {action_rule}
3. 细节必须服务叙事, 不加无关装饰; 场空间锚死, 换空间=废稿
4. 氛围用感官词, 不用抽象概念
5. scene_detail 禁重复"场空间"行的空间名 (组装层已注入); action 禁括注人物造型
6. 现代感军规 (1010 ID-079): 人物/物件锚定当代职场 — 现代服饰单品+概念物件现代
   转译, 禁古装前现代意象 (作者镜除外)

口播段落: {narration}

输出严格 JSON: {{"action": "...", "scene_detail": "...", "atmosphere": "..."}}"""

# 1009 P2: 批量版 system — 全集所有批量调用共享同一前缀 (缓存友好位);
# 逐镜参数 (寄存器/微位/道具/口播) 全在 user 行内, 不进 system。
_CREATIVE_BATCH_SYS = """你是画面描述师。输入 = 一场镜头表 (每行一镜, 带已定参数), 你为每镜填三个创意槽。

已定参数 (渲染层自动注入, 不要重复描述): 景别/角度/光影/服装/皮肤。
你只填三个槽:
- action (≤20字): 主体这一刻在做什么 (按行内寄存器任务)
- scene_detail (≤30字): 微位上的环境细节 (道具/光线效果/背景物件; 只能在行内场空间变奏, 禁换空间)
- atmosphere (≤10字): 情绪氛围 (2-3个感官词, 不用抽象概念)

寄存器 (行内标明, 决定 action 写什么):
- metaphor 概念镜: 概念世界实体在发生物理过程 — action=过程推进到哪一刻 (沙粒正漏下/大石留在筛面), 承接前镜物理过程, 不并列换画
- contrast 对比镜: 左右两侧各自的动态瞬间 (一侧膨胀/一侧收缩式可画对比), 不并列换画
- recap 展馆镜: 站点/工具依次点亮或翻页推进到哪一站
- pain_point 痛点镜: 泛化职场人在困境中的具体动作 (被消息轰炸/被会议切割), 顺着前镜承接
- case_narrative 案例镜: 案例人物具体可画的动作 ("翻看日历"✓, "思考人生"✗), 与前镜连成小故事; 行内有卡带名, 泛化人物只写衣着动作
- author_cite 作者镜: 作者与书的动作 (合上书页/笔尖落下/抬眼看向读者), 克制有仪式感
- host_brand 品牌镜: 仪式性动作 (递出工具/翻开下一页), 简洁

规则:
1. 不写景别/角度/光影/服装/皮肤 — 已由其他层决定
2. 场空间锚死 (表头), 换空间=废稿; 细节只能在空间内变奏
3. 细节服务叙事, 不加无关装饰
4. 批内按序承接: 每镜 action 接你写的前一镜 (表头"承接起点"给首镜输入)
5. 概念镜 (metaphor/contrast/recap) 无定妆人物
6. 道具列已给中文画面实体, 槽值直接用它; 禁出现任何英文单词/代号 (出口有剥除闸, 别白写)
7. scene_detail 写微位上的具体物件与光线细节, **禁重复表头场空间名** (三展台/展馆
   全景这类空间词组装层已注入, 槽里再写=双写) — 直接从物件写起
8. action 禁括注人物造型/服饰 (如"德鲁克（深灰西装…）抬手") — 名字或代称即可,
   造型由系统按角色卡注入
9. 现代感军规 (1010 ID-079): 人物与物件锚定当代职场时空 — 服饰只出现衬衫/针织衫/
   西装/西裤/休闲裤系现代单品; 概念物件做现代转译 (账本→平板台账界面、铜算盘→
   数据图表、怀表→手机时钟), 禁长袍/马褂/古装宽袖/油灯烛台等前现代意象
   (author_cite 历史人物镜除外, 按其年代近代正装)

输出严格 JSON (对象包数组, 与输入行同序同数, 一行不落):
{"slots": [{"shot_id": "原样带回", "action": "...", "scene_detail": "...", "atmosphere": "..."}]}"""

# 1009 寄存器分支: 主体锚/action 任务按寄存器换文案 (概念镜=物理过程, 实景镜=人物动作)
# (串行降级路径用; 批量路径的寄存器语义在 _CREATIVE_BATCH_SYS)
_REG_TASKS = {
    "metaphor": ("概念世界「{ref}」的实体 (概念镜主体, 无定妆人物)",
                 "这个概念过程推进到哪一刻 (沙粒正漏下/大石留在筛面/金流正汇聚 — 具体可画的物理瞬间)",
                 "动作=上一刻物理过程的延续 (前镜筛网开始漏沙, 这镜沙粒堆积成丘), 不并列换画"),
    "contrast": ("概念世界「{ref}」的双立场对比 (对比镜, 无定妆人物)",
                 "左右两侧各自在发生什么动态 (一侧膨胀/一侧收缩式的可画对比)",
                 "动作=两侧对比的动态瞬间, 不并列换画"),
    "recap": ("概念世界「{ref}」的工具/站点推进 (展馆镜, 无定妆人物)",
              "站点/工具依次点亮或翻页推进到哪一站",
              "动作=推进过程的具体一刻"),
    "pain_point": ("观众替身 (泛化职场人, 无定妆卡 — 用衣着动作描述, 不写名)",
                   "这个人物在困境中做什么动作 (被消息轰炸/被会议切割 — 具体可画)",
                   "动作要顺着前镜承接 — 前镜看向哪/做到哪, 这镜接着"),
    "case_narrative": ("案例人物「{ref}」(案例镜 — 有卡带卡, 泛化人物只写衣着)",
                       "这个人物在做什么动作 (具体的、可画的; 顺着前镜承接)",
                       '动作必须具体可画 ("翻看日历" ✓, "思考人生" ✗), 且与前镜连续成小故事, 不并列'),
    "author_cite": ("作者「{ref}」与书 (作者镜 — 执笔/翻页/抬眼, 权威客串)",
                    "作者与书的动作 (合上书页/笔尖落下/抬眼看向读者)",
                    "动作围绕书与写作, 克制有仪式感"),
    "host_brand": ("讲者品牌镜", "仪式性动作 (递出工具/翻开下一页)", "动作简洁有仪式感"),
}


def _reg_block(register: str, reg_ref: str) -> tuple[str, str, str]:
    """寄存器 → (主体锚文案, action 任务, action 规则)."""
    tpl = _REG_TASKS.get(register) or _REG_TASKS["case_narrative"]
    ref = reg_ref or "本镜主体"
    return (tpl[0].format(ref=ref), tpl[1], tpl[2])


def _sanitize_slots(action: str, detail: str, atmo: str
                    ) -> tuple[str, str, str, list[str]]:
    """规层闸③ (R5): 创意槽违禁词扫描+覆写 — 零 LLM 确定性兜底."""
    from app.services.compliance import sanitize_visual_text
    action, h1 = sanitize_visual_text(action)
    detail, h2 = sanitize_visual_text(detail)
    atmo, h3 = sanitize_visual_text(atmo)
    return action, detail, atmo, h1 + h2 + h3


import re as _re

_REF_PAT = _re.compile(r"[A-Za-z_]{3,}")


def _accept_slot(shot: dict[str, Any], slots: dict[str, Any]) -> bool:
    """单槽验收: action 非空 + ref代号剥除 + 规层净化 + 长度帽 → 写入 creative_slots."""
    action = str(slots.get("action") or "").strip()
    detail = str(slots.get("scene_detail") or "").strip()
    atmo = str(slots.get("atmosphere") or "").strip()
    if not action:
        return False
    # 1010 A/B 人检: LLM 照抄道具英文代号 (Time_Ledger/Ledger 词根 — flash 臂
    # 21 镜渗漏 vs pro 0, 两臂共病=喂料+出口双修) — 确定性剥除 3+ 英文字母序列
    # (槽值纯中文语境, 剥无副作用; 剥完空则当槽拒)
    action = _REF_PAT.sub("", action).strip("，。 、")
    detail = _REF_PAT.sub("", detail).strip("，。 、")
    atmo = _REF_PAT.sub("", atmo).strip("，。 、")
    if not action:
        return False
    action, detail, atmo, hits = _sanitize_slots(action, detail, atmo)
    if hits:
        logger.warning("[creative] %s 规层覆写: %s", shot.get("shot_id"), "/".join(hits))
    shot["creative_slots"] = {
        "action": action[:50],
        "scene_detail": detail[:80],
        "atmosphere": atmo[:30],
    }
    shot.pop("slots_source", None)
    return True


def _props_zh(props_visible: list, bible: dict[str, Any] | None) -> str:
    """道具列中文实体化 (1010 A/B 人检实锤: 法层喂英文 ref (Time_Ledger), 两臂 LLM
    都照抄进槽值 — 裸代号 K2 画不出且与器层道具 look 双写)。ref→圣经 look 前段;
    无 bible/无 look 退原文 (宽进)。"""
    if not bible:
        return ", ".join(str(x) for x in (props_visible or [])[:3])[:24]
    looks = {str(pp.get("ref") or ""): str(pp.get("look") or "")
             for pp in (bible.get("props") or [])}
    out = []
    for item in (props_visible or [])[:3]:
        ref = str(item).split("(", 1)[0].strip()
        look = looks.get(ref, "")
        out.append((look.split("，")[0][:14] or str(item)) if look else str(item))
    return ", ".join(out)[:30]


def _batch_user_block(arc_targets: list[dict], pairs: dict, arc_last: str,
                      register_hint: dict[str, tuple[str, str]],
                      bible: dict[str, Any] | None = None) -> str:
    """一场 → 批量 user 数据块 (逐镜参数全在此, system 保持全集恒定)."""
    first = pairs.get(str(arc_targets[0].get("shot_id")))
    fa_scene = (first or (None, {}))[1]
    location = str(fa_scene.get("location") or "未定")[:12]
    try:
        light = str((fa_scene.get("lighting") or {}).get("mood")
                    or fa_scene.get("lighting") or "")[:20]
    except Exception:  # noqa: BLE001 — lighting 形状不定, 取不到给空
        light = ""
    rows = []
    for shot in arc_targets:
        fa_shot, _fs = pairs.get(str(shot.get("shot_id"))) or ({}, {})
        _blk = fa_shot.get("blocking") or {}
        reg, ref = register_hint.get(str(shot.get("shot_id")), ("", ""))
        reg_label = f"{reg}" + (f"「{ref[:10]}」" if ref else "")
        props = _props_zh(fa_shot.get("props_visible") or [], bible) or "无"
        rows.append(
            f"- {shot.get('shot_id')} | {reg_label} | {fa_shot.get('size', 'MS')}/{fa_shot.get('angle', 'eye_level')}"
            f" | 微位:{str(_blk.get('micro_position') or '场内')[:10]} | 道具: {props}"
            f" | 口播: {str(shot.get('narration') or '')[:90]}")
    return (f"【场数据】场空间: {location} | 光影: {light}\n"
            f"【承接起点 (首镜的输入)】{arc_last or '(本场首镜)'}\n"
            f"【逐镜填空 (按序, 每镜 action 承接你写的前一镜; 行内寄存器决定 action 语义)】\n"
            + "\n".join(rows))


def fill_creative_slots(doc: dict[str, Any], shooting_script: dict[str, Any],
                        only: set[str] | None = None, model: str = "flash",
                        dry: bool = False, bible: dict[str, Any] | None = None,
                        persist: bool = True) -> int:
    """为每镜填充创意槽 (术层核心).

    读法层的 shot_list → 找到对应的镜 → LLM 填 action/scene_detail/atmosphere.
    已有 creative_slots 的镜跳过 (幂等). 返回填充数.
    对位 = assembler.fa_index 比例映射 (1008 修: 法层镜数≠doc镜数, 单一实现两处共用).
    1009 P2: 按场批量; model 默认 flash (1010 D3 定档: A/B 双臂 47/47 质量持平,
    flash 照抄病已被出口闸确定性兜住 — 速度成本双优); 批量失败镜退串行。
    dry=True 只填内存不落盘 (A/B 对照用)。
    persist=False (1014): 管线内嵌调用 (fallback 重试网内) 不落盘 — 落盘权在
    规划过闸之后 (同 assembler.assemble_episode 纪律, ep4 A6 带病落盘实锤)。
    """
    from app.config import load_config, set_config
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    from .assembler import fa_index

    set_config(load_config())
    scenes = shooting_script.get("scenes") or []
    if not scenes:
        logger.warning("[creative] 法层无场次, 跳过")
        return 0

    # shot_id → (法层 shot, 法层 scene) 比例映射
    pairs = fa_index(doc, shooting_script)

    # 场内动作链: arc_id → 上一镜 action (承接用; 镜序按 doc 顺序)
    arc_last_action: dict[str, str] = {}
    targets: list[dict[str, Any]] = []
    for shot in doc.get("shots") or []:
        sid = shot.get("shot_id")
        if only and sid not in only:
            continue
        # 1014 单写者教义: writer 镜五件套即最终产物, 术层不碰 (与器层免覆写同款
        # 豁免) — 旧版照填, ep4 实弹 56/58 镜白填永不消费 (浪费 flash + 假数据)
        if str(shot.get("writer_source") or "") == "writer":
            continue
        if shot.get("creative_slots"):
            arc_last_action[str(shot.get("arc_id") or "")] = \
                str((shot.get("creative_slots") or {}).get("action") or "")
            continue  # 已填, 幂等
        if str(shot.get("narration") or "").strip() and pairs.get(str(sid)):
            targets.append(shot)
    if not targets:
        return 0

    # 按场分组 (doc 顺序保持)
    by_arc: dict[str, list[dict]] = {}
    for shot in targets:
        by_arc.setdefault(str(shot.get("arc_id") or ""), []).append(shot)

    # 寄存器钉标 (幂等, 零 LLM) — 批量行内要带寄存器
    from .registers import assign_registers
    assign_registers(doc)
    register_hint = {str(s.get("shot_id")): (str(s.get("register") or ""),
                                             str(s.get("reg_ref") or ""))
                     for s in doc.get("shots") or []}

    filled = 0
    failed: set[str] = set()
    n_batch_calls = 0
    for arc_id, arc_targets in by_arc.items():
        user = _batch_user_block(arc_targets, pairs,
                                 arc_last_action.get(arc_id, ""), register_hint,
                                 bible=bible or doc.get("bible"))
        rows_n = len(arc_targets)
        max_tok = max(600, rows_n * 150)
        tag = f"creative_batch:{model}"
        try:
            # response_format guard (1009 P3·F, 设计 §5): 400 不在兜底码内会硬炸 —
            # 照 correct_article 生产用法: 带参失败降级一次不带参
            try:
                raw = _llm().chat(_CREATIVE_BATCH_SYS, user, model=model,
                                  temperature=0.3, max_tokens=max_tok,
                                  response_format={"type": "json_object"}, tag=tag)
            except Exception:  # noqa: BLE001 — response_format 网关不收则裸调一次
                raw = _llm().chat(_CREATIVE_BATCH_SYS, user, model=model,
                                  temperature=0.3, max_tokens=max_tok, tag=tag)
            n_batch_calls += 1
            data = _parse_json(str(raw)) or {}
            slot_rows = data.get("slots") if isinstance(data, dict) else data
            if not isinstance(slot_rows, list):
                slot_rows = []
            by_sid = {str(r.get("shot_id")): r for r in slot_rows
                      if isinstance(r, dict) and r.get("shot_id")}
            # 按行序验收: shot_id 匹配优先, 缺失行按位兜底 (LLM 偶发漏带 id)
            arc_failed = 0
            for i, shot in enumerate(arc_targets):
                sid = str(shot.get("shot_id"))
                r = by_sid.get(sid) or (slot_rows[i] if i < len(slot_rows) else None)
                if isinstance(r, dict) and _accept_slot(shot, r):
                    filled += 1
                    arc_last_action[arc_id] = str(shot["creative_slots"]["action"])
                else:
                    failed.add(sid)
                    arc_failed += 1
            if arc_failed:
                logger.warning("[creative] 场 %s 批量缺 %d/%d 镜 → 串行补",
                               arc_id, arc_failed, rows_n)
        except Exception as exc:  # noqa: BLE001 — 批量失败不断批, 整场退串行
            logger.warning("[creative] 场 %s 批量失败 (%s) → 整场串行", arc_id, exc)
            failed |= {str(s.get("shot_id")) for s in arc_targets}

    n_serial = 0
    if failed:
        n_serial = _fill_serial(doc, pairs, failed, arc_last_action, model,
                                bible=bible or doc.get("bible"))
        filled += n_serial

    if filled and not dry and persist:
        shots_mod.save(doc)
    logger.info("[creative] %d/%d 镜创意槽填充 ✓ (批量 %d 场调用%s + 串行 %d 镜%s)",
                filled, len(targets), n_batch_calls,
                f", model={model}" if model != "pro" else "", n_serial,
                ", dry" if dry else "")
    return filled


def _fill_serial(doc: dict[str, Any], pairs: dict, sids: set[str],
                 arc_last_action: dict[str, str], model: str,
                 bible: dict[str, Any] | None = None) -> int:
    """逐镜串行填槽 (1009 P2 起为批量失败镜的降级路径, 原逐镜实现原样保留)."""
    from app.config import load_config, set_config
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    set_config(load_config())
    filled = 0
    for shot in doc.get("shots") or []:
        sid = shot.get("shot_id")
        if str(sid) not in sids:
            continue
        if shot.get("creative_slots"):
            continue

        pair = pairs.get(str(sid))
        if not pair:
            continue
        fa_shot, fa_scene = pair

        narration = str(shot.get("narration") or "")[:120]
        if not narration.strip():
            continue

        fa_scene_shots = fa_scene.get("shots") or []
        _blocking = fa_shot.get("blocking") or {}
        _cont = fa_scene.get("continuity") or {}
        _chain = _cont.get("eyeline_chain") or []
        reg_task, action_task, action_rule = _reg_block(
            str(shot.get("register") or ""), str(shot.get("reg_ref") or ""))
        prompt = _CREATIVE_SYS.format(
            size=fa_shot.get("size", "MS"),
            angle=fa_shot.get("angle", "eye_level"),
            lighting=str(fa_shot.get("lighting_note") or
                        (fa_scene_shots and (fa_scene_shots[0].get("lighting") or {}).get("mood", "")) or
                        (fa_scene.get("lighting") or {}).get("mood", "")),
            reg_task=reg_task,
            action_task=action_task,
            action_rule=action_rule,
            location=str(fa_scene.get("location") or "未定")[:12],
            micro_position=str(_blocking.get("micro_position") or "场内")[:10],
            props=_props_zh(fa_shot.get("props_visible") or [], bible),
            prev_action=arc_last_action.get(str(shot.get("arc_id") or ""), "本场首镜"),
            eyeline=str(_chain[max(0, int(fa_shot.get("order") or 1) - 1)])[:20] if _chain else "无",
            narration=narration,
        )

        try:
            # user 消息不可为空 (kimi 网关 200+错误体拒收, 逐镜兜底换厂实锤)
            raw = _llm().chat(prompt, "按系统说明填写三个槽, 只输出 JSON。",
                              model=model, temperature=0.3,
                              max_tokens=400, tag="creative")
            slots = _parse_json(str(raw)) or {}
            if _accept_slot(shot, slots):
                filled += 1
                arc_last_action[str(shot.get("arc_id") or "")] = \
                    str(shot["creative_slots"]["action"])
        except Exception as exc:  # noqa: BLE001 — 单镜失败不断批
            logger.warning("[creative] %s 失败: %s", sid, exc)
    return filled
