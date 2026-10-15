# -*- coding: utf-8 -*-
"""第二层 facing 面向蒸馏 (2026-08-22) — 多 agent, 各拿 L0 章节跑一个维度.

设计: docs/design/蒸馏产物schema设计.md §3. 每个 facing = 一个独立 LLM 调用, 输入 L0 章节块,
输出该维度结构化 JSON, 落 data/l0/{book}/facing/facing-*.json.

面向:
- kernel      内核 (作者要扭转读者哪个认知 / 独特框架) → 补全/总纲
- units       主题单元 (units.length = 该拆几集) → 总纲/逐集 (#1 #4)
- quotes      金句分级 (格言型/情境型+context+usage) → 补全核心金句/逐集 (#3)
- cases       案例 (era_sensitive + contemporary_analogy 当代桥接)
- compliance  合规 (book_tier/sensitive_units/safe_angles) → Gate0/GateB (#2)
- readers     读者共鸣 (pain/unit_id) → 评论层/逐集戳痛点

模型: Gemma (本地语义级, 复用 distiller.GemmaClient). 输入=L0 结构化, 非原文.
命令行: python -m app.services.book_service.facing <L0目录或书路径> [--facing kernel,units]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import get_config
from .l0 import _l0_dir, _safe
from .reader import clean_book_title

logger = logging.getLogger(__name__)

__all__ = ["run_facings", "FACINGS"]

# ── 面向定义: prompt + 落盘文件名 ─────────────────────────────────
def _kernel_sys() -> str:
    return (
        "你是拆书创作系统的【内核提炼器】。基于全书 L0 结构化信息，提炼这本书的\"内核\"——"
        "作者要扭转读者哪个默认认知、用什么独特框架/方法论。"
        "作者序/前言自述是最可信的信号，优先采用。"
        "输出严格 JSON: {\"one_liner\":str,\"framework\":str,\"unique_angle\":str,"
        "\"why_read\":str,\"positioning\":str,"
        "\"reader_shift\":[str],\"eligible\":bool}。"
        "why_read=为什么这本书值得拆/读（爆火原因/同类对比/受众刚需，供导读集+挂车话术）；"
        "positioning=与同类书的差异定位。eligible=false 仅当内核模糊/无实质可讲。"
    )


_DEFAULT_AUDIENCE = "25-50 岁宝妈/职场女性（夫妻矛盾/亲子拉扯/职场委屈/讨好型内耗/情绪内耗）"


def _hooks_sys(audience: str | None = None) -> str:
    _aud = audience or _DEFAULT_AUDIENCE
    return (
        "你是拆书创作系统的【钩子设计器】。基于主题单元，为每集设计\"开篇钩子 + 结尾互动 + 下集预告\"。"
        f"目标读者={_aud}。"
        "- opening_hook: 开篇 3 秒钩子（绑定生活场景戳痛点，具体不空泛，剧透别太多）\n"
        "- closing_question: 结尾生活化互动问题（引导评论，如'你家是不是也常这样？''你会怎么选？'，不空洞求关）\n"
        "- next_teaser: 下集预告（自然引出，绑定下集主题，勾追更；最后一集写系列完结预告）\n"
        "输出严格 JSON: {\"hooks\":[{\"unit_id\":str,\"opening_hook\":str,"
        "\"closing_question\":str,\"next_teaser\":str}]}，覆盖所有单元。"
        "禁用词: 疗愈/治疗/治愈/绝对化(最/唯一/顶级)/迷信/金融/引流。"
    )


def _units_sys(audience: str | None = None) -> str:
    _aud = audience or _DEFAULT_AUDIENCE
    return (
        "你是拆书创作系统的【主题单元划分器】。把这本书划分为\"能独立讲 3.5-4.5 分钟\"的主题单元，"
        "形成一套完整的拆书系列。**系列必须有清晰的三段结构（漏斗）**：\n"
        "- **第 1 单元 = 全书导读**：为什么要读/买这本书（最直白的统揽全局介绍：这书到底讲了什么、解决什么问题、谁适合读）\n"
        "- **中间单元 = 内容拆解**：书里核心内容的讲解（每单元一个可讲透的主题，层层深入）\n"
        "- **最后单元 = 落地应用**：整本书读完，如何在生活中应用（行动清单/可操作建议/读者能带走什么）\n"
        "units 的数量 = 这本书该拆的集数（内容多则多单元、内容少则少单元，5-9 集为宜；最少 4 集=导读+拆解+落地）。"
        "落地单元若内容多（思想觉醒+实操清单都重）→ 可拆成两集（前一集讲'拿回选择权/自我负责'，后一集输出'可执行小步骤+全书总结+预告下一本'）。"
        "输出严格 JSON: {\"units\":[{\"id\":\"u01\",\"role\":\"导读|拆解|落地\",\"title\":str,\"core_claim\":str,"
        "\"concepts\":[str],\"reader_resonance\":[str],\"analogy_hooks\":[str],"
        "\"selling_point\":str,"
        "\"sensitive\":{\"level\":\"green|yellow|red\",\"hits\":[str],\"safe_angle\":str}}]}。"
        "role 标记每单元的三段角色（第1单元必为导读、最后单元必为落地、中间为拆解）。"
        f"【受众场景·必须绑定】目标读者={_aud}。"
        "title/selling_point/analogy_hooks 必须落到这些生活场景，生活化口语，不纯讲理论；"
        "concepts 保留专业词（如交互分析/人生坐标），但 title/卖点/钩子要'人能听懂'。"
        "selling_point=本单元一句话卖点：观众看完能带走什么/看懂什么（挂车钩子），具体可感知。"
        "【禁用词·全平台红线, 出现即规避】医疗承诺: 疗愈/治疗/治愈/根治/心理诊疗（→觉察/梳理/看见）；"
        "绝对化: 最/唯一/顶级/万能/天花板/彻底（→值得/一个不错的选择/带来启发）；"
        "迷信: 好运/辟邪/旺运/招财（讲书不承诺转运）；"
        "金融: 赚钱/暴富/稳赚不赔（→启发财富思维/增加收入可能性）；"
        "引流: 微信/私信我/完整版在xx。全篇用'个人感受+参考建议'的温和表达。"
        "sensitive 标注该单元是否涉及敏感维度（神/宗教/精神/宿命论/争议）及安全讲法。"
    )


# ── 总纲对齐单元重切 (0912): 重跑总纲换了六集骨架, units/hooks 必须联动 ──
def _recut_sys(audience: str | None = None) -> str:
    _aud = audience or _DEFAULT_AUDIENCE
    return (
        "你是拆书创作系统的【总纲对齐单元划分器】。给定总纲各集主题与蒸馏材料, 为**每集**产出一个主题单元。\n"
        "**数量与顺序铁律**: 单元数=集数, 第 i 单元对应第 i 集 (id 由系统覆写, 你只管顺序对齐)。\n"
        "**内容实取铁律**: core_claim=该集主题在书里的真实论点; concepts=蒸馏材料中该主题相关的"
        "真实概念名 (书的原词, 禁编造); reader_resonance/analogy_hooks=从材料的故事/方法/路径中提炼, "
        "绑定受众生活场景; 找不到对应材料的字段宁可短, 禁止编造。\n"
        "role: 第1集=导读(全集大钩子), 中间=拆解, 末集=落地。\n"
        f"【受众场景·必须绑定】目标读者={_aud}。title=口语钩子式单元题, selling_point=一句话带走物。\n"
        "【禁用词·全平台红线, 出现即规避】医疗承诺: 疗愈/治疗/治愈/根治/心理诊疗（→觉察/梳理/看见）；"
        "绝对化: 最/唯一/顶级/万能/天花板/彻底；迷信: 好运/辟邪/旺运/招财；"
        "金融: 赚钱/暴富/稳赚不赔；引流: 微信/私信我/完整版在xx。\n"
        "输出严格 JSON: {\"units\":[{\"id\":\"u01\",\"role\":\"导读|拆解|落地\",\"title\":str,"
        "\"core_claim\":str,\"concepts\":[str],\"reader_resonance\":[str],\"analogy_hooks\":[str],"
        "\"selling_point\":str,\"sensitive\":{\"level\":\"green|yellow|red\",\"hits\":[str],\"safe_angle\":str}}]}"
    )


def recut_units_for_roadmap(book, db=None, audience: str | None = None) -> dict:
    """按总纲重切主题单元 + hooks 联动 + unit_id 回填 (0912 用户令: 总纲重排必须联动单元重切).

    背景: 重跑总纲换了六集骨架, facing-units 还是旧骨架 → 单元级精料 (unit_block/
    单元金句/单元案例) 与总纲脱钩, 逐集生成饿死单元料 (HBO ep1/ep3 实锤: unit_id 全灭)。
    流程: 蒸馏材料 × 总纲 → kimi 每集一单元 (实取) → 备份旧 units → 写新 →
    hooks 按新单元重产 → 各集 roadmap.unit_id 回填。通用机制, 任意书零改动。
    """
    import time as _t

    from .creation_common import _llm, _parse_json
    from .l0 import _l0_dir
    from .series_outline import _material

    # 受众继承链 (0912 教训: 显式参 → 当前 units 指纹 → 历史备份指纹 → 默认。
    # 漏继承会绑错人群 — 老谭线曾误绑静读书宝妈受众; 当前文件可能是无指纹的误切产物,
    # 必须回溯 pre-recut 备份)
    if not audience:
        _fd = _l0_dir(book.book_title) / "facing"
        _cands = [_fd / "facing-units.json"] + sorted(
            _fd.glob("facing-units.pre-recut-*.json"), reverse=True)
        for _c in _cands:
            try:
                audience = json.loads(_c.read_text(encoding="utf-8")).get("_audience")
            except Exception:
                audience = None
            if audience:
                logger.info("[facing] 受众指纹继承自 %s", _c.name)
                break

    material, _gate_hits = _material(book)
    eps = sorted(book.episodes, key=lambda e: e.ep_index)
    if not eps:
        return {"status": "failed", "error": "书无集"}
    rows = [{"ep": e.ep_index,
             "主题": e.title or (e.roadmap_json or {}).get("主题") or "",
             "概念": (e.roadmap_json or {}).get("概念") or [],
             "本集秘籍": (e.roadmap_json or {}).get("本集秘籍") or ""} for e in eps]
    user = (f"【总纲 ({len(rows)} 集, 逐集对齐)】\n{json.dumps(rows, ensure_ascii=False)}\n\n"
            f"【蒸馏材料】\n{material}")
    raw = _llm().chat(_recut_sys(audience), user, model="pro", temperature=0.3)
    data = _parse_json(raw)
    units = (data or {}).get("units") or []
    if len(units) != len(rows):
        return {"status": "rejected", "error": f"单元数 {len(units)} ≠ 集数 {len(rows)}"}

    d = _l0_dir(book.book_title) / "facing"
    d.mkdir(parents=True, exist_ok=True)
    up = d / "facing-units.json"
    if up.exists():
        up.rename(up.with_name(f"facing-units.pre-recut-{_t.strftime('%m%d_%H%M%S')}.json"))
    payload = {"units": units, "_recut_for_roadmap": True}
    if audience:
        payload["_audience"] = audience
    up.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # hooks 联动: 按新单元重产 (episode_gen 的 hook_block 按 unit_id 匹配)
    hooks = _recut_hooks(units, audience)
    if hooks:
        (d / "facing-hooks.json").write_text(
            json.dumps({"hooks": hooks}, ensure_ascii=False, indent=1), encoding="utf-8")

    # unit_id 回填 (u{i} = 第 i 集)
    if db is not None:
        for i, e in enumerate(eps, 1):
            rm = dict(e.roadmap_json or {})
            rm["unit_id"] = f"u{i:02d}"
            e.roadmap_json = rm
        db.commit()
    logger.info("[facing] 总纲对齐重切: %s %d 单元 %d hooks", book.book_title, len(units), len(hooks or []))
    return {"status": "ok", "units": len(units), "hooks": len(hooks or []),
            "units_file": str(up)}


def _recut_hooks(units: list[dict], audience: str | None = None) -> list[dict]:
    """按新单元重产 hooks (镜像 _hooks_sys, 直喂 units — 走 kimi 不启 gemma)。"""
    from .creation_common import _llm, _parse_json

    _aud = audience or _DEFAULT_AUDIENCE
    sys_p = (
        "你是拆书创作系统的【钩子设计器】。基于给定主题单元, 为每单元设计\"开篇钩子+结尾互动+下集预告\"。"
        f"目标读者={_aud}。\n"
        "- opening_hook: 开篇 3 秒钩子 (绑定生活场景戳痛点, 具体不空泛, 剧透别太多)\n"
        "- closing_question: 结尾生活化互动问题 (引导评论, 不空洞求关)\n"
        "- next_teaser: 下集预告 (绑定下一单元主题, 勾追更; 最后单元写系列完结预告)\n"
        "输出严格 JSON: {\"hooks\":[{\"unit_id\":str,\"opening_hook\":str,"
        "\"closing_question\":str,\"next_teaser\":str}]} — hooks 数=单元数, unit_id 逐一对齐。"
    )
    raw = _llm().chat(sys_p, json.dumps(units, ensure_ascii=False), model="pro", temperature=0.4)
    data = _parse_json(raw)
    hs = (data or {}).get("hooks") or []
    by_id = {u.get("id"): u for u in units}
    out = []
    for h in hs:
        uid = h.get("unit_id")
        if uid in by_id and all(h.get(k) for k in ("opening_hook", "closing_question", "next_teaser")):
            out.append(h)
    return out if len(out) == len(units) else []


def _quotes_sys() -> str:    return (
        "你是拆书创作系统的【金句分级器】。把全书 L0 提取的 quotes 逐条分类："
        "格言型=脱离上下文仍成立（可独立上屏/引用）；情境型=依赖情节/前文铺垫才成立"
        "（须带 context，只能借该情节引用，不得单独上屏）。"
        "emphasis=true（排版强调）的金句优先保留。输出严格 JSON: "
        "{\"quotes\":[{\"text\":str,\"type\":\"格言型|情境型\",\"chapter\":int,"
        "\"context\":str,\"usage\":str,\"emphasis\":bool}]}。"
        "情境型必须给 context，无 context 不得列。"
    )


def _cases_sys() -> str:
    return (
        "你是拆书创作系统的【案例蒸馏器】。把全书 L0 提取的 cases 整理，标注："
        "era_sensitive=该案例是否可能因出版年代久远而让现代观众脱节（技术/通讯/社会形态/消费习惯类易脱节；"
        "人性/情感/历史/文学价值类不太脱节；历史/神话类不桥接）。"
        "contemporary_analogy=当代等效类比（LLM 生成，非书中内容，须标注）。"
        "输出严格 JSON: {\"cases\":[{\"id\":\"c01\",\"title\":str,\"chapter\":int,"
        "\"desc\":str,\"scene\":str,\"era_sensitive\":bool,"
        "\"contemporary_analogy\":str,\"usage\":str}]}。"
    )


def _compliance_sys() -> str:
    return (
        "你是短视频平台合规审计器（抖音=关键词+语义上下文双重审核）。基于全书 L0 与【规则集】审计："
        "输出严格 JSON: {\"book_tier\":\"green|yellow|red\",\"hard_blacklist_hits\":[],"
        "\"sensitive_units\":[{\"unit_id\":str,\"hits\":[str],\"risk\":str,\"action\":\"换角度|弱化|避让\"}],"
        "\"safe_angles\":[str],\"abandon\":bool}。"
        "abandon=true 仅当该书写不出安全角度（极罕见）；否则即便有敏感单元也给出 safe_angles（换角度讲，不放弃整本书）。"
    )


def _readers_sys(audience: str | None = None) -> str:
    _aud = audience or _DEFAULT_AUDIENCE
    return (
        "你是拆书创作系统的【读者共鸣提炼器】。基于主题单元，提炼目标读者"
        f"（{_aud}）的痛点/共鸣点，"
        "供评论层与逐集戳痛点用。输出严格 JSON: "
        "{\"readers\":[{\"pain\":str,\"unit_id\":str,\"resonance\":str}]}。"
        "pain=读者真实困惑/情绪痛点；unit_id 关联对应主题单元。"
    )


def _relations_sys() -> str:
    """跨章理解蒸馏 (2026-08-23): 人物图谱 + 概念论证位置 + 作者意图.

    对应 3 个 L0 理解欠缺: 无跨章人物图谱 / 概念无论证位置 / 作者序没用起来.
    输入含【作者序/导读】正文 +【全书人物/实体】(build_facing_input 2026-08-23 补).
    """
    return (
        "你是拆书系统的【全书理解蒸馏器】，擅长把散落在各章的信息聚合成本书的理解层。\n"
        "基于 L0 的序言导读 + 全书章节浓缩 + 人物实体，输出三块 JSON：\n"
        "1. characters 跨章人物图谱（聚合全书所有角色，3-10个）：每个 "
        "{\"name\":名字, \"identity\":一句话身份, \"relation_to_hero\":与主角关系, \"role_in_book\":在全书中的作用}。\n"
        "   - 必须跨章聚合：谁是谁的长辈/朋友/咨询师/对立者，从各章事件归纳，不要只列名字。\n"
        "2. concept_roles 概念论证位置（全书核心概念 4-8 个）：每个 "
        "{\"name\":概念名, \"why_important\":为什么重要, \"position_in_argument\":在全书论证链的位置}。\n"
        "   - 标明哪些是核心框架（如人生坐标），哪些是支撑概念。\n"
        "3. author_intent 作者意图（从序言/导读提炼；若无序言则从全书推断）："
        "{\"core_question\":全书要回答的核心问题, \"framework\":全书论证框架, "
        "\"reader_journey\":读者被引导的认知转变}。\n"
        "输出严格 JSON {\"characters\":[], \"concept_roles\":[], \"author_intent\":{}}，无多余文字。"
    )


FACINGS: dict[str, dict] = {
    "kernel": {"sys": _kernel_sys, "file": "facing-kernel.json"},
    "units": {"sys": _units_sys, "file": "facing-units.json"},
    "quotes": {"sys": _quotes_sys, "file": "facing-quotes.json"},
    "cases": {"sys": _cases_sys, "file": "facing-cases.json"},
    "compliance": {"sys": _compliance_sys, "file": "facing-compliance.json"},
    "readers": {"sys": _readers_sys, "file": "facing-readers.json"},
    "hooks": {"sys": _hooks_sys, "file": "facing-hooks.json"},
    "relations": {"sys": _relations_sys, "file": "facing-relations.json"},
}


# ── 输入构建: L0 结构化 → facing 输入 (非原文, 控制 token) ────────
def build_facing_input(l0: dict, cap_per_chapter: int = 2) -> str:
    """全书 L0 → 结构化浓缩 (每章 summary + 前 N 概念/金句 + 聚合 references)."""
    parts = [f"【书名】{l0.get('book','')}"]
    m = l0.get("meta") or {}
    for k in ("author", "publisher", "pub_date"):
        if m.get(k):
            parts.append(f"【{k}】{m[k]}")
    fm = l0.get("frontmatter") or []
    if fm:
        # 2026-08-23: 作者序/导读正文进输入 — 此前只给名称, 作者序理解完全没用上
        parts.append("【作者序/导读】" + " || ".join(
            f"{str(f.get('name', ''))}：{str(f.get('text', ''))[:400]}" for f in fm))
    # 2026-08-23: 跨章实体聚合 (人物图谱/角色理解的原料)
    all_ents: list[str] = []
    for ch in l0.get("chapters", []):
        for e in (ch.get("entities") or []):
            s = str(e).strip()
            if s and s not in all_ents:
                all_ents.append(s)
    if all_ents:
        parts.append("【全书人物/实体】" + "、".join(all_ents))
    # 章节浓缩
    ch_parts = []
    for ch in l0.get("chapters", []):
        line = f"- 第{ch.get('idx','?')}章 {ch.get('title','')}：{ch.get('summary','')[:80]}"
        conc = ch.get("concepts") or []
        for c in conc[:cap_per_chapter]:
            line += f" 概念[{c.get('name','')}:{str(c.get('mech',''))[:50]}]"
        qs = ch.get("quotes") or []
        for q in qs[:cap_per_chapter]:
            line += f" 金句[{str(q.get('text',''))[:30]}]"
        ch_parts.append(line)
    parts.append("【章节】\n" + "\n".join(ch_parts))
    # references 聚合
    refs = []
    for ch in l0.get("chapters", []):
        for r in ch.get("references") or []:
            refs.append(f"{r.get('type','')}:{r.get('name','')}")
    if refs:
        parts.append("【书内引用源头】" + "、".join(refs[:20]))
    return "\n".join(parts)


# ── 各 facing 执行 ───────────────────────────────────────────────
# 受众敏感面 (0907 双人物): 选题角度/钩子/读者痛点由账号受众决定 —
# 静读书(女性成长)与老谭读书(男性认知/职场升值)对同一本书拆出的角度完全不同。
_AUDIENCE_SENSITIVE = ("units", "hooks", "readers")


def _run_one(facing: str, l0: dict, client, spec: dict, audience: str | None = None) -> dict:
    user = build_facing_input(l0)
    if facing in _AUDIENCE_SENSITIVE and audience:
        sys_p = spec["sys"](audience=audience)
    else:
        sys_p = spec["sys"]()
    if facing == "units":
        # 2026-08-22: 源头注入全系统统一限流词 (config/compliance_common.json), LLM 生成时规避
        from app.services.compliance import build_redline_prompt
        sys_p += "\n" + build_redline_prompt()
    if facing == "hooks":
        # hooks 关联主题单元 (unit_id/title/卖点 → 开篇钩子/结尾互动/下集预告)
        try:
            u_path = _l0_dir(str(l0.get("book", ""))) / "facing" / "facing-units.json"
            u = json.loads(u_path.read_text(encoding="utf-8"))
            lines = [f"- {x.get('id')}《{x.get('title')}》: {str(x.get('selling_point', ''))[:50]}"
                     for x in u.get("units", [])]
            if lines:
                user += "\n\n【主题单元】\n" + "\n".join(lines)
        except Exception:
            pass
    if facing == "compliance":
        try:
            from .distiller import load_book_rules
            rules = load_book_rules()
            user += f"\n\n【规则集】{json.dumps(rules, ensure_ascii=False)[:3000]}"
        except Exception:
            pass
    data = None
    for attempt in range(2):  # 2026-08-23: 空 JSON 重试一次, 防核心主张等静默空
        try:
            raw = client.chat(user, system=sys_p)
        except Exception as exc:
            logger.warning("[facing] %s 异常: %s", facing, exc)
            return {"error": str(exc)}
        data = client.parse_json_block(raw)
        if data:
            break
        logger.warning("[facing] %s 空 JSON (第%d次), 重试…", facing, attempt + 1)
    if not data:
        logger.warning("[facing] %s 空 JSON (已重试)", facing)
        return {"error": "empty"}
    return data


def run_facings(l0_dir_or_book: str | Path, client=None,
                facings: list[str] | None = None, model="gemma",
                audience: str | None = None) -> dict:
    """第二层 facing 蒸馏.

    l0_dir_or_book: data/l0/{book}/ 目录 或 书路径(自动跑 L0 后). facings=None 跑全部.
    model: gemma(本地) / deepseek(可选, 创作级).
    """
    from .distiller import GemmaClient, ensure_gemma

    p = Path(l0_dir_or_book)
    l0_path = p if p.name == "l0-chapter-v1.json" else (p / "l0-chapter-v1.json")
    if not l0_path.exists():
        raise FileNotFoundError(f"L0 不存在: {l0_path} (先跑 python -m app.services.book_service.l0 <书>)")
    l0 = json.loads(l0_path.read_text(encoding="utf-8"))
    book_dir = l0_path.parent
    facing_dir = book_dir / "facing"
    facing_dir.mkdir(parents=True, exist_ok=True)

    todo = facings or list(FACINGS)
    client, _started = ensure_gemma(client or GemmaClient())

    results = {}
    for name in todo:
        spec = FACINGS.get(name)
        if not spec:
            logger.warning("[facing] 未知面向: %s", name)
            continue
        data = _run_one(name, l0, client, spec, audience=audience)
        # 0907 双人物: 受众敏感面记受众指纹 — 建书时 persona 受众与产物指纹
        # 不一致 → 重跑这三面 (选题角度随账号, 不是书的固有属性)
        if name in _AUDIENCE_SENSITIVE and audience:
            data["_audience"] = audience
        results[name] = data
        (facing_dir / spec["file"]).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("[facing] %s → %s", name, spec["file"])

    return {"book": l0.get("book"), "dir": str(facing_dir),
            "facings": {k: ("ok" if not v.get("error") else f"err:{v['error']}")
                        for k, v in results.items()}}


def facing_audience(l0_dir_or_book: str | Path) -> str | None:
    """读 units 面记录的受众指纹 (无指纹 = 旧产物, 视为默认静读书受众)."""
    try:
        p = Path(l0_dir_or_book)
        d = p if p.is_dir() else p.parent
        u = json.loads((d / "facing" / "facing-units.json").read_text(encoding="utf-8"))
        return u.get("_audience")
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    from app.config import load_config, set_config

    set_config(load_config())
    if len(sys.argv) < 2:
        print("用法: python -m app.services.book_service.facing <L0目录或书路径> [--facing kernel,units]")
        sys.exit(1)
    facings = None
    if "--facing" in sys.argv:
        facings = sys.argv[sys.argv.index("--facing") + 1].split(",")
    r = run_facings(sys.argv[1], facings=facings)
    print(json.dumps(r, ensure_ascii=False, indent=1))
