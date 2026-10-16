# -*- coding: utf-8 -*-
"""法层: 拍摄脚本 (Episode Shooting Script) — 每集的镜头语言设计.

道法术器架构 (1007 v3 定稿):
  道 = 世界规则手册 (书级, 已落地)
  法 = 本文件 (集级: 镜头序/光影方案/色彩节奏/连续性)
  术 = 导演LLM填创作槽 (待实现)
  器 = K2/H3确定性组装 (待实现)

生成时机: 规划(立意闸)阶段, 切场完成后、分镜续跑前.
产出: ep目录/shooting_script.json (机器可读, 供术/器/校验消费).
"""
from __future__ import annotations

import json
import logging
import re as _re
import time
from pathlib import Path
from typing import Any

from . import shots as shots_mod

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# 法层 LLM 的 system prompt — 只做镜头语言设计, 不写场景内容
_SHOOTING_SYS = """你是TED讲座的图文导演+摄影指导(DP)合体。为拆书动画设计每场戏的镜头语言。

这不是电视剧 — 没有演员等你拍全覆盖。口播(歌手)是主线, 画面(电影)讲到什么给什么;
概念世界是默认片场, 人物只在专属寄存器出场。

你只做四件事:
1. 场空间锚定: 每场按**主寄存器**选域 —
   - 概念场 (句型分布以 概念/总结/互动 为主): location = 该场指定的概念世界名
     (概念世界自带视觉体系, 它就是片场 — 不是实景办公室)
   - 案例场 (案例句为主): location 从实景世界清单选 (真实地点讲真实故事)
   - 引用场 (引用句为主): location 选书房类实景 (作者+书)
2. 镜头序设计: 每场景别递进 (ELS定场=概念世界全貌→MS叙事→CU情绪), 相邻镜景别至少差一级
3. 光影方案: 每场从世界规则的光影法度中选用 — 只定方向/色温/光比 (光效语言),
   不选灯具实体 (台灯/面板灯不入画)
4. 连续性约束: 人物位置/视线链/道具状态 + 每镜微位 (场空间内不同角落, 禁重复) —
   概念场的微位 = 概念世界的不同侧面 (筛面/筛孔下/沙堆/石堆)

人物纪律 (MTV 手法·铁律):
- blocking.character **可选** — 概念场/互动/总结场的镜一律不填人物 (空镜/概念实体镜)
- 作者 (如德鲁克) 只准出现在引用场的镜 — 他是被引用的权威, 不是主演
- 案例人物只在案例场填; 讲者只在品牌场填 (品牌镜由品牌卡链处理, 无需你设计)

你不做的事:
- 不写场景内容细节(那是导演/术层的事)
- 不写人物动作(那是术层填空的事)
- 不写具体的光影描述文字(那是渲染组装的事)

输入: 世界规则摘要 + 概念世界注册表 + 本集场次(含句型分布与指定概念世界) + 口播参考
输出: 每场的镜头序列 JSON

规则:
1. 每场首镜必须 ELS 或 LS(定场), 末镜回 MLS 以上(收束)
2. 相邻镜景别至少差一级(ELS→MS✓, MS→MS✗), 角度也要变
3. 同场光源/方向/色温一致(从世界规则mood_map选)
4. 道具按生命周期出现(introduce集才首现)
5. 视线链: 相邻镜的视线方向要有连贯逻辑 (上镜看向哪, 下镜顺着接) — 仅人物镜需要
6. 微空间变奏 (1008 用户令·电影感): 全场同属 location 大空间, 每镜 micro_position
   换场内角落, 组内禁重复 — 空间连续+镜头语言变化

输出严格 JSON:
{
  "scenes": [
    {
      "arc_id": "A1",
      "location": "大空间名 (概念场=指定的概念世界名; 案例场=实景清单选, 2-6字)",
      "lighting": {
        "mood": "从世界规则mood_map选一个情绪",
        "key_direction": "主光方向(如 侧光45度/顶光)",
        "temp": "色温",
        "fill_ratio": "补光比(如4:1)"
      },
      "shots": [
        {
          "order": 1,
          "size": "ELS",
          "angle": "birds_eye",
          "blocking": {"character": "角色引用(仅人物镜; 概念镜省略此字段)",
                        "position": "画面位置(如right_third)",
                        "micro_position": "场空间内微位(2-6字, 如 工位/窗边/筛面)"},
          "props_visible": ["道具引用列表"],
          "transition_to_next": "hard_cut|match_cut|dissolve",
          "creative_hint": "一句提示术层该填什么(概念镜: 隐喻过程推进到哪一刻)"
        }
      ],
      "continuity": {
        "eyeline_chain": ["s01:窗外", "s02:电脑"],
        "prop_states": "道具状态变化描述"
      }
    }
  ]
}"""


def shooting_script_path(book_title: str, ep: int) -> Path:
    return shots_mod.ep_dir(book_title, ep) / "shooting_script.json"


def load(book_title: str, ep: int) -> dict[str, Any] | None:
    """加载已有拍摄脚本, 不存在返回 None."""
    p = shooting_script_path(book_title, ep)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def generate(book_id: str, book_title: str, ep: int) -> dict[str, Any]:
    """生成本集拍摄脚本 — 道文档 + 切场结果 → 镜头序/光影/连续性.

    在规划(立意闸)阶段调用, 切场完成后. 产出 shooting_script.json.
    """
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.llm_service import get_llm_service as _llm, _parse_json

    # ① 读道文档
    bible_path = shots_mod.anim_output_root() / "_资产" / f"圣经_{book_title}.json"
    if not bible_path.exists():
        raise FileNotFoundError(f"道文档不存在: {bible_path} (先生成世界规则)")
    bible = json.loads(bible_path.read_text(encoding="utf-8"))

    # ② 读已有切场结果 (arcs from shots.json skeleton)
    doc = shots_mod.load_doc(book_title, ep)
    arcs = doc.get("arcs") or []
    if not arcs:
        raise ValueError("无切场结果 (先跑规划)")

    # ③ 读本集稿件段落 (用于镜头序设计参考)
    import sqlite3

    from .config import load
    con = sqlite3.connect(load().db_path)
    row = con.execute(
        "SELECT script_text FROM book_episodes WHERE book_id=? AND ep_index=?",
        (book_id, ep)).fetchone()
    con.close()
    script_text = (row[0] if row else "")[:2000]

    # ④ 构建法层输入 (注意力预算: 只给必要信息)
    dao_summary = _summarize_dao(bible, ep)
    # 1009 拆书导演模型: 弧带 概念世界 + 句型分布 (DP 按主寄存器选 location 域)
    from .registers import arc_register_mix, world_map
    wm = world_map(doc)
    concept_block = "\n".join(f"  {k}: {v[:50]}" for k, v in wm.items()) or "  (无注册表 — 全走实景)"
    arcs_summary = "\n".join(
        f"  {a.get('arc_id', '?')} [{a.get('t_start', '?')}-{a.get('t_end', '?')}s] "
        f"句型分布[{arc_register_mix(a)}] 概念世界={a.get('visual_world', '未定')}: "
        f"{str(a.get('narrative', ''))[:60]}"
        for a in arcs)

    user = f"""世界规则摘要:
{dao_summary}

概念世界注册表 (概念场的 location 从这里选, 世界名+视觉体系):
{concept_block}

本集场次:
{arcs_summary}

本集口播前2000字:
{script_text[:1500]}

请设计每场的镜头语言 (概念场 location=概念世界名; 作者只在引用场出镜)。"""

    # ⑤ LLM 生成
    logger.info("[shooting] 法层生成中 (LLM 1-2分钟)…")
    raw = _llm().chat(_SHOOTING_SYS, user, model="pro", temperature=0.3, tag="fa")
    data = _parse_json(str(raw)) or {}

    if not data.get("scenes"):
        raise ValueError("法层产出不可解析 (无 scenes)")

    # ⑥b 1008 规层 R4: 违禁词净化 — creative_hint/props/连续性 全量扫描覆写
    n_fixed = _sanitize_scenes(data.get("scenes") or [])
    if n_fixed:
        logger.warning("[shooting] 规层闸②: %d 处违禁词已覆写 (法层)", n_fixed)

    # ⑥ 包装为完整文档
    result = {
        "version": SCHEMA_VERSION,
        "book_title": book_title,
        "ep": ep,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dao_ref": f"圣经_{book_title}.json",
        **data,
    }
    # ⑥c 1008 P7: 校验报告随档落盘 (页面徽章消费; 装配层会带 doc/bible 刷新)
    result["validation"] = validate_report(result)

    # ⑦ 落盘
    p = shooting_script_path(book_title, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[shooting] %s ep%d → %s (%d 场)",
                book_title, ep, p.name, len(data.get("scenes") or []))

    return result


def _summarize_dao(bible: dict, ep: int = 0) -> str:
    """道文档 → 法层输入摘要 (只提取镜头语言相关的字段, 减少注意力负载)."""
    parts = []

    ident = bible.get("identity") or {}
    if ident:
        parts.append(f"调性: {ident.get('one_line', '')}")

    # 1008 修 (电影感·空间锚): 世界清单下发 — location 只准从这里选, 空间可跨场延续
    world = bible.get("world") or {}
    exists = world.get("exists") or []
    if exists:
        parts.append(f"世界清单 (location 只准从这里选): {', '.join(str(x) for x in exists[:8])}")
    era = world.get("era")
    if era:
        parts.append(f"年代: {str(era)[:60]}")

    lighting = bible.get("lighting") or {}
    if lighting:
        parts.append(f"光源哲学: {lighting.get('doctrine', '')}")
        moods = lighting.get("mood_map") or {}
        mood_str = "; ".join(f"{m}={c.get('ratio','')} {c.get('temp','')}" for m, c in moods.items())
        if mood_str:
            parts.append(f"情绪光影: {mood_str}")
        allowed = lighting.get("allowed_sources") or []
        if allowed:
            # 1009 拆书导演模型: 光源清单只供理解世界 — 光影输出只准光效语言
            # (方向/色温/光比), 灯具实体不入画 (台灯×12 镜实锤)
            parts.append(f"世界光感参考 (只取氛围, 不选灯具): {', '.join(allowed)}")

    color = bible.get("color") or {}
    if color:
        pal = color.get("palette") or {}
        parts.append(f"色板: 基底{pal.get('base','')} 主色{pal.get('primary','')} 点缀{pal.get('accent','')}")

    props = bible.get("props") or []
    if props:
        # 1008 修 (One_Page_Binder ep2 提前登场实锤): 集窗随道具下发 —
        # 否则 prompt rule "道具按生命周期出现" 无数据支撑, LLM 必滑
        prop_str = "; ".join(
            f"{p.get('ref','')}[{str((p.get('lifecycle') or {}).get('introduce') or '?')}"
            f"-{str((p.get('lifecycle') or {}).get('resolve') or '?')}]"
            for p in props)
        parts.append(f"道具体系 (名字[登场集-退场集], 本集第{ep}集): {prop_str}")

    # 1008 修 (法层自造名实锤: blocking.character 写了圣经外的'老谭'):
    # 角色名单显式给到 DP — blocking.character 只准用名单内的准确名字
    names = [str(c.get("name") or "") for c in (bible.get("characters") or []) if c.get("name")]
    if names:
        parts.append(
            f"角色名单 (blocking.character 只准用这些名字): {', '.join(names)}\n"
            f"  人物三班制 (MTV 手法·1009 用户令): {names[0] if names else '作者'}=作者(只准引用场客串); "
            f"讲者=品牌场专属(品牌卡链处理); 其余=案例人物(只准案例场)")

    motifs = bible.get("motifs") or []
    if motifs:
        motif_str = "; ".join(m.get("name", "") for m in motifs)
        parts.append(f"视觉母题: {motif_str}")

    forbidden = bible.get("forbidden") or []
    # 1008 规层 R3: 平台限流/禁止类目并入禁区行 (一行标签, 不展开词表 —
    # 法层只做镜头序设计, 具体词由出口确定性扫描兜底)
    from app.services.compliance import visual_platform_labels
    _labels = visual_platform_labels()
    if _labels:
        forbidden = list(forbidden) + ["/".join(_labels) + " 类物品一概不入画面"]
    if forbidden:
        parts.append(f"禁区: {', '.join(forbidden[:10])}")

    return "\n".join(f"  {p}" for p in parts if p)


def _sanitize_scenes(scenes: list[dict]) -> int:
    """规层闸② (1008): 法层文本违禁词扫描+覆写 — creative_hint/props/blocking/连续性.

    compliance_visual.json 单一事实源, 零 LLM。返回修复处数。
    """
    from app.services.compliance import sanitize_visual_text
    n = 0

    def _fix(obj: dict, key: str) -> None:
        nonlocal n
        val = obj.get(key)
        if not isinstance(val, str) or not val:
            return
        clean, hits = sanitize_visual_text(val)
        if hits:
            obj[key] = clean
            n += len(hits)
            logger.warning("[shooting] 规层覆写: %s 命中 %s", key, "/".join(hits))

    for scene in scenes:
        # 1008 电影感: 微位场内去重 (LLM 常一角落堆 4 镜, 两方位后缀必撞 —
        # A5 书桌前×4 实锤)。四方位轮换 + 数字兜底; 幂等 = 先剥旧后缀归一再判。
        seen_mp: dict[str, int] = {}
        _dirs = ("左", "右", "后", "侧")
        for shot in scene.get("shots") or []:
            blk = shot.get("blocking") or {}
            mp = str(blk.get("micro_position") or "").strip()
            base = mp.split("·", 1)[0].strip() if mp else ""  # 剥旧后缀 (schema 无·)
            if not base:
                continue
            if base in seen_mp:
                seen_mp[base] += 1
                i = seen_mp[base] - 1  # 第几次重复 (0-based)
                suffix = (_dirs[i % len(_dirs)]
                          + (str(i // len(_dirs) + 1) if i >= len(_dirs) else ""))
                blk["micro_position"] = f"{base}·{suffix}"
                n += 1
            else:
                seen_mp[base] = 1
                if mp != base:
                    blk["micro_position"] = base  # 归一 (剥旧后缀)
                    n += 1
        for shot in scene.get("shots") or []:
            _fix(shot, "creative_hint")
            _fix((shot.get("blocking") or {}), "position")
            props = shot.get("props_visible") or []
            new_props = []
            for prop in props:
                if isinstance(prop, str):
                    clean, hits = sanitize_visual_text(prop)
                    if hits:
                        n += len(hits)
                        logger.warning("[shooting] 规层覆写: props 命中 %s", "/".join(hits))
                    new_props.append(clean or prop)
                else:
                    new_props.append(prop)
            if new_props != props:
                shot["props_visible"] = new_props
        cont = scene.get("continuity") or {}
        _fix(cont, "prop_states")
    return n


def save(book_title: str, ep: int, ss: dict[str, Any]) -> None:
    """拍摄脚本落盘 (校验报告刷新时由装配层调用)."""
    p = shooting_script_path(book_title, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ss, ensure_ascii=False, indent=2), encoding="utf-8")


def _split_words(visual: str) -> list[str]:
    """概念世界视觉短语 → 关键词候选 (按标点/斜杠切, ≥2字) — 隐喻对齐闸用."""
    return [w for w in _re.split(r"[，,。/、；;：:\s]+", str(visual)) if len(w) >= 2]


def validate_report(ss: dict, doc: dict | None = None,
                    bible: dict | None = None) -> dict:
    """法校验报告 (1008 P7): 分项结构化结果 — 存 ss["validation"], 页面徽章消费.

    items: [{name, status: "ok"|"warn", detail, count, problems[]}];
    problems 保持逐条粒度 (validate() 摊平消费, 日志不降级)。
    doc/bible 可选: 给了则追加 术层槽覆盖/器层组装/道具生命周期 三项。"""
    scenes = ss.get("scenes") or []
    ep = int(ss.get("ep") or 0)
    SIZE_ORDER = ["ELS", "LS", "MLS", "MS", "MCU", "CU", "ECU"]

    seq_problems: list[str] = []
    light_problems: list[str] = []
    ban_problems: list[str] = []
    micro_problems: list[str] = []
    n_light_checked = 0

    from app.services.compliance import check_visual
    for scene in scenes:
        arc = scene.get("arc_id", "?")
        shots = scene.get("shots") or []
        if not shots:
            seq_problems.append(f"{arc}: 无镜头")
            continue

        # 1008 电影感: 场空间锚 + 微位禁重复
        if not str(scene.get("location") or "").strip():
            micro_problems.append(f"{arc}: 缺 location 场空间锚")
        seen_micro: dict[str, int] = {}
        for k, sh in enumerate(shots):
            mp = str((sh.get("blocking") or {}).get("micro_position") or "").strip()
            if not mp:
                continue  # 微位缺标软容忍 (老法层兼容)
            if mp in seen_micro:
                micro_problems.append(
                    f"{arc} shot{k+1}: 微位「{mp}」与 shot{seen_micro[mp]+1} 重复 — 微空间变奏要求")
            else:
                seen_micro[mp] = k

        first = shots[0]
        if first.get("size") not in ("ELS", "LS"):
            seq_problems.append(f"{arc}: 首镜 {first.get('size')} 非定场(需ELS/LS)")
        last = shots[-1]
        if last.get("size") in ("CU", "ECU"):
            seq_problems.append(f"{arc}: 末镜 {last.get('size')} 过近(需MLS以上)")
        for i in range(1, len(shots)):
            prev, cur = shots[i - 1], shots[i]
            try:
                pi = SIZE_ORDER.index(prev.get("size", "MS"))
                ci = SIZE_ORDER.index(cur.get("size", "MS"))
                if abs(pi - ci) < 1:
                    seq_problems.append(
                        f"{arc} shot{i+1}: 景别{prev.get('size')}→{cur.get('size')} 差距不足")
            except ValueError:
                pass

        light = scene.get("lighting") or {}
        if not light:
            light_problems.append(f"{arc}: 无光影方案")
        else:
            n_light_checked += 1
            # 1009: key_source 已退役 (灯具不入画) — 判据改光效语言 key_direction
            if not ((light.get("key_direction") or light.get("key_source"))
                    and light.get("temp")):
                light_problems.append(f"{arc}: 光影缺方向/色温")

        blob = " ".join(filter(None, [str(sh.get("creative_hint") or "") for sh in shots]))
        blob += " " + " ".join(str(p) for sh in shots for p in (sh.get("props_visible") or []))
        hits = check_visual(blob)
        if hits:
            ban_problems.append(f"{arc}: 违禁词残留 {'/'.join(hits[:3])}")

    items = [
        {"name": "镜头序 (首镜定场/末镜收束/相邻景别差)", "problems": seq_problems,
         "ok": f"{len(scenes)} 场景别递进全部通过"},
        {"name": "微空间 (场空间锚+微位变奏)", "problems": micro_problems,
         "ok": f"{len(scenes)} 场空间锚定, 微位零重复"},
        {"name": "光影方案 (每场方向/色温)", "problems": light_problems,
         "ok": f"{n_light_checked} 场方向/色温/补光比齐备"},
        {"name": "违禁残扫 (规层 R4)", "problems": ban_problems,
         "ok": "法层文本零违禁词"},
    ]

    if bible:
        # 道具生命周期: ref 出场集 < introduce 集 = 提前登场
        life_problems: list[str] = []
        seen_refs: set[str] = set()
        for scene in scenes:
            for sh in scene.get("shots") or []:
                for p in sh.get("props_visible") or []:
                    seen_refs.add(str(p).split("(", 1)[0].strip())
        for prop in bible.get("props") or []:
            ref = str(prop.get("ref") or "")
            intro = str((prop.get("lifecycle") or {}).get("introduce") or "")
            digits = _re.search(r"第(\d+)集", intro)
            if ref in seen_refs and digits and ep and ep < int(digits.group(1)):
                life_problems.append(f"{ref} 提前登场 (introduce={intro}, 本集ep{ep})")
        items.append({"name": "道具生命周期 (登场集对账)", "problems": life_problems,
                      "ok": "出场道具均在生命周期窗口内"})

    if doc:
        # 术层槽覆盖 + 器层组装覆盖 — 分母只数待产镜 (planned/failed 非品牌);
        # 已产物镜走旧链 keyframe, 缺槽/缺fa 属正常不算欠账
        need = [s for s in doc.get("shots") or []
                if not s.get("brand_card")
                and s.get("status") not in ("img_done", "approved", "anim_done")]
        total = len(need)
        with_slots = sum(1 for s in need if s.get("creative_slots"))
        with_fa = sum(1 for s in need if s.get("fa"))
        slot_problems = [] if with_slots >= total else \
            [f"{total - with_slots} 镜无创意槽 (重跑 ▶️分镜续跑 可补)"]
        asm_problems = [] if with_fa >= total else \
            [f"{total - with_fa} 镜未组装 (缺创意槽则先补槽)"]
        items.append({"name": f"术层槽覆盖 ({with_slots}/{total})",
                      "problems": slot_problems, "ok": f"{with_slots}/{total} 待产镜创意槽齐备"})
        items.append({"name": f"器层组装 ({with_fa}/{total})",
                      "problems": asm_problems, "ok": f"{with_fa}/{total} 待产镜确定性组装"})

        # 1013 P0: 三闸(主体围栏/人物密度/隐喻对齐)已迁 final_gate 终产口径
        # (立法分层: 主体规则属镜头层+终产闸, 法层报告只管法层结构四项)

    for it in items:
        it["count"] = len(it["problems"])
        it["status"] = "warn" if it["problems"] else "ok"
        it["detail"] = it["ok"] if not it["problems"] else "; ".join(it["problems"][:6])
    return {"items": items, "warns": sum(it["count"] for it in items),
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def validate(doc: dict) -> list[str]:
    """法层校验 (摊平版, planner 日志消费) — 细目粒度同旧版."""
    rep = validate_report(doc)
    return [p for it in rep["items"] for p in it["problems"]]
