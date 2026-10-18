# -*- coding: utf-8 -*-
"""拆书步骤 5 — 逐集生成 (创作层 + 编辑层两层契约) + 确认 + 级联重跑.

拆包自 orchestrator.py , 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import BookProject, Episode
from app.models.base import _now
from app.services.book_service import passes as passes_mod
from app.services.book_service.creation_common import (
    PLATFORM_DURATION_BAND,
    SPEECH_RATE,
    _llm,
    _parse_json,
    _scan_occult_quotes,
    _zh_brand_issues,
    derive_breakdown_steps,
    elastic_labels,
    ep1_opening_order_issues,
    fallback_base,
    fandeng_opening_issues,
    hook_position_issues,
    persona_anchor_issues,
    persona_identity_ending,
    section_flow_issues,
    source_context,
    typing_card_issues,
    validate_script,
    word_band,
)
from app.services.book_service.creation_steps import _l0_facing
from app.services.book_service.distiller import load_book_rules
from app.services.llm_service import _load_prompt_template
from app.services.prompt_guard import TRUST_DECL, wrap_source

logger = logging.getLogger(__name__)

__all__ = ["generate_episode", "confirm_episode", "rerun_cascade"]

# 2026-08-23: 编辑层口语化 — 生成层专注创作, 编辑层专职改口语 (身份激活真人口播语感).
# 两层契约: 创作层用 ⟦⟧ 标记原书原话 → 编辑层见标记一字不改 → 输出清标记.
_QUOTE_MARK_RE = re.compile(r"⟦(.*?)⟧")



def _relations_block(book: BookProject) -> str:
    """facing-relations 注入 : 跨章人物图谱 + 概念论证位置 + 作者意图.

    解决 L0 理解欠缺: 獾是谁/核心框架/全书意图 — 逐集生成不再靠猜.
    """
    try:
        from app.services.book_service.l0 import _l0_dir
        p = _l0_dir(book.book_title) / "facing" / "facing-relations.json"
        if not p.exists():
            return ""
        r = json.loads(p.read_text(encoding="utf-8"))
        lines: list[str] = []
        chs = r.get("characters") or []
        if chs:
            lines.append("【本书人物关系 · 首次出现须用此身份介绍】")
            for c in chs:
                lines.append(f"- {c.get('name', '')}：{c.get('identity', '')}（{c.get('relation_to_hero', '')}）。"
                             f"{str(c.get('role_in_book', ''))[:50]}")
        cr = r.get("concept_roles") or []
        if cr:
            lines.append("【核心概念 · 论证位置】")
            for c in cr:
                lines.append(f"- {c.get('name', '')}（{c.get('position_in_argument', '')}）："
                             f"{str(c.get('why_important', ''))[:50]}")
        ai = r.get("author_intent") or {}
        if ai:
            lines.append(f"【作者意图】核心问题：{ai.get('core_question', '')}；"
                         f"框架：{str(ai.get('framework', ''))[:70]}；读者转变：{str(ai.get('reader_journey', ''))[:50]}")
        return "\n" + "\n".join(lines) + "\n" if lines else ""
    except Exception:
        return ""


def _douban_block(book: BookProject) -> str:
    """豆瓣真实读者反应注入 .

    ⚠️ 用户验收发现参与生成后"不对味儿" — 豆瓣读者语言风格渗入稿子, 冲淡书本身的
    讲述语感. 数据保留 input_json.douban (爬虫/落库/前端展示不回滚), 仅停止注入逐集生成;
    待重新设计使用方式 (如仅槽点规避/人工参考) 后再启用: 删掉下方 early-return 即恢复.
    """
    return ""
    d = (book.input_json or {}).get("douban") or {}
    if not d:
        return ""
    lines: list[str] = ["【豆瓣真实读者反应 · 共鸣/槽点/深度】"]
    for c in (d.get("comments") or [])[:6]:
        lines.append(f"- [短评{c.get('votes', 0)}赞] {str(c.get('text', ''))[:60]}")
    for v in (d.get("reviews") or [])[:2]:
        lines.append(f"- [书评·{v.get('votes', 0)}有用]《{v.get('title', '')}》{str(v.get('summary', ''))[:80]}")
    lines.append("读者槽点(康复太快/对读过的太浅/结局现实)做稿时规避或回应, 不硬拗。")
    return "\n" + "\n".join(lines) + "\n"


def _rm_hint(ep) -> str:
    """roadmap 秘籍+钩子+数据钉摘要 (喂樊登讲故事)."""
    rm = ep.roadmap_json or {}
    parts = [str(rm.get("本集秘籍") or "")]
    if rm.get("黄金三秒钩子"):
        parts.append(f'钩子方向: {rm["黄金三秒钩子"]}')
    if rm.get("数据锚点"):
        parts.append(f'数据: {rm["数据锚点"]}')
    if rm.get("贯穿人物"):
        parts.append(f'人物线: {rm["贯穿人物"]}')
    return "; ".join(x for x in parts if x)


def _strip_for_series_feed(t: str) -> str:
    """ep1 大钩子喂 2-5 稿前清洗 (0912 用户令: 标签行是废话且带偏 ep1):
    ① 剥「回顾+引入」整节 — 该节 recap 的是 ep1 自己, 循环喂入;
    ② 剥「总结+下期预告」整节 (恒为末段) — 下期预告=单集转场件会带偏系列级导览,
       总结=拆解段复述, 尾部还各带一份 CTA (喂4份=CTA撞衫源);
    ③ 剥六段弹性标签行 (【0-11秒｜钩子】等) — 纯结构噪声, 挤占 1400 字预算,
       且 2-5 的时段划分与 ep1 自己的标签表冲突, 诱发格式模仿;
    ④ 防御剥尾部 coverage JSON 块。只动喂入文本, 不碰库内原稿。"""
    t = re.sub(r"【[^】]*(?:回顾|总结)[^】]*】.*?(?=【[^】]*】|\Z)", "", t or "", flags=re.S)
    t = re.sub(r"^[ \t]*【[^】]*】[ \t]*$", "", t, flags=re.M)
    t = re.sub(r'\{"coverage"\s*:.*\}\s*$', "", t, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# 0927 案例口径三则 (用户×豆包合议定稿, 用户令"需要成为一种规则"):
# ①不评企业好坏: 只描述管理动作, 收口句式="这个动作印证了书中观点";
# ②信源限定词: 引入大厂案例的句子必带 公开报道/当年对外披露/早期 之一;
# ③选题避雷: 禁近两年热点争议事件 (2024+年份与品牌同句即打回), 选已沉淀公知商业史。
# 词典补字节/腾讯/阿里系: 旧词典无这些名, 字节案例进稿连在场闸都认不出 (0927实测)。
def generate_episode(db: Session, ep: Episode) -> Episode:
    book = ep.book
    inp = book.input_json or {}
    # 1018 C4a 北斗星第5条: 目标时长平台带闸 (270,420)s — 越带不代改 (target 是内容决策, 归人调)
    if not PLATFORM_DURATION_BAND[0] < float(ep.target_duration_sec or 0) < PLATFORM_DURATION_BAND[1]:
        raise ValueError(
            f"目标时长 {float(ep.target_duration_sec or 0):.0f}s 越北斗星平台带 "
            f"({PLATFORM_DURATION_BAND[0]:.0f}-{PLATFORM_DURATION_BAND[1]:.0f}s, 单条视频须 >4:30 且 <7:00), "
            "请先调整本集 target_duration_sec 再生成")
    labels = elastic_labels(ep.target_duration_sec)
    label_table = "\n".join(f"【{a}-{b}秒｜{n}】" for a, b, n in labels)
    char_hints = "、".join(f"【{n}】约{int((b - a) * SPEECH_RATE)}字" for a, b, n in labels)

    prev = next((e for e in book.episodes if e.ep_index == ep.ep_index - 1), None)
    coverages = [e.coverage_json or [] for e in book.episodes if e.ep_index < ep.ep_index]
    # ── 基底模板按书的 persona 分流 (2026-09-07 双人物): 老谭读书 / 静读书 ──
    _persona_tpl = "jingshu-book"
    try:
        _pid = inp.get("persona_id")
        if _pid:
            from app.models import Persona as _Persona
            _p = db.query(_Persona).filter(_Persona.id == _pid).first()
            if _p and _p.prompt_template:
                _persona_tpl = _p.prompt_template
    except Exception:
        pass
    base = _load_prompt_template(_persona_tpl)
    if not base or len(base) < 200:
        logger.warning("[book] config/%s.txt 缺失或过短, 回退内置兜底基底", _persona_tpl)
        base = fallback_base(_persona_tpl)
    _is_laotan = "laotan-book" in _persona_tpl
    # 附加指令的人设差异化片段 (静=女性成长场景 / 老谭=职场认知场景)
    if _is_laotan:
        _hint_plain = ("7. 专业概念口播必须翻译成大白话，用职场晋升/带团队/跳槽决策/饭局"
                       "的生活例子解释（“你说得好”不如“你一听就懂”），不裸甩专业术语。\n")
        _hint_hook = ("8. 钩子(ep2-6)=断言+锤式, 严格按模板【钩子铁律·ep2-6】执行; ep1 不用此式 (按开局铁律八拍); "
                      "禁渗后续集核心概念; "
                      "结尾钩子问题绑定本集视角的真实困境, 自然引导留言或追下集。"
                      "(书为主体/金句独立成行/设问锚点节奏 模板已有铁律, 不在此复述)\n")
        _self_ban = "'我是XX/我是老谭'自称"
        # 受众两层 (0914 用户令: L1 书定主线全篇不换 + 闲话层按轴搭桥):
        # L1 = 目标读者画像 (书定); 闲话层 = roadmap 分配 (轴+组, 池在受众地图)
        _l1 = ((inp.get("目标读者画像") or {}).get("value")
               if (inp.get("目标读者画像") or {}).get("tier") != "persona" else None) \
            or "见【受众地图】L1 (缺料时: 按本集秘籍的处境锁定一人, 全篇只对他)"
        _am = (inp.get("受众地图") or {}).get("value") if isinstance(inp.get("受众地图"), dict) else None
        # 书级固定打字卡 (用户令: 全系列每集 0-2s 同一句, 总纲层冻结; 无卡时 ep1 自产并回填)
        _card = str(((inp.get("系列打字卡") or {}).get("card")) or "").strip()
        # 集型 (0914 结构参数化: 集数由书定 = 工具数K+2; 集型按角色不按固定集号)
        _total_eps = len(book.episodes) or 6
        _is_finale = _total_eps > 1 and ep.ep_index >= _total_eps      # 行动册集 (末集)
        _is_tool_ep = 1 < ep.ep_index < _total_eps                     # 工具集 (一集一工具)
        # 樊登开局逐字段 (用户想法: 樊登稿一遍成型质量高, ep1 前三段一字不改,
        # 大钩子从稿里提 — 生成只做缝合与 L1 代入, 传话游戏降质从根上免掉)
        _fd_open_paras: list[str] = []
        if ep.ep_index == 1:
            try:
                from .fandeng_full import _paragraphs, load_fandeng_full
                _fd_open_paras = [p.strip() for p in
                                  _paragraphs(load_fandeng_full(book.book_title) or "")[:3]
                                  if p.strip()]
            except Exception:
                _fd_open_paras = []
        _tool_n = max(_total_eps - 2, 0)                               # 工具件数 K
        if _is_tool_ep:
            # 0926 硬核层: 原书锚段入标签表 — 只改宪法文本 LLM 不执行实锤 (ep2 复跑
            # 零原书锚验证), 结构注入必须与宪法同向; 从拆解三前段切 15-25s
            _i3 = next((i for i, (_, _, n) in enumerate(labels) if "拆解（三）" in n), -1)
            if _i3 >= 0:
                _a3, _b3, _n3 = labels[_i3]
                _span = max(15, min(25, int((_b3 - _a3) * 0.4)))
                labels[_i3] = (_a3 + _span, _b3, _n3)
                labels.insert(_i3, (_a3, _a3 + _span, "书里怎么说的"))
                label_table = "\n".join(f"【{a}-{b}秒｜{n}】" for a, b, n in labels)
                char_hints = "、".join(f"【{n}】约{int((b - a) * SPEECH_RATE)}字" for a, b, n in labels)
        _CN = {2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
        _cn = lambda n: _CN.get(n, str(n))
        # 秘籍形态适配 (0908 用户令: 集分开生成, 提示词应随集差异化)
        _rm = ep.roadmap_json or {}
        _chatter_ep = _is_tool_ep or ep.ep_index == 1    # 闲话=工具集+大钩子集 (末集纯干货豁免)
        _chat_rows = (_rm.get("闲话层") or []) if _chatter_ep else []
        _chat_txt = "；".join(
            f"{c.get('轴')}·{c.get('group')}"
            + (f"(桥:{c.get('bridge')})" if c.get("bridge") else "")
            for c in _chat_rows if isinstance(c, dict) and c.get("group")) or None
        _pool_txt = ""
        if _am and _chatter_ep:
            _pool_txt = (f"【受众地图池】上下游: "
                         + " / ".join(f"{p.get('group')}(桥:{p.get('bridge')})" for p in (_am.get("上下游") or []))
                         + "；上下层: "
                         + " / ".join(f"{p.get('group')}(桥:{p.get('bridge')})" for p in (_am.get("上下层") or []))
                         + "\n")
        _hint_views = (
            f"受众两层铁律: 主线'你'全篇只对 L1 说 —【本集主受众】{_l1}; "
            "钩子认领只认领 L1 一个 (禁多类人排比); 论证/工具/诊断问全归 L1。\n"
            + (_pool_txt if _pool_txt else "")
            + ((f"本集闲话层 (总纲分配, 迁移落地/共鸣处点名, 每组必带桥句): {_chat_txt} — "
                "点名即在对应块真落地一次 (点名+具体场景+桥); 桥=机制同构, 禁鸡汤桥。\n"
                if _chat_txt else
                "本集闲话层未分配 (旧总纲): 迁移落地处沿两轴各搭一桥 (上下游一处/上下层一处), "
                "必带桥句, 禁凭空多点人群。\n") if _chatter_ep else ""))
        _secret = str(_rm.get("本集秘籍") or "")
        # 0909 四模型评审新列: 钩子/赌注/数据钉 + 行动册预告 (每集注入)
        _hook3 = str(_rm.get("黄金三秒钩子") or "")
        _stake = str(_rm.get("赌注层级") or "")
        _datanail = str(_rm.get("数据锚点") or "")
        # 构件调度 (0910 受控多样性): 总纲分配四维构件, 生成器只执行禁自选
        _comp = str(_rm.get("构件") or "")
        _hook_extra = ""
        if _card:
            _hook_extra += f"首行固定输出「打字卡：{_card}」(书级固定卡, 全系列同一句, 一字不改)。\n"
        if _comp:
            _hook_extra += f"本集构件 (按此执行, 禁换其他): {_comp} — 开场/论证序/金句形态/结尾方式四词对应执行, 与前集不同型是设计意图。\n"
        # 拆解三步 (0913b 治段落割裂): 总纲分配三块内容边界, 生成器只执行禁自选;
        # 仅工具集注入 — 大钩子集 (开局八拍+书三件套+导览) 与行动册集 (纯干货装订)
        # 结构另由开局/收官铁律定; 旧总纲无此列时从秘籍 ①②③ 标记确定性兜底切三步
        _steps = _rm.get("拆解三步") if _is_tool_ep else None
        if not (isinstance(_steps, list) and len(_steps) == 3
                and all(str(s).strip() for s in _steps)):
            _steps = derive_breakdown_steps(_secret)
        if isinstance(_steps, list) and len(_steps) == 3:
            _hook_extra += ("本集拆解三步 (总纲分配, 拆解一/二/三各走一步, 禁自选禁换序): "
                            f"①{str(_steps[0])} / ②{str(_steps[1])} / ③{str(_steps[2])} — "
                            "每块第一句=步骤路标句 (点出第几步/档, 接住上块尾, 半句回指可用); "
                            "本步的钉/机制/行动只在本步块讲, 禁第一块把三步总览完, 禁回炒已走完的步。\n")
        # 0930 跨集案例去重 (蒙牛撞 ep3 关联定位实锤 — 幽默 pass 换案带进来的):
        # 案例库名单 ∩ 前集稿文本 → 禁用清单注入, 生成层防偷前集正主案例
        _used_cases = []
        try:
            from app.services.book_service.l0 import _l0_dir as _l0d2
            _cbp = _l0d2(book.book_title).parent / "案例库.json"
            if _cbp.exists():
                _names = [str(c.get("name") or "") for c in
                          (json.loads(_cbp.read_text(encoding="utf-8")).get("cases") or [])]
                _prev_text = "".join(e2.script_text or "" for e2 in book.episodes
                                     if e2.ep_index < ep.ep_index)
                _used_cases = [n for n in _names if n and n in _prev_text]
        except Exception:  # noqa: BLE001
            _used_cases = []
        if _used_cases:
            _hook_extra += ("案例跨集去重: 前集已用案例 " + "、".join(_used_cases)
                            + " 禁原案重讲 (要提只许换角度半句带过, 篇幅留给本集新案例)。\n")
        if _hook3:
            _hook_extra += f"黄金三秒钩子(开头3秒必用): {_hook3}\n"
        if _stake:
            _hook_extra += f"本集赌注层级(开场结尾按此写): {_stake}\n"
        if _datanail:
            _hook_extra += f"本集数据钉(正文中真实引用): {_datanail}\n"
        # 行动册预告 (0914 动态): 步=工具序号 (工具集 ep2=第一步), 册=末集 — 集数随书
        _booklet_line = (
            f"'这是{_cn(_tool_n)}步转型的第{_cn(ep.ep_index - 1)}步, "
            f"完整行动册在第{_total_eps}集等你'(第{_total_eps}集兑现交棒)") if _is_tool_ep else (
            f"'这{_total_eps}集一集一件工具, 完整行动册在第{_total_eps}集装订'")
        _hook_extra += ("结尾行动册预告: " + _booklet_line + "; "
                        "回顾段拼图位**前埋**'五步转型'概念 — 用'五步转型, 这是第X件工具'式 "
                        "(校验只认'X步转型的第X步'原句, 此式不撞); 总结段才用原句 — "
                        "0930 点评: 250s 才首次出现'五步转型'观众会疑惑; "
                        "互动问=评论钩站队优先 (给可对线立场, 最好是本集要反驳的那个; "
                        "进阶=两派对立式'很多人说X/还有人说Y/你站哪边', 两派须真实存在禁稻草人; "
                        "或低门槛晒经历'报个名'式; 总结段只放一个问主推站队钩, "
                        "禁自曝短处式'贴出来让大家帮你判断'✗, "
                        "禁与回顾段诊断问同款复读, 禁'你怎么看'); "
                        "下集预告=悬念问句(禁结论句/禁说破后续集概念); "
                        "转发/收藏CTA=收尾段末唯一一处且每集现造: 对象/理由从本集秘籍出发"
                        "(集与集必须不同句, 系列连播同句=观众疲劳), 收藏给场景理由, "
                        "句式按本集话题轮换; "
                        "禁渗后续集核心概念(后面集要展开的答案/隐喻只留悬念)。\n")
        _figure = str(_rm.get("贯穿人物") or "")
        if _figure:
            _hook_extra += ("贯穿人物出场配额·全稿≤3处, 各有职能不重复 "
                            "(0930 用户实锤'反复讲太啰嗦' — 人物被四个规则拽进四个功能块): "
                            "①钩子=反差结论两三句 (名字+核心事实+反差, 禁延伸禁总结腔); "
                            "②中段(拆解一或二)=完整故事**仅此一次** (困境/选择/代价); "
                            "③拆解三若用人物=只许**拿工具量化** (拿本集口诀/问话量她: 占的维度/"
                            "刻度/判定结果三行式, 禁重述故事事实); "
                            "④总结=身份重述一句 ('他不是换工作, 是换了一个赛道'式, "
                            "按本集人物现造 — 全片情感高潮在这一句) 禁跟事实复述; "
                            "本集人物料: " + _figure + "\n")
        _hook_extra += ("判定刻度铁律: 秘籍判定必须给可操作刻度(翻账本/比例线/'五年前没这词'), "
                        "禁'主要来自/很多'模糊词 — 观众拿账就能圈出自己。\n")
        _hook_extra += ("**真副总裁语感铁律 **: 每句洞察必须带'我什么场景看到的'痕迹 — "
                        "禁平空断言式句型('XX年前就有人看透'/'做X的人都知道'/'这是基本逻辑' — "
                        "没身份锚, 谁都能说); 场景按本集话题现造 (讲钱→审合同/算账; 讲人→招聘/留人; "
                        "讲取舍→砍项目), 禁套别的话题场景 — 格局=场景记忆, 不是总结陈词。\n")
        if ep.ep_index == 1:
            # 后续集核心概念清单 (动态取自各集 roadmap, 通用机制禁手填个案)
            _next_concepts = sorted({str(c) for e2 in book.episodes
                                     if e2.ep_index > ep.ep_index
                                     for c in ((e2.roadmap_json or {}).get("概念") or [])})[:8]
            _nc_tip = f" (禁渗清单: {'、'.join(_next_concepts)})" if _next_concepts else ""
            if _fd_open_paras:
                # 拼装式开局 (樊登开局逐字段在): 骨架逐字, 生成只做缝合与 L1 代入
                _card_open = (f"首行固定输出「打字卡：{_card}」(书级固定卡一字不改) → " if _card else
                              "首行单独输出「打字卡：…」定场卡 (无口播) — 对'你'的收益承诺三承重 "
                              "(86万赞真锚式: 如果你能坚持[每晚睡前式低成本行为], [时间窗]后, 你的[收益主体]"
                              "将超过[90%式量化]的人; 零上下文铁律: 每个词冷启动秒懂, 收益翻成大白话(财商式), "
                              "禁系列内部词汇(九集/工具/判定/墙/行动册); 书主角反差不进卡) → ")
                # 1018 C3 (R2, 已拍板六拍③去作者): fd 锁段第1段含作者介绍时, ③拍
                # 动态去作者 — 旧文案 ③+锁段1 各介绍一遍作者 = 双开篇架构性复读
                # (ep1 实锤: 车轱辘话打回但去重pass删不动锁段)。
                _au_toks = [t for t in re.split(r"[（(·\s]", str(book.author or ""))
                            if len(t) >= 2] or []
                _fd1_has_author = bool(_fd_open_paras) and any(
                    tok in (_fd_open_paras[0] or "") for tok in _au_toks)
                _beat3 = ("③硬核宣言(只报书不报作者 — 作者由逐字段第1段引入, 禁重复介绍) → "
                          if _fd1_has_author else "③硬核宣言+作者(啃什么书为什么硬) → ")
                _hint_ep_type = ("ep1 大钩子集 · 拼装式开局 (老谭第一人称经历零出场 — 禁'我踩过/我当年/我管过'式, "
                                 "场景血肉只用书里事实和观众处境): "
                                 + _card_open
                                 + "**权威梯六拍** (86万赞真锚开场模式, ~30s): ②报书名(一句, 禁讲内容) → "
                                 + _beat3
                                 + "④地位拍×2(纪录/反差各一句, 蒸馏料实供) → "
                                 "⑤'说实话'式自谦反转(降期待立信任) → ⑥价值承诺(最精华+明天就能用上+'全揉进这次拆书里', "
                                 "禁报集数/系列长度 — 30s不给压力, 集数留到导览拍) → "
                                 "⑦受众认领+收益(L1 一句点名+听完会怎样) → "
                                 "**六拍完才进钩子**: 【开局逐字段】第1段**逐字**接上 (故事钩子, 痛点悬念由它扛); "
                                 "缺口/共情预判压成一两句缝织在六拍与第1段之间, 禁整拍展开 (认领拍已代入, 禁重复); "
                                 "缝里或导览总起前**沿两轴各搭一桥**点名闲话人群 (带桥句, '你不做这行也一样…'式 — "
                                 "ep1 是系列受众最宽的一集, 闲话池不用=白炼) → "
                                 "拆解区 = 第2-3段**逐字**(元问题深挖)+一口最反常识预演(轻悬念) → "
                                 "系列导览(钩子清单式: 每行=动词+工具+半句为什么要听, 总起用【系列收益承诺】收益链) → 收尾承诺+下集预告。"
                                 "**逐字段一字不改 (⟦⟧原话保护): 禁增删改字/换词/压缩/拆段重排; "
                                 "**数字形态也是字** — 原文『365户/90%/1928年』就写阿拉伯数字, "
                                 "禁改『三百六十五户/百分之九十/一九二八年』(TTS 自己会念, 稿面必须原样); "
                                 "段落标签行只许插在句号处, 不算改动**; 禁教任何工具; "
                                 f"**禁渗后续集核心概念**{_nc_tip}; "
                                 f"导览=总起【系列收益承诺】的收益链一句+每个工具集一行钩子 "
                                 f"(动词+工具名+半句为什么要听, 禁目录腔裸报名; 本系列共{_total_eps}集: "
                                 f"第2~{_total_eps-1}集一集一件工具, 第{_total_eps}集行动册装订)。\n" + _hook_extra)
            else:
                _card_open8 = (f"首行固定输出「打字卡：{_card}」(书级固定卡一字不改); " if _card else
                               "首行单独输出「打字卡：…」定场卡 (无口播; 对'你'的收益承诺三承重, 痛点落在观众本人); ")
                _hint_ep_type = (f"ep1 大钩子集 · 收益承诺开局 (按模板【开局铁律·ep1】权威梯; "
                                 f"老谭第一人称经历零出场, 禁'我踩过/我当年/我管过'式): "
                                 + _card_open8
                                 + "~6s开口=硬核宣言(啃什么书为什么硬, 禁断言锤禁问候); ~19s代入日常; "
                             "~30s痛点悬念(悬而不答); ~42s缺口(没人教过); ~51s共情预判(替观众说出想划走的念头); "
                             "**书三件套硬拍各≥2句: ⑦书地位(版本/纪录/反差) ⑧作者(谁+凭什么资格写, 禁虚构) "
                             "⑨元问题(这本书回答的那个问题, 用【本书元问题】料) — 缺一即违规, "
                             f"这是观众决定跟不跟这{_total_eps}集系列的信任地基**; "
                             "**拆解区=元问题深挖+一口最反常识预演 (禁教任何工具 — 工具是工具集的事)**; "
                             "前95s句均≤18字、每拍一新信息、大转折卡3/30/60s检查点; "
                             "引入段价值锚句(≥2具体物真实落差: 年份/数字/代名作)自然带书; "
                             f"**禁渗后续集核心概念**{_nc_tip} — 后面集要展开的答案/论证只能留悬念; "
                             "但系列导览拍(⑩)的工具**点名**不在此列: 点名≠剧透, 展开论证才是剧透。"
                             f"导览=钩子清单式: 总起【系列收益承诺】收益链+每个工具集一行钩子(动词+工具名+半句为什么要听, 禁目录腔) "
                             f"(本系列共{_total_eps}集: 第2~{_total_eps-1}集一集一件工具, 第{_total_eps}集行动册装订)。\n"
                             + _hook_extra)
        elif _is_finale:
            # 0914 行动册集; 1005 密度爆炸令 (用户: 绝对的干, 绝对的信息密度爆炸):
            # K件工具逐件一页精炼, 每页三件套 (与宪法【行动册集】块同向 — 双端锚定)
            _secrets_all = []
            for e in sorted(book.episodes, key=lambda x: x.ep_index):
                if not (1 < e.ep_index < _total_eps):   # 只收工具集的秘籍 (头尾不占工具位)
                    continue
                sc = str((e.roadmap_json or {}).get("本集秘籍") or "")
                if sc:
                    _secrets_all.append(f"第{_cn(e.ep_index - 1)}件: {sc}")
            _secrets_block = "\n".join(_secrets_all)
            _hint_ep_type = ((f"首行固定输出「打字卡：{_card}」(书级固定卡一字不改)。\n" if _card else "")
                             + f"行动册集 (末集) · 密度爆炸 (绝对的干): 下面{_cn(_tool_n)}件工具"
                             "逐件一页精炼, **每页三件套缺一即废稿**: ①怎么填(输入项+步骤, "
                             "可执行指令式) ②过线判据(数字化刻度, 观众拿自己数据能对号) "
                             "③误用警告(填错的样子一句)。工具'是什么'全页限一句 — "
                             "名词解释不是信息, 刻度和动作才是; 每页主案例=中国企业/当下可感, "
                             "书证钉只当一句注脚(与干度冲突时删书证)。"
                             "字数地板: 成品 1250-1380 字 (只到三件套骨架没到地板 = 未装订完); "
                             "每件工具页三件套之外必须带 ≥1 个具体案例或数字锚 — "
                             "**优先复用前集已建立的人物案例** (本系列教过的人物观众已认识, "
                             "收官集复用零认知成本; 案例库中国企业/书中故事次之); "
                             "全 shallow (五件各三行裸骨架) = 废稿。"
                             f"{_cn(_tool_n)}件×(三件套+案例)若超字数预算 → 宁深勿全: "
                             "砍到3件深讲(每件≥350字)+其余一页索引(指路合集), 禁名词目录, "
                             "也禁 5 件全浅的中间路线。"
                             "零空转: 承接/仪式感压最少, 每句自检'删掉这句观众少知道什么?'"
                             "答不上来=删句 — 观众以后翻这集是来抄作业的, 不是背工具名。"
                             "交棒句/集数计数/转发CTA 由系统结尾统一注入, 稿内禁写"
                             "(禁'这套系统…是你的'式交棒/禁'七集/五件'计数收口/禁'转给…'CTA); "
                             "收官互动=观众自发晒全系列微行动作业(非提问, 稿内禁平台词邀请); 系列完结语禁下集预告。"
                             f"\n【{_cn(_tool_n)}件工具全文(逐件精炼成页)】\n{_secrets_block}\n")
        elif _is_tool_ep:
            _hint_ep_type = (f"工具集 (本集=第{_cn(ep.ep_index - 1)}/{_cn(_tool_n)}件, 一集完整交付一件工具): "
                             "回顾段三合一+评论钩: 开场=**回收上集尾部留下的悬念问句** "
                             "(【上集尾部】料里的问句, 转成'上集我们问:…'式, 禁弃问句改讲概念) "
                             "→ 一句给答案方向 → 本集拼图位+诊断问; "
                             "**段末=痒点钩 (1018 v3 — 1:30 法则: 用户互动决策全在前90s完成; "
                             "17集仅2条真实评论实锤, 旧'报数打卡/硬邀请'型全废)**: "
                             "留一句让观众'有话想说'的痒点句 — 客户镜头 (28-40岁白领, 城市线序二>三>四>一, "
                             "强升职意愿; 场景从 汇报被打断/方案被署别人名/同届没升/评语'再稳重些'/35岁线 现造), "
                             "四型轮换禁连用: 对号断言 ('二三线的办公室有条暗规律: 活最重的人往往不是明年升的那个') / "
                             "自曝镜像 ('我第一次汇报, 开场白练了一晚上, 领导抬头只问: 所以你要多少钱' — 老谭自曝观众照镜子) / "
                             "时机叩问 ('这一层我是第八年才看破的, 你是第几年？') / 反差留白 ('把汇报做得越漂亮的人越难升——这话我拆给你看'); "
                             "**钩≠邀请: '评论区/留言/聊聊/说说/打在/报个到/教教我/打卡'等平台词与邀请令一律禁写** "
                             "(痒点句本身即钩, 观众想说自己会说; 平台词只占字数催流失); "
                             "零披露成本 (观众接话无需当众认怂); 身份落款由系统注入不写稿; "
                             "本集骨架一行速览 (秘籍三档各半句)。三案六句内完成, 禁'上集我们讲了'复述腔; "
                             "禁回顾段展开人物故事 (埋点一句即可, 兑付留片尾)。\n" + _hook_extra)
        else:
            _hint_ep_type = ""
        _form_hints = (("判定式" in _secret or "判定法" in _secret,
                        "秘籍讲法=问答推演: 带观众把判定问题问出来→两种答案→各自推论, 让人当场对号。"),
                       ("清单" in _secret, "秘籍讲法=逐项过: 每项一句判断标准+一个反例, 像打勾一样过完。"),
                       ("自测表" in _secret or "自测" in _secret, "秘籍讲法=带着自问: 抛出每个自测问题后停半拍, 再给判读方式。"),
                       ("标准卡" in _secret or "定义" in _secret, "秘籍讲法=替换式: 明说「把旧标准X换成新标准Y」, 给一个前后对比例。"),
                       ("行动册" in _secret or "工具" in _secret, "秘籍讲法=装订式: 按序号串起每件工具, 每件一句怎么用。"))
        _fh = next((h for k, h in _form_hints if k), "")
        _hint_secret = (f"本集秘籍 : {_secret}\n{_fh}\n"
                        "本集核心交付物就是它 — 书料论证为它铺垫, 收尾口诀/微行动围绕它。\n") if _secret else ""
    else:
        _hint_plain = ("7. 书中专业概念 (本书的概念/机制名) 口播必须翻译成大白话，"
                       "用宝妈/婚姻/亲子/职场的生活例子解释（“你说得好”不如“你一听就懂”），不裸甩专业术语。\n")
        _hint_hook = ("8. 结尾抛一个生活化钩子问题（绑定夫妻矛盾/亲子拉扯/职场委屈/讨好型内耗），"
                      "自然引导观众留言或追下集，不空洞求关。\n")
        _self_ban = "'我是XX/我是静姐'自称"
        _hint_views = ""   # 静读书不走视角适配 (0908 老谭侧专属)
        _hint_ep_type = ""
        _hint_secret = ""
    # ── 附加指令: 动态硬数据, 优先级高于模板 ──
    # 0912: 语速 4.7→4.6 (2.5 实测锚); 0907: 字数基线按目标时长动态换算 — 旧硬编码 2800-2950 按 600s
    # 设计, 老谭读书 300s 时误导 LLM 写超一倍 (155句/3166字/11分钟实锤)。
    # 1018 C4a: 语速 4.6→SPEECH_RATE=5.0 (《定位》7 集实测 median 4.974); 单带改弹性带
    # (北斗星第3条 字数=弹性工具): 严格≤软带上限 (相对带∩平台包络), 推荐 带下限~cap。
    _dur = float(ep.target_duration_sec or 600)
    _wb_lo, _wb_hi, _wb_hlo, _wb_hhi = word_band(_dur)
    _cap_chars = int(_dur * SPEECH_RATE)
    _char_lo, _char_hi = _wb_lo, min(_cap_chars, _wb_hi)
    sys_p = (
        base
        + "\n\n【本集附加指令 · 优先级高于模板】\n"
        + f"1. 本集：第{ep.ep_index}集，目标时长 {ep.target_duration_sec:.0f}s，主题：{ep.title}。\n"
        + f"2. 六段弹性标签（逐字保留，作为段落标题）：\n{label_table}\n"
        + (f"1b. 本集核心问题（痛点锚 — 痛点共鸣段对它开刀, 钩子与结尾困境同源）: "
           f"{str(_rm.get('核心问题') or '')}\n" if _rm.get('核心问题') else "")
        + "   【】括号是标签专用符号：除上述 6 个标签行外，正文任何位置禁止使用【】"
          "（含引用/强调/批注/小标题）——需要强调时用引号或破折号。\n"
        + f"3. 各段参考字数（{SPEECH_RATE} 字/秒）：{char_hints}。\n"
        + f"4. 字数基线（硬校验, 按目标时长 {_dur:.0f}s 换算, 覆盖模板基线数字）："
          f"全文正文严格≤{_char_hi}字、推荐{_char_lo}-{_char_hi}字，宁短勿超。\n"
        + "5. 必须输出全部 6 个标签行，末段终点≈目标时长±10%；**语义段落之间空一行**"
          "（空行=段界 — 音频断包/段落气口/分镜切场全按它切, 标签行下每个独立语义单元"
          "自成一段, 禁整节连排不空行; 单换行=同段内的句行）。\n"
        + "6. 约束：书中论证与案例≥60%（书为主体，与模板一致），场景/类比/解读等迁移≤40%；观点须可追溯来源，"
          "严禁编造书中不存在的观点；中性表述，禁“你一定/你必须/相信我/震惊/颠覆”；"
          "3处以上日常场景化举例；第1集无回顾（该段写痛点共鸣+引入书籍），"
          "第6集收尾无预告（写系列完结语）；金句无逐字出处只转述不挂引号。"
          "**数字形态按语义直写**（系统零转换, 字幕直出稿原文）：真数量/序数/百分比用阿拉伯"
          "（3年前、第5条、10%、1984年、12个月）；不定冠词的“一”用中文（这一集、一本、"
          "一个、一天、再来一次）。合成读音由系统转换, 你只管语义形态。"
          "不要加身份段/结尾落款（系统后处理注入）；全稿禁止任何位置出现"
          + _self_ban + "——自我介绍由系统注入, 创作层只写内容本身。\n"
        + _hint_plain
        + _hint_hook
        + _hint_views + _hint_ep_type + _hint_secret
        + "9. 规避治疗承诺词：禁'疗愈/治疗/治愈/心理诊疗'，统一'觉察/梳理/看见/练习'；"
          "禁绝对化（最/唯一/顶级/天花板）、迷信（好运/辟邪/转运）、金融（赚钱/暴富/稳赚不赔）、引流（微信/私信我/完整版在xx）。\n"
        + "10. 正文末尾另起一段附严格 JSON 块 {\"coverage\":[str]} 列本集知识点"
          "（不计入字数），该块之后禁止再输出任何文字。\n"
    )
    # Gate B (2026-08-19): 书线合规红线注入 — 配置驱动, 规则集升版自动生效
    rules = load_book_rules()
    hr = " / ".join(v for k, v in (rules.get("high_risk_delete") or {}).items() if not k.startswith("_"))
    mr = " / ".join(v for k, v in (rules.get("medium_risk_rewrite") or {}).items() if not k.startswith("_"))
    mn = " / ".join(v for k, v in (rules.get("minor_risk_framing") or {}).items() if not k.startswith("_"))
    safe = (rules.get("safe_formulas") or ["这本书提出……（书中观点）"])[:2]
    sys_p += (
        f"\n平台合规红线(书线): 禁[{hr}]; 转述改写[{mr}]; 逻辑红线[{mn}]; "
        f"书中观点一律视角限定, 可用{' / '.join(safe)}; L0 精华若有「合规备注」节必须遵守。"
    )
    # 2026-08-21 Gate B 强化: 关键规则单独成行硬约束 (整行拼接权重低, 逐条必守)
    def _rule_text(group: str, key: str) -> str:
        v = (rules.get(group) or {}).get(key)
        return v if isinstance(v, str) else ""
    hard_lines = []
    abs_claim = _rule_text("medium_risk_rewrite", "absolute_claims")
    qual_disc = _rule_text("medium_risk_rewrite", "qualification_disclaimer")
    map_ban = _rule_text("minor_risk_framing", "reality_mapping_ban")
    heal_ban = _rule_text("minor_risk_framing", "healing_vocab_ban")
    scene = _rule_text("minor_risk_framing", "audience_scene")
    abs_ban = _rule_text("minor_risk_framing", "absolute_claims_ban")
    prom_ban = _rule_text("minor_risk_framing", "promise_ban")
    super_ban = _rule_text("minor_risk_framing", "superstition_ban")
    fin_ban = _rule_text("minor_risk_framing", "finance_claims_ban")
    ext_ban = _rule_text("minor_risk_framing", "external_link_ban")
    if abs_claim:
        hard_lines.append(f"1. 书中观点限定: {abs_claim}")
    if qual_disc:
        hard_lines.append(f"2. 资质边界: {qual_disc}")
    if map_ban:
        hard_lines.append(f"3. 不映射现实: {map_ban}")
    if heal_ban:
        hard_lines.append(f"4. 禁治疗承诺词: {heal_ban}")
    if scene:
        hard_lines.append(f"5. 受众场景: {scene}")
    if abs_ban:
        hard_lines.append(f"6. 禁绝对化用语: {abs_ban}")
    if prom_ban:
        hard_lines.append(f"7. 禁虚假承诺: {prom_ban}")
    if super_ban:
        hard_lines.append(f"8. 禁迷信用语: {super_ban}")
    if fin_ban:
        hard_lines.append(f"9. 禁金融承诺: {fin_ban}")
    if ext_ban:
        hard_lines.append(f"10. 禁站外引流: {ext_ban}")
    if hard_lines:
        sys_p += "\n【合规硬约束 · 逐条必守(优先级最高, 与上面汇总同源)】\n" + "\n".join(hard_lines)
    # 2026-08-22: 全系统统一限流词注入 (config/compliance_common.json — 拆书/新闻线共用)
    # 0912 合并 (用户令): 删此处二次注入 — 模板加载器 (_load_prompt_template) 已统一注入,
    # 双注实锤 (02_sys 留档红线块 ×2), 纯 token 浪费。
    # 2026-08-23: 口播稿质量标准 — 系统性防 5 类硬伤 + 口语化示范 (用户验收迭代 v2)
    sys_p += (
        "\n【口播稿质量标准 · 成片前硬性自查】\n"
        "1. 口语化: 像真人聊天, 禁书面腔/AI腔. 手法 (按本集内容现造, 禁照抄示例):\n"
        "   · 心理概括 → 脑内原声 (\"从不敢说不\"→那个\"不\"字反反复复没说出口式)\n"
        "   · 行为概括 → 带情绪的动作 (\"你笑着说好\"→满心不情愿也还是应了下来式)\n"
        "   · 抽象比喻 → 中国人熟悉的日常物件比喻 (贴本集话题现造)\n"
        "   · 书面动词 → 口语动词 (\"看一看\"→\"聊聊\")\n"
        "2. 忠实原文: 引用书中台词/金句/对话必须与原文一字不差, 且用 ⟦⟧ 包裹 "
        "— ⟦⟧ 是原话保护标记, 后续处理见标记一字不改; 禁改写/概括/编造; 拿不准就不引.\n"
        "3. 角色连续性: 书中人物首次出现必须一句话介绍身份(是谁、什么关系), 不能直接进对话.\n"
        "4. 生活常识: 场景/对话符合真实生活 (人情往来按常识写, 禁港台剧腔/教科书腔).\n"
        "5. 反讽引号: 反讽/语境相反义/强调词必须加中文引号(“”) — "
        "字幕会保留引号, 不加引号就露馅. 不加引号=没读懂反讽.\n"
        "6. 禁写写作指令: 全稿只能是给观众听的话, 禁止把写作要求/提示语写进口播稿 "
        "(如\"最后留一个生活钩子\"\"这里举例\"这类绝不能出现).\n"
        "7. 段落去重: 每段讲新点, 同一概念/例子全稿只讲一次; 后段不得重复前段已讲的定义或场景.\n"
        "8. 比喻贴语感: 比喻须用中国人熟悉的日常意象, 与论证严格对应.\n"
        "9. 生造缩合词禁: 动词搭配逐个读出声自查 — 口播里没人在说的两字缩合=车祸\n"
        "   (\"手掏\"✗→\"掏钱的\"✓; \"嘴上说的和手掏的不一样\"✗). 拿不准就展开成完整说法.\n"
        "10. 量词与列举物严格对应: 说\"三个字\"后面必须真是三个单字; 列的是词组就说\"三个词\";\n"
        "    \"写三个字\"然后列\"广告主/用户/补贴\"=错配. 列举完回读一遍核对.\n"
    )
    # 2026-08-25: 女性目标人群语感法则 (用户对第2集逐句验收提炼) — 教判断标准, 不是词表:
    # 同一个意思换表达, 观众感受完全不同; 表达可变, 法则不变.
    # 0912 删② (用户令): 女性语感按线分流 — 静读书 (女性受众) 专属, 老谭线 (男性为主) 剥离
    if not _is_laotan:
        sys_p += (
            "\n【女性目标人群语感 · 人设定律】(观众=25-50岁女性, 创作前先读懂再动笔)\n"
        "总纲A · 人群跨度: 25岁和45岁的女性共用这套稿. 措辞必须落在'全跨度大众共识', "
        "只对年轻段有效的'怯懦/小女生'表达, 45岁观众看着就不是自己 — 取公约数, 不取极端.\n"
        "总纲B · 人设复利: 单句改不改都过得去, 但每句都在给主播人设记账. "
        "主播的位置是'清醒的共情者'——看得懂讨好, 但不活成讨好. 台词把'懂事退让'写多了, "
        "人设就从'帮你说话的明白人'滑向'受气包闺蜜', 长期掉粉.\n"
        "法则1 · 心理原声化: 人物反应写脑内第一人称原声, 不写作者的心理概括. "
        "反例→正例: '马上在自己身上找原因' → 第一反应是'我又怎么了'.\n"
        "法则2 · 自我合理化话术: 讨好者不说'我讨好', 说'我顾家'. 行为要用当事人给自己找的台阶呈现. "
        "反例→正例: '把家务全包了' → '顺理成章地调整了生活重心'; "
        "'怪自己要求太高' → '怪自己太讲究形式'. 注意: 自责只能落在'我可以不计较'(形式主义/想开了), "
        "绝不落在'我不该有要求'(要求太高/太贪心) — 后者暗示女性正常诉求是过错, 冒犯观众.\n"
        "法则3 · 憋屈剂量控制: 一个场景示弱只给一层, 语境已立住就不再叠加. "
        "反例→正例: 忘了纪念日这个语境已经够憋屈, 口边的话是'没关系'就够了, "
        "再加一句'你忙'就成了叠层示弱 — 观众的同情有配额, 超额就从共鸣变反感.\n"
        "法则4 · 大众词优先于病理词: 台词要落在'大多数女性真说得出口的话'. "
        "反例→正例: 职场被抢功, 口边是'没事'(强装大度, 大众常态), 不是'没关系'(认怂退让, 小众). "
        "'强装大度'人人有共鸣, '怯懦'只有少数人对号入座.\n"
        "法则5 · 歧义审计: 逐句自查'这个词在女性读者耳朵里有没有第二种解读'. "
        "反例→正例: '随叫随到'有歧义误读, 换'恨不得24小时在线'.\n"
    )
    if ep.ep_index == 1 and rules.get("opening_disclaimer"):
        # 2026-08-21 免责视觉化: 免责不上口播(省开篇黄金时间), 由成片首帧右上角字幕呈现
        sys_p += ("\n强制免责(视觉化, 不上口播): 系统会在成片首帧画面右上角加免责字幕"
                  "('以下仅为这本书的作者观点…'), 口播稿不要念免责声明, "
                  "把开篇 0-3 秒黄金时间留给痛点钩子。")
    # 2026-08-22 逐集按单元消费: 从 roadmap 的 unit_id 取对应主题单元素材 (core_claim/概念/共鸣/类比/敏感),
    # 评论按 unit_id 过滤; 用单元概念在 L0 章节定位 quotes/cases.
    unit_block = ""
    unit_comments = inp.get("comment_layer") or []
    l0_unit = None
    if ep.roadmap_json and ep.roadmap_json.get("unit_id"):
        _r, units = _l0_facing(book)
        l0_unit = next((u for u in units if u.get("id") == ep.roadmap_json["unit_id"]), None)
        if l0_unit:
            unit_block = (
                f"\n【本集主题单元 · 素材】(优先级高于全书输入)\n"
                f"- 单元:{l0_unit.get('id')}《{l0_unit.get('title')}》\n"
                f"- 核心主张:{l0_unit.get('core_claim', '')}\n"
                f"- 概念:{','.join(str(c) for c in (l0_unit.get('concepts') or []))}\n"
                f"- 读者共鸣:{';'.join(str(r) for r in (l0_unit.get('reader_resonance') or []))}\n"
                f"- 类比钩子:{';'.join(str(a) for a in (l0_unit.get('analogy_hooks') or []))}\n"
                + (f"- 敏感:{l0_unit['sensitive'].get('level', '')} 安全讲法:{l0_unit['sensitive'].get('safe_angle', '')}"
                   if l0_unit.get("sensitive") else "")
            )
            unit_comments = [c for c in unit_comments if c.get("unit_id") == l0_unit["id"]]
    # L0 章节定位 (单元概念命中 → 该章 quotes/cases 注入)
    # 0912 拐棍切换 (樊登前置架构): 有樊登全书稿+总纲切片 → 拐棍=确定性切片,
    # 不再逐集跑樊登; 旧书无稿走 legacy 逐集樊登 (原路径不动)
    _fd_slice_out = ""
    if _is_laotan and not __import__("os").environ.get("BOOK_DISABLE_FANDENG"):
        # ep1 拼装式开局在: 常规"重讲"拐棍让位 (前三段已逐字锁定, 再喂切片会诱导重写)
        _skip_slice = bool(_fd_open_paras) and ep.ep_index == 1
        try:
            from .fandeng_full import load_fandeng_full, slice_paragraphs
            _sl = (ep.roadmap_json or {}).get("樊登切片") or {}
            if _sl.get("start_p") and _sl.get("end_p") and not _skip_slice:
                _ff_text = load_fandeng_full(book.book_title)
                if _ff_text:
                    _fd_slice_out = slice_paragraphs(
                        _ff_text, int(_sl["start_p"]), int(_sl["end_p"]))
                    logger.info("[book] 拐棍=樊登全书稿切片 P%s-P%s (%d字)",
                                _sl["start_p"], _sl["end_p"], len(_fd_slice_out))
                    if not _fd_slice_out:
                        logger.warning("[book] 切片范围空 (P%s-P%s 越界?), 回退逐集樊登",
                                       _sl["start_p"], _sl["end_p"])
        except Exception as exc:
            logger.warning("[book] 切片拐棍加载失败, 回退逐集樊登: %s", exc)
    # L0 命中章节名 (切片模式下 L0 原文节选只取这些章, 替代从头切8000字)
    _hit_ch_titles: set[str] = set()
    _hit_ch_blobs: list[str] = []
    unit_quotes_cases = ""
    unit_concepts: set[str] = set()   # 0926 案例库接线: 概念键预置 (L0缺料时保底空集)
    try:
        from app.services.book_service.l0 import _l0_dir
        l0_path = _l0_dir(book.book_title) / "l0-chapter-v1.json"
        if l0_path.exists() and l0_unit:
            l0 = json.loads(l0_path.read_text(encoding="utf-8"))
            unit_concepts = set(str(c) for c in (l0_unit.get("concepts") or []))
            hit_ch = [ch for ch in l0.get("chapters", [])
                      if any(str(c.get("name", "")) in unit_concepts or
                             any(str(c.get("name", "")) == uc for uc in unit_concepts)
                             for c in (ch.get("concepts") or []))]
            # 0912 内容锚命中: 整词精确匹配对不上总编剧造词 (u02"付费主体" vs 章概念
            # "付费电视商业模式" 0命中实锤 — 与料仓整节兜底同病根)。切片=本集真实内容,
            # 拿它与章节概念做 bigram 重叠打分取 top3, 与精确命中取并集。
            if _fd_slice_out:
                def _bg(s: str) -> set[str]:
                    return {s[i:i + 2] for i in range(len(s) - 1)}
                _anchor = _bg(_fd_slice_out[:1200] + "、".join(unit_concepts)
                              + str(l0_unit.get("core_claim") or "")[:150])

                def _ch_score(ch) -> float:
                    blob = ("、".join(str(c.get("name", "")) for c in (ch.get("concepts") or []))
                            + str(ch.get("title") or ""))
                    return len(_anchor & _bg(blob)) / max(1, min(len(_anchor), 60))

                _scored = sorted(((_ch_score(ch), ch) for ch in l0.get("chapters", [])),
                                 key=lambda x: x[0], reverse=True)
                _extra = {id(ch) for ch in hit_ch}
                for sc, ch in _scored[:3]:
                    if sc >= 0.10 and id(ch) not in _extra:
                        hit_ch.append(ch)
                        _extra.add(id(ch))
                if hit_ch:
                    logger.info("[book] 章节内容锚命中: %s (切片%d字)",
                                [str(ch.get("title") or "")[:12] for ch in hit_ch[:4]], len(_fd_slice_out))
            _hit_ch_titles = {str(ch.get("title") or "") for ch in hit_ch} - {""}
            # 命中章节自带文本 (L0原文节选直接吃它 — source_path 是蒸馏txt单章«全文»,
            # 回原书按名匹配永远 0 命中, 23:57 实锤)
            _hit_ch_blobs = [f"【{str(ch.get('title') or '')[:24]}】{str(ch.get('text') or ch.get('summary') or '')}"
                             for ch in hit_ch if (ch.get("text") or ch.get("summary"))]
            qs = [q for ch in hit_ch for q in (ch.get("quotes") or [])][:6]
            cs = [c for ch in hit_ch for c in (ch.get("cases") or [])][:4]
            if qs or cs:
                unit_quotes_cases = "\n【本单元书内金句/案例】\n"
                if qs:
                    unit_quotes_cases += "金句: " + " / ".join(str(q.get("text", ""))[:60] for q in qs) + "\n"
                if cs:
                    unit_quotes_cases += "案例: " + " / ".join(str(c.get("desc", ""))[:60] for c in cs) + "\n"
            # 2026-08-22: 源头书注入 — 本书相关章节引用的书/理论 (reference_library 消除孤岛, 逐集内容关联)
            refs = []
            for ch in hit_ch:
                for r in ch.get("references") or []:
                    nm = str(r.get("name", "")).strip()
                    if nm and nm not in refs:
                        refs.append(nm)
            if refs:
                unit_quotes_cases += f"\n【本书相关章节引用的源头书/理论(可自然带出, 不硬塞)】{', '.join(refs[:5])}\n"
            # 2026-08-22: 相关人物/实体注入 — 消除 entities 孤岛 (讲情节/案例时自然称呼)
            ents = []
            for ch in hit_ch:
                for e in (ch.get("entities") or []):
                    nm = str(e).strip()
                    if nm and nm not in ents:
                        ents.append(nm)
            if ents:
                unit_quotes_cases += f"\n【本单元相关人物/实体(讲情节或案例时自然带出, 勿平铺罗列)】{', '.join(ents[:5])}\n"
    except Exception:
        pass
    # v2 料仓取料 (0908 用户令·全迁): sections/ 故事库+方法与清单, 按本集概念过滤
    # 0912 樊登前置: 切片模式下故事库不再喂 (拐棍=切片, 叙事已由樊登稿统一分配);
    # 0926 硬核层接线: 公共案例库 (概念匹配) + 可引用原句加厚 (原书锚段直引唯一合法来源)
    try:
        from app.services.book_service.l0 import _l0_dir as _l0d
        _kw = ("、".join(sorted(unit_concepts)[:12])
               + "、" + "、".join(str(k) for k in ((ep.roadmap_json or {}).get("概念") or []))
               + "、" + str((ep.roadmap_json or {}).get("主题") or "")
               + "、" + str((ep.roadmap_json or {}).get("工具名") or "")
               + ("、" + str(l0_unit.get("core_claim") or "")[:100] if l0_unit else ""))

        def _bg2(s: str) -> set[str]:
            return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()

        _ak = _bg2(_kw[:500])
        _bank_p = _l0d(book.book_title).parent / "案例库.json"
        if _bank_p.exists():
            def _cscore(c: dict) -> int:
                blob = c["name"] + "、".join(str(t) for t in (c.get("concepts") or []))
                s = sum(5 for t in (c.get("concepts") or []) if str(t) and str(t) in _kw)
                return s + len(_bg2(blob) & _ak)
            _hit = [c for c in (json.loads(_bank_p.read_text(encoding="utf-8")).get("cases") or [])
                    if c.get("status") == "可用" and _cscore(c) > 3]
            _hit = sorted(_hit, key=_cscore, reverse=True)[:6]
            if _hit:
                _lines = [f"- {c['name']} [{c.get('role')}|{c.get('fame')}] "
                          f"概念: {'/'.join(c.get('concepts') or [])}\n"
                          f"  事实: {'；'.join(c.get('facts') or [])}\n"
                          f"  边界: {c.get('boundary') or ''}" for c in _hit]
                unit_quotes_cases += ("\n【公共案例库·本集概念匹配 (主案例与反例必须从此选, "
                                      "主案例只准国民级; 每案必答 为什么起效/什么时候失效, "
                                      "边界字段是预埋答案; 库外案例=提名制待审; "
                                      "大厂案例口径三则: ①只述管理动作禁评企业好坏 "
                                      "(收口句式='这个动作印证了书中观点', 同款全篇≤1次"
                                      "其余换说法) "
                                      "②引入句必带限定词 公开报道/当年对外披露/早期 "
                                      "③禁近两年热点争议事件)】\n"
                                      + "\n".join(_lines) + "\n")
                logger.info("[book] 案例库命中 %d 案: %s", len(_hit),
                            "、".join(c["name"] for c in _hit))
            else:
                # 0926 用户实锤 ep2 零中国品牌视角: 库无匹配≠可静默退回纯书内案例 —
                # 中国案例是每集硬指标, 无匹配必须走提名制 (宪法+注入同向铁律);
                # 提名路径=用户传授的推导启发框架 (0926)
                unit_quotes_cases += ("\n【中国品牌案例硬指标】案例库无本集概念匹配 — 必须走提名制: "
                                      "按推导框架自寻 — ①问\"这个品类里第一是谁、第二第三更迭过谁\""
                                      "(各品类都有排位史); ②优先1997年后(品牌影响力在中国大规模显现); "
                                      "③目标池=大厂/超市货架品牌/春晚标王级知名度; ④从人设从事领域联想。"
                                      "提名 ≥1 个中国品牌案例 (只用公知口径骨架, 禁编细节数字), "
                                      "讲机制两问 (为什么起效/什么时候失效), 备注标\"待审\"给人闸; "
                                      "零中国案例=废稿。"
                                      "口径三则 (与案例库同规): 选题只取已沉淀公知史 (禁近两年热点争议事件); "
                                      "引入大厂案例的句子必带限定词 — 公开报道、当年对外披露、早期 "
                                      "(例: '公开报道、当年对外披露，字节早期推行文档文化'); "
                                      "只描述管理动作、禁评企业伟大/失败, 收口句式='这个动作印证了书中观点'"
                                      "(同款全篇≤1次, 其余换说法: 这正是书里说的/德鲁克这句话就是给它的注脚)。\n")
        _q_p = _l0d(book.book_title) / "sections" / "可引用原句.txt"
        if _q_p.exists():
            _ql = [l.strip().strip('"“”') for l in _q_p.read_text(encoding="utf-8").splitlines()
                   if len(l.strip()) >= 12]
            _ql = [q for q in _ql if len(_bg2(q) & _ak) >= 2][:8]
            if _ql:
                unit_quotes_cases += ("\n【可引用原句 (原书锚段的直引唯一合法来源; "
                                      "引用前校对OCR错字; ⟦⟧包裹输出)】\n"
                                      + "\n".join(f"- {q}" for q in _ql) + "\n")
                logger.info("[book] 可引用原句加厚 %d 句", len(_ql))
    except Exception as exc:  # noqa: BLE001 — 接线失败不断生成, 宪法降级为无库运行
        logger.warning("[book] 案例库/可引用原句接线失败 (跳过): %s", exc)
    # 0913 料5方法清单默认关 (用户令: 与料2b秘籍重复, 全集零用) — 切片模式料仓整停,
    # legacy 路径 (无切片) 保持旧行为不动
    # 1005 治本令 (用户: 守着宝库要饭 — L0 蒸馏层利用率审计实锤): 切片模式按集型重开 —
    #   工具集喂「方法与清单」(判定刻度的正源, 治秘籍空转) / 收官集喂「方法与清单+故事库」
    #   (1005 三件套铁律: 过线判据/误用反例的料源); ep1 大钩子集维持整停 (拼装开局零杂料)。
    _feed_secs: list[tuple[str, str, int]] = []
    if not _fd_slice_out:
        _feed_secs = [("故事库", "本集相关故事(叙事体, 保留年份/人物/代名作)", 2500),
                      ("方法与清单", "书中方法/清单(含边界, 本集秘籍的料)", 1500)]
    elif _is_tool_ep:
        _feed_secs = [("方法与清单",
                       "书中方法/清单(本集秘籍的判定刻度料 — 判定必须落在这些清单上, 禁自造刻度)", 1500)]
    elif _is_finale:
        # 1005 用户料单令: 末集喂十节全料 (除可引用原句 — 收官集不逐字引书),
        # 每节允许"一句半句"地用 — 判据/误用/收口反调的料源; cap 防厚书撑爆预算
        _feed_secs = [("方法与清单",
                       "书中方法/清单(每页三件套·过线判据的正源 — 判据必须落在这些清单上)", 2000),
                      ("故事库", "本集相关故事(误用反例/叙事钉, 叙事体保留年份/人物/代名作)", 1500),
                      ("核心概念与机制", "书中概念与机制(判定逻辑的底料)", 600),
                      ("反模式与坑", "书中反模式与坑(误用警告的正源)", 400),
                      ("一页核", "全书一页核(总装订顺序的骨架)", 350),
                      ("核心哲学", "全书核心哲学(收口反调的料)", 300),
                      ("成功路径", "全书成功路径(装订顺序参考)", 300),
                      ("元问题", "全书元问题(收口呼应)", 150)]
    if _feed_secs:
        try:
            from app.services.book_service.compliance_gate import sanitize_material
            from app.services.book_service.distiller import load_section
            _rm_concepts = [str(c) for c in ((ep.roadmap_json or {}).get("概念") or [])]
            _sp = book.source_path or ""
            for _sec_name, _label, _cap in _feed_secs:
                _t = load_section(book.book_title, _sec_name)
                if not _t:
                    continue
                if _rm_concepts:
                    _paras = [p for p in re.split(r"\n\s*\n", _t)
                              if any(k and k in p for k in _rm_concepts)]
                    _t = "\n\n".join(_paras) if _paras else _t
                _safe_t, _ = sanitize_material(_t[:_cap], book.book_title, source_path=_sp)
                if _safe_t.strip():
                    unit_quotes_cases += f"\n【{_label}】\n{_safe_t.strip()}\n"
        except Exception:
            pass

    # E1 系列预告注入 (2026-08-22 → 0912 用户悟升级): ep1 与 ep6 同构 — ep6=收束汇总
    # 需全集内容, ep1=全集大钩子同样要吃 **2-5 集的实产全稿** (用户令: 稿子+ep1流程=味道),
    # 导览/预告/打字卡从真稿取真实交付物 (秘籍名/数字/金句), 只点名禁展开论证。
    # 生成顺序 2-5 → 1 → 6。
    def _is_tool_ep_of(n: int) -> bool:
        # 工具集判定 (0914 结构参数化): 2..N-1 — ep1 大钩子料只吃工具集实产全稿
        n_all = len(book.episodes) or 6
        return 1 < n < n_all

    series_inject = ""
    if ep.ep_index == 1:
        _later_eps = sorted((e for e in book.episodes if _is_tool_ep_of(e.ep_index)),
                            key=lambda x: x.ep_index)
        _have_scripts = [e for e in _later_eps if (e.script_text or "").strip()]
        if _have_scripts:
            parts = []
            for e in _have_scripts:
                rm = e.roadmap_json or {}
                subj = rm.get("主题") or e.title or ""
                parts.append(f"第{e.ep_index}集《{subj}》实产全稿(已剥段落标签与回顾节):\n"
                             f"{_strip_for_series_feed(e.script_text)[:1400]}")
            series_inject = (
                "\n【系列大钩子 · E1 整集职能】ep1 不只是第一集, 是整个系列的大钩子。\n"
                "下面是各工具集的实产全稿 — 系列导览/预告/打字卡从这里取**真实交付物**"
                "(每集一个差异化动词 + 秘籍名/数字/金句点名); **只点名禁展开** "
                "(后续集的论证/答案是它们的正菜, ep1 展开即剧透违规), 禁凭空许愿。\n"
                + "\n\n".join(parts) + "\n")
        else:
            prev_txt = (ep.roadmap_json or {}).get("系列预告") or ""
            if not prev_txt:
                others = []
                for e in sorted(book.episodes, key=lambda x: x.ep_index):
                    if e.ep_index <= 1:
                        continue
                    rm = e.roadmap_json or {}
                    subj = rm.get("主题") or e.title or ""
                    secret = str(rm.get("本集秘籍") or "")
                    nail = str(rm.get("数据锚点") or "")
                    seg = f"第{e.ep_index}集《{subj}》秘籍:{secret[:40]}"
                    if nail:
                        seg += f"|数字:{nail[:30]}"
                    others.append(seg)
                if others:
                    prev_txt = (f"本系列共{len(book.episodes)}集：" + ";".join(others)
                                + "。预告须动词化 (每集一个差异化动词+半句悬念), 末集=工具全汇总+建议按序听")
            if prev_txt:
                series_inject = (f"\n【系列大钩子 · E1 整集职能】ep1 是整个系列的大钩子 — "
                                 f"系列导览/预告必须引用各集真实交付物 (秘籍名+数字), 禁凭空许愿。"
                                 f"本期开头 20-30 秒预告系列：{prev_txt}"
                                 "（引导关注/追更，具体不空洞，禁止'记得点赞关注'式直白求关）。\n")
        # 系列收益承诺+各集钩子料 (导览总起/每行一钩的料源): 灵魂三问❸是导演提炼的
        # 收益链 (比临场现编强), 黄金三秒钩子是各集现成钩子句 — 导览禁目录腔裸报名
        _soul = inp.get("灵魂三问") if isinstance(inp.get("灵魂三问"), dict) else {}
        _gain = str(_soul.get("gain") or "")
        _hook_lines = []
        for e in sorted(book.episodes, key=lambda x: x.ep_index):
            if not _is_tool_ep_of(e.ep_index):
                continue
            h3 = str((e.roadmap_json or {}).get("黄金三秒钩子") or "")
            sec = str((e.roadmap_json or {}).get("本集秘籍") or "")[:30]
            if h3 or sec:
                _hook_lines.append(f"第{e.ep_index}集({sec}): {h3[:80]}")
        if _gain:
            series_inject += f"\n【系列收益承诺 · 导览总起/收尾承诺必用它的收益链】{_gain}\n"
        if _hook_lines:
            series_inject += ("\n【各集钩子料 · 导览每行一钩从这取 (改写成'第X集, 动词+工具——半句为什么要听')】\n"
                              + "\n".join(_hook_lines) + "\n")

    # 2026-08-22: facing-hooks 注入 (开篇钩子/结尾互动/下集预告) + kernel.why_read (E1 导读)
    hook_block = ""
    try:
        from app.services.book_service.l0 import _l0_dir
        facing_d = _l0_dir(book.book_title) / "facing"
        hooks = json.loads((facing_d / "facing-hooks.json").read_text(encoding="utf-8")).get("hooks", [])
        uid = (ep.roadmap_json or {}).get("unit_id")
        hk = next((h for h in hooks if h.get("unit_id") == uid), None) or (hooks[0] if hooks else None)
        if hk:
            parts = []
            if hk.get("opening_hook"):
                parts.append(f"开篇钩子(3秒): {hk['opening_hook']}")
            if hk.get("closing_question"):
                parts.append(f"结尾互动: 抛生活化问题引导评论→{hk['closing_question']}")
            if hk.get("next_teaser"):
                parts.append(f"下集预告: {hk['next_teaser']}")
            if parts:
                hook_block = "\n【本集钩子·design】\n" + "\n".join(parts) + "\n"
    except Exception:
        pass
    kernel_why = ""
    if ep.ep_index == 1:
        try:
            from app.services.book_service.l0 import _l0_dir
            k = json.loads((_l0_dir(book.book_title) / "facing" / "facing-kernel.json").read_text(encoding="utf-8"))
            if k.get("why_read"):
                kernel_why = f"\n【为什么值得读 · E1 导读必带】{k['why_read']}\n"
        except Exception:
            pass

    # 书三件套实料 (0912 用户令: ep1 书/作者/解决的问题说太少): 作者喂 DB 字段,
    # 元问题喂蒸馏节 — 没实料生成器只能编或略
    book_meta_ep1 = ""
    if ep.ep_index == 1:
        _mq = ""
        try:
            from .distiller import load_section as _ls2
            _mq = (_ls2(book.book_title, "元问题") or "").strip()
        except Exception:
            pass
        if _mq:
            book_meta_ep1 = f"\n【本书元问题 · E1 元问题拍必用】{_mq[:200]}\n"

    # 0913 废料清理 (用户令: 料9a豆瓣/料9b关系意图/料6 L0节选/料5方法清单 全集零用 —
    # 网页版四模块优选全扫零命中): 料8缩成核心主张+关键概念清单, legacy路径冻结不动
    _inp_core = (inp.get("全书核心主张") or {})
    _core_claim = _inp_core.get("value") if isinstance(_inp_core, dict) else str(_inp_core)
    _concepts_top = inp.get("关键概念清单")
    _concepts_s = "、".join(str(x) for x in _concepts_top[:20]) if isinstance(_concepts_top, (list, tuple)) else str(_concepts_top or "")
    _book_brief = ""
    if _core_claim or _concepts_s:
        _book_brief = "\n【全书底座 (概念锚, 非内容源)】"
        if _core_claim:
            _book_brief += f"核心主张：{str(_core_claim)[:150]}\n"
        if _concepts_s:
            _book_brief += f"关键概念：{_concepts_s[:200]}\n"

    # 0912 L0 节选收敛: 切片模式只喂本集命中章节自带文本 (cap 3000); legacy 保持从头 8000 字
    # 0913: 料6 全集零用 → 切片模式停喂 (块保留但置空), legacy 冻结
    _l0_src_block = ""
    if not _fd_slice_out and book.source_path:
        _l0_src_block = (f"\n【L0 来源节选】\n"
                         f"{wrap_source(source_context(book, cap=8000), label='L0原文')}")

    # 0912 修: 剥 _checks/_issues — 上轮生成的体检报告不再回喂下轮提示词 (污染)
    _roadmap_feed = {k: v for k, v in (ep.roadmap_json or {}).items() if not k.startswith("_")}
    # 0913 宪法落地: 料包总则 (user_p 顶部) + ban_list 注入 (sys_p 尾部)
    from .feed_registry import REGULA_BLOCK, load_ban_list
    _ban_block = load_ban_list()
    if _ban_block:
        sys_p += _ban_block
    # 樊登开局逐字段块 (ep1 拼装式开局): 前三段 ⟦⟧ 锁死 + 后续段降级背景参考
    _fd_open_block = ""
    if _fd_open_paras:
        _ref_tail = ""
        try:
            from .fandeng_full import _paragraphs, load_fandeng_full
            _ref_tail = "\n\n".join(_paragraphs(load_fandeng_full(book.book_title) or "")[3:13])
        except Exception:
            pass
        _fd_open_block = (
            "\n【开局逐字段 · 一字不改 (已成稿全书开场, 质量已验收, ⟦⟧=原话保护标记)】\n"
            + "\n\n".join(f"⟦{p}⟧" for p in _fd_open_paras) + "\n"
            "摆放: 第1段=权威梯六拍之后的故事钩子 (开场口播); 第2-3段=拆解区元问题深挖骨架。"
            "禁增删改一字/换词/压缩/拆段重排; **数字形态原样** (365户禁写三百六十五户, "
            "90%禁写百分之九十, 1928年禁写一九二八年 — TTS 自己会念); "
            "段落标签行只许插在句号处 (标签行不算改动)。\n"
            + (f"\n【全书开场后续段 · 仅背景参考 (是工具集的正菜, 禁整段复述/禁展开论证, "
               f"取一句悬念最多)】\n{_ref_tail}\n" if _ref_tail else ""))
    # 0926 论证骨架层 (用户令"加" — 系统稿与我手写稿差一截的根因: 缺"先想清楚"步):
    # 成稿前一次小 LLM 调用出本集论证设计, 成稿严格按骨架填肉 (动画线两级化同哲学)
    if _is_tool_ep:
        try:
            _SKELETON_SYS = (
                "你是纪录片论证设计师。给这集拆书短视频先做论证设计, 禁写成稿。"
                "输出骨架 (每项一两句, 共300-500字):\n"
                "1. 核心命题: 本集一句话立论, 全稿为证明它;\n"
                "2. 主案例: 是谁、承担什么教学角色; 为什么起效(因果链两三步)、什么时候失效;\n"
                "3. 反面/格局案例: 是谁、证什么边界(前提不满足时的死法);\n"
                "4. 原书锚: 从素材原句里选1-2句, 指向陈述怎么写(用书的论点把案例收拢);\n"
                "5. 三步骨架: 每步教什么(是什么+怎么操作/判定条件/话术模板), 每步一句教学目标;\n"
                "6. 个人锚: 人设的哪个实战场景配哪一步。\n"
                "铁律: 案例从素材的案例库匹配项里选(中国品牌优先, 主案例只准国民级), "
                "禁发明素材外案例; 每案必答两问(为什么起效/什么时候失效); "
                "大厂案例口径三则: 只述管理动作禁评企业好坏、引入句带限定词"
                "(公开报道/当年对外披露/早期)、禁近两年热点争议事件; "
                "直接输出骨架, 不写口播。")
            _sk = _llm().chat(
                _SKELETON_SYS,
                f"【本集主题】{(_rm.get('主题') or '')}\n【概念】{('、'.join(str(k) for k in (_rm.get('概念') or [])))}\n"
                f"【工具名】{(_rm.get('工具名') or '')}\n【素材(金句/案例库/原句)】\n{unit_quotes_cases[-3500:]}",
                model="pro", temperature=0.4, tag="epgen:skeleton")
            _sk = (str(_sk or "")).strip()
            if 200 <= len(_sk) <= 1500:
                unit_quotes_cases = ("\n【本集论证骨架 (先想清楚再写 — 成稿严格按此填肉: "
                                     "案例与锚句禁现场更换, 每步教学目标禁偏移)】\n" + _sk + "\n"
                                     + unit_quotes_cases)
                logger.info("[book] ep%d 论证骨架 ✓ (%d字)", ep.ep_index, len(_sk))
                try:
                    (_gen_dir / "03b_skeleton.txt").write_text(_sk, encoding="utf-8")
                except Exception:
                    pass
            else:
                logger.warning("[book] ep%d 论证骨架长度异常 (%d字), 降级直出", ep.ep_index, len(_sk))
        except Exception as _exc_sk:  # noqa: BLE001 — 骨架失败不阻断, 降级直出
            logger.warning("[book] ep%d 论证骨架失败 (降级直出): %s", ep.ep_index, _exc_sk)
    user_p = (
        TRUST_DECL + "\n"
        + REGULA_BLOCK
        + f"书名：《{book.book_title}》 第{ep.ep_index}集：{ep.title}\n"
        f"作者：{book.author or '未知'}\n"
        f"本集路线图：{json.dumps(_roadmap_feed, ensure_ascii=False)}\n"
        # 0913 废料清理 (用户令): 全书输入4000字dict dump→缩成核心主张+关键概念清单;
        # _relations_block/_douban_block (料9b) 与 素材精华 (料9a) 零用摘除
        + _book_brief
        + unit_block
        + unit_quotes_cases
        + series_inject
        + _fd_open_block
        + kernel_why
        + book_meta_ep1
        + hook_block
        + f"前序覆盖清单：{json.dumps(coverages, ensure_ascii=False)[:1500]}\n"
        + (f"上集尾部：{(prev.script_text or '')[-200:]}\n本集【回顾+引入】须与之咬合。\n" if prev and prev.script_text else "")
        + _l0_src_block
    )
    # ── 两步生成 (0909 用户令: 樊登讲故事→老谭改稿) ──
    # 0912 拐棍切换: 有全书稿切片直接用 (零 LLM 调用); 无切片走 legacy 逐集樊登
    # 0912 A/B 开关: BOOK_DISABLE_FANDENG=1 关樊登层 (b 变体, 留档比对判生死)
    _fd_out = ""
    if _fd_slice_out:
        _fd_out = _fd_slice_out
        logger.info("[book] 拐棍=樊登全书稿切片, 跳过逐集樊登: %d 字", len(_fd_out))
    elif _is_laotan and not __import__("os").environ.get("BOOK_DISABLE_FANDENG"):
        try:
            _fd_path = Path(__file__).resolve().parents[3] / "config" / "fandeng_storyteller.txt"
            if _fd_path.exists():
                _fd_sys = _fd_path.read_text(encoding="utf-8")
                from .distiller import load_section as _ls
                _story_parts = []
                for _sn, _cap in (("故事库", 3000), ("方法与清单", 2000),
                                   ("成功路径", 1500), ("概念与机制", 2000)):
                    _t = _ls(book.book_title, _sn)
                    if _t:
                        _story_parts.append(_t[:_cap])
                _story_mat = "\n\n".join(_story_parts) if _story_parts else (inp.get("全书核心主张") or {}).get("value", "")
                # 1018 C2 (R1): 补书名/作者 — 旧版无此字段, fdopen 凭书内容意译书名
                # 《被信任的专业顾问》(ep1 01_fd_draft.txt 第3段实锤)。
                _fd_user = (f"【书名】《{book.book_title}》(提及一律用此名, 禁意译)\n"
                            f"【作者】{book.author or '未知'}\n"
                            f"【本集】第{ep.ep_index}集 | {ep.title or ''}\n"
                            f"秘籍: {_rm_hint(ep)}\n\n【书的蒸馏材料】\n{_story_mat}")
                _fd_out = _llm().chat(_fd_sys, _fd_user, model="pro", temperature=0.6, timeout=600, tag="epgen:fdopen")
                logger.info("[book] 樊登讲故事完成: %d 字", len(_fd_out))
        except Exception as exc:
            logger.warning("[book] 樊登步骤跳过: %s", exc)
            _fd_out = ""
    if _fd_out:
        # 1018 C2 (R1): 拐棍双真相源仲裁 — 拐棍稿内书名/作者与 user_p 顶部分义时, 以顶部为准
        user_p = (f"【樊登讲述参考 (拐棍 — 保持故事质量: 场景/对话/心理变化/流畅口语), "
                  f"但用你老谭的方式重讲: 你的决策场景/金句/工具/互动照常加。"
                  f"拐棍稿内如出现书名/作者与本提示词顶部【书名】【作者】不一致, 一律以顶部为准】\n"
                  f"{_fd_out[:6000]}\n\n"
                  f"---\n{user_p}")

    # ── 生成留档 (0912 用户令: 每层留档, 改提示词→看效果要有证据链) ──
    # outputs/拆书/{书}/ep{N}/gen_{ts}/: 分层快照 + meta。只写盘不改流程, 失败不阻断生成。
    import time as _time
    _book_dir = re.sub(r'[\\/:*?"<>|\s]+', "_", book.book_title).strip("_") or "unnamed"
    _gen_dir = (Path(__file__).resolve().parents[3] / "outputs" / "拆书" / _book_dir
                / f"ep{ep.ep_index}" / _time.strftime("gen_%Y%m%d_%H%M%S"))
    try:
        _gen_dir.mkdir(parents=True, exist_ok=True)
        (_gen_dir / "01_fd_draft.txt").write_text(_fd_out or "(未跑/跳过)", encoding="utf-8")
        (_gen_dir / "02_sys.txt").write_text(sys_p, encoding="utf-8")
        (_gen_dir / "03_user.txt").write_text(user_p, encoding="utf-8")
        # 0913 料包台账 (feed_registry): 每包实喂字数落盘, 反查零人肉
        try:
            import json as _json

            from .feed_registry import feed_ledger
            (_gen_dir / "03_feeds.json").write_text(
                _json.dumps({"total": len(user_p), "feeds": feed_ledger(user_p)},
                            ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
        logger.info("[book] 生成留档: %s", _gen_dir)
    except Exception as _exc:
        logger.warning("[book] 生成留档失败 (不阻断): %s", _exc)

    out = _llm().chat(sys_p, user_p, model="pro", temperature=0.8, tag="epgen:draft")
    try:
        (_gen_dir / "04_raw_main.txt").write_text(out, encoding="utf-8")
    except Exception:
        pass

    m = re.search(r"\{[\s\S]*\"coverage\"[\s\S]*\}\s*$", out)
    coverage: list = []
    if m:
        try:
            coverage = _parse_json(m.group(0)).get("coverage") or []
            out = out[:m.start()].rstrip()
        except Exception:
            pass

    # 0913 打字卡出稿 (用户令: 打字卡=视频层0-2s静音打字效果, 禁入口播稿 — TTS会念):
    # 首行「打字卡：…」确定性摘出 → roadmap_json.打字卡 (导演步/视频层取), 校验前摘=字数不带它算
    _card_txt = ""  # 本次出稿的打字卡 (空=本稿未出卡行, 陈旧 roadmap 卡不算数)
    _mcard = re.match(r"\s*打字卡[：:]\s*(.+?)(?:\n|$)", out)
    if _mcard:
        _card_txt = _mcard.group(1).strip()
        out = out[:_mcard.start()] + out[_mcard.end():]
        out = out.lstrip("\n")
        _rm_card = dict(ep.roadmap_json or {})
        _rm_card["打字卡"] = _card_txt
        ep.roadmap_json = _rm_card
        logger.info("[book] 打字卡出稿→roadmap_json.打字卡 (视频层用, 不入口播): %s", _card_txt[:40])
        # 书级无卡时冻结为系列固定卡 — 骨架合规才冻 (0914 守门: 自产卡也须三件套,
        # 旧版只查长度把不合规卡冻进去的实锤修复)
        if not (inp.get("系列打字卡") or {}).get("card") and not typing_card_issues(_card_txt):
            inp = dict(inp)
            inp["系列打字卡"] = {"card": _card_txt, "tier": "L0"}
            book.input_json = inp

    # 硬校验: 不达标带 issues 重试一次; 灵性金句未锚定也触发重写 (2026-08-21)
    # 0912 概念词频检 (车轱辘机器层): 本集核心概念名 >6 次 = 论点复读, 确定性打回
    # 0913 CTA 集间差异化: 老谭非收官集收尾段须有现造转发/收藏 CTA (系统结尾只剩品牌句)
    _req_cta = _is_laotan and not (ep.ep_index >= _total_eps and _total_eps > 1)
    _concept_issues: list[str] = []
    for _cw in [str(c) for c in (_rm.get("概念") or []) if str(c)] or [str(_rm.get("主题") or "")[:4]]:
        _cw_n = out.count(_cw)
        if _cw_n > 6:
            _concept_issues.append(f"概念「{_cw}」出现 {_cw_n} 次 >6 (论点复读=车轱辘, 立论+收尾点题外禁再提)")
    # 0930 贯穿人物出场配额 (用户实锤"反复讲太啰嗦") — 实现迁 passes.fig_quota_issues
    # (快照/去重触发/终稿复检共用); 闸必须挂每层 (首版只挂首稿, 终稿5次无issue实锤)
    def _fig_quota(text: str) -> list[str]:
        return passes_mod.fig_quota_issues(text, _figure)
    _concept_issues.extend(_fig_quota(out))
    # 1018 C4c: 黄金三秒钩子落位闸 — 工具集/收官集才查 (ep1 豁免: 六拍+打字卡扛前3秒)
    _hook_ref = str(_rm.get("黄金三秒钩子") or "") if ep.ep_index > 1 else ""
    _concept_issues.extend(hook_position_issues(out, _hook_ref))
    ok, issues = validate_script(out, ep.target_duration_sec, book_title=book.book_title,
                                 require_cta=_req_cta)
    # 六拍模块总线 (0917 架构令): 标记解析不足 6 段 = 结构不合规, 进打回链自动重试
    from .module_map import parse_modules
    if len(parse_modules(out)) < 6:
        issues = list(issues) + [f"六拍标记解析仅 {len(parse_modules(out))} 段 (需 6) — 模块总线断"]
        ok = False
    # 樊登开局逐字段校验 (ep1 拼装式开局): 前三段一字不改, 缺段/改写打回
    _fd_open_chk = fandeng_opening_issues(out, _fd_open_paras)
    if _fd_open_chk:
        issues = list(issues) + _fd_open_chk
        ok = False
    # ep1 开场秩序 (0914 六拍+钩子后移): 打字卡骨架/冻结一致 + 报书名先于逐字段
    if ep.ep_index == 1:
        _persona_chk = persona_anchor_issues(out)
        if _persona_chk:
            issues = list(issues) + _persona_chk
            ok = False
        _card_out = _card_txt
        _ord_chk = (typing_card_issues(_card_out, _card)
                    + ep1_opening_order_issues(out, book.book_title, _fd_open_paras))
        if _ord_chk:
            issues = list(issues) + _ord_chk
            ok = False
    # 0913b 段间承接检测 (确定性): 拆解块首句路标/咬合 + 工具名配额 + 系列句配额 —
    # 句级校验管不到的语篇层, 割裂稿在这里打回
    _flow_issues = section_flow_issues(out, book_title=book.book_title, ep_index=ep.ep_index,
                                        flow_check=_is_tool_ep)
    if _flow_issues:
        issues = list(issues) + _flow_issues
        ok = False
    if _concept_issues:
        issues = _concept_issues + list(issues)
        ok = False
    # 0926 硬核层执法: 中国品牌案例视角硬指标 (ep2 零中国案例多连实锤 — 宪法+user
    # 注入+编辑层全链路都可能丢, 进确定性闸与字数闸同族, 首稿/重试/编辑后三点齐查)
    if _is_tool_ep and _zh_brand_issues(out):
        issues = list(issues) + _zh_brand_issues(out)
        ok = False
    occult_issues = _scan_occult_quotes(out)
    if occult_issues:
        issues = list(issues) + occult_issues
        ok = False
    if not ok:
        retry = _llm().chat(
            sys_p, user_p + f"\n\n上一版问题，请修正：{'; '.join(issues)}",
            model="pro", temperature=0.6, tag="epgen:retry")
        try:
            (_gen_dir / "05_raw_retry.txt").write_text(
                f"issues: {issues}\n\n{retry}", encoding="utf-8")
        except Exception:
            pass
        m2 = re.search(r"\{[\s\S]*\"coverage\"[\s\S]*\}\s*$", retry)
        if m2:
            try:
                coverage = _parse_json(m2.group(0)).get("coverage") or coverage
                retry = retry[:m2.start()].rstrip()
            except Exception:
                pass
        # 0913 打字卡出稿 (重试稿同规): 卡更新进 roadmap, 稿内删除
        _mcard2 = re.match(r"\s*打字卡[：:]\s*(.+?)(?:\n|$)", retry)
        if _mcard2:
            _card2 = _mcard2.group(1).strip()
            retry = retry[:_mcard2.start()] + retry[_mcard2.end():]
            retry = retry.lstrip("\n")
            _rm2 = dict(ep.roadmap_json or {})
            _rm2["打字卡"] = _card2
            ep.roadmap_json = _rm2
        out2_ok, issues2 = validate_script(retry, ep.target_duration_sec, book_title=book.book_title,
                                           require_cta=_req_cta)
        # 0926 硬核层: 中国品牌闸在重试稿同规 (ep2 实锤: 品牌在重试轮进了、其它校验
        # 未全过回退首稿时被丢弃 → 终稿零品牌)
        if _zh_brand_issues(retry, book.book_title):
            issues2 = list(issues2) + _zh_brand_issues(retry, book.book_title)
        # 0913b: 重试稿承接检测同规 (重试只顾字数/车轱辘、割裂照旧 = 白重试)
        _flow2 = section_flow_issues(retry, book_title=book.book_title, ep_index=ep.ep_index,
                                     flow_check=_is_tool_ep)             + fandeng_opening_issues(retry, _fd_open_paras)
        issues2 = list(issues2) + _flow2 + hook_position_issues(retry, _hook_ref)
        # 1018 C1a (北斗星源头闸): 重试全有全无 → 病族不新增 且 字数距带不倒退 即接受;
        # 残余病 (软带字数/CTA/复读) 交给后置 pass 收尾 — ep1 实锤: 重试修好 5/6 病
        # (书名✓CTA✓钩子秩序✓) 因字数爆 2728 被整稿弃用, 修好的全被扔 (05_raw_retry.txt)。
        # 验收复用 passes 现成 family_new + word_band_ok (与 CTA/去重 pass 同判据)。
        _rn_all = list(issues2) + _scan_occult_quotes(retry)
        _rn_new = passes_mod.family_new(_rn_all, list(issues))
        _rn_wc_ok = (not any("字数" in i for i in _rn_all)
                     or passes_mod.word_band_ok(_rn_all, list(issues), ep.target_duration_sec))
        if (out2_ok or (not _rn_new and _rn_wc_ok)) and not _scan_occult_quotes(retry):
            if not out2_ok:
                logger.info("[book] ep%d 重试按病族放宽接受 (残余=%s, 后置 pass 接手)",
                            ep.ep_index, [i[:24] for i in _rn_all][:4])
            out = retry
        else:
            logger.warning("[book] ep%d 硬校验二次仍不过: %s", ep.ep_index, issues2)
            # 0926 硬核层: 品牌闸是废稿级, 高于其它 polish 级校验 — 重试稿有中国
            # 品牌而首稿没有时, 首稿已经输在更硬的规则上, 选重试稿 (ep2 实锤:
            # 首稿零品牌存活到终稿)
            if _is_tool_ep and _zh_brand_issues(out, book.book_title) \
                    and not _zh_brand_issues(retry, book.book_title):
                out = retry
                logger.info("[book] 品牌闸优先: 重试稿(含中国品牌)替换首稿(零品牌)")

    # 0926 品牌注入 pass (硬核层终局兜底): 首稿+重试双双零中国品牌时 (ep2 第四连实锤
    # — 重试是单掷, 修了字数丢了品牌是采样方差), 定向微注射而非全文重写:
    # 在拆解段嵌一句案例库候选的中国品牌证据句, 禁动其它句子。
    if _is_tool_ep and _zh_brand_issues(out, book.book_title):
        try:
            _cands = ""
            try:
                from app.services.book_service.l0 import _l0_dir as _l0d
                _bp = _l0d(book.book_title).parent / "案例库.json"
                if _bp.exists():
                    _cs = [c for c in (json.loads(_bp.read_text(encoding="utf-8")).get("cases") or [])
                           if c.get("status") == "可用"]
                    # 0928: 候选放开全量 — 旧 [:8] 把排在库尾的字节/华为两案永远挡在
                    # 候选外 (ep2 实锤: 8 个不相关旧案 → 模型原稿退回 长度差=0)
                    _cands = "\n".join(f"- {c['name']} ({c.get('role')}): "
                                       f"{'；'.join(c.get('facts') or [])[:120]}" for c in _cs)
            except Exception:  # noqa: BLE001
                _cands = ""
            # 1009 P5: 行级 patch 优先 (未动行零漂移), 失败回退全稿
            inj = _llm().edit_patch(
                out,
                "你是口播稿植入编辑。任务: 只在稿件的【核心概念拆解】段内嵌入 1-2 句"
                "中国品牌案例证据句 (从候选案例选, 只用候选里给出的事实口径, 禁编数字), "
                "句内必带 当年/早期/公开报道 限定词, 只述管理动作不评企业好坏, "
                "位置=紧贴它支撑的那个论断 (改写该行, 把证据句并入); 同时把稿内出现的"
                "英文品牌名 (如 Saturn) 改成中文说法。禁动段落标签与⟦⟧原话, 总字数变化 ≤60。",
                f"\n候选案例:\n{_cands or '(缺库 — 从公共商业史提名: 美团/蒙牛/滴滴/支付宝等国民级)'}",
                model="pro", max_tokens=2000, tag="epgen:inject")
            if inj is None:
                inj = _llm().chat(
                    "你是口播稿植入编辑。任务: 只在稿件的【核心概念拆解】段内嵌入 1-2 句"
                    "中国品牌案例证据句 (从候选案例选, 只用候选里给出的事实口径, 禁编数字), "
                    "句内必带 当年/早期/公开报道 限定词, 只述管理动作不评企业好坏, "
                    "位置=紧贴它支撑的那个论断; 同时把稿内出现的英文品牌名 (如 Saturn) 改成"
                    "中文说法或删除。铁律: 禁改其它任何句子、禁动段落标签、禁动⟦⟧原话、"
                    "总字数变化 ≤60。直接输出改后的完整稿。",
                    f"候选案例:\n{_cands or '(缺库 — 从公共商业史提名: 美团/蒙牛/滴滴/支付宝等国民级)'}\n\n稿件:\n{out}",
                    model="pro", temperature=0.3, tag="epgen:inject")
            inj = (inj or "").strip()
            if inj and not _zh_brand_issues(inj, book.book_title) and abs(len(inj) - len(out)) <= 80:
                out = inj
                logger.info("[book] ep%d 品牌注入 pass ✓ (%d→%d字)", ep.ep_index, len(out), len(inj))
                try:
                    (_gen_dir / "05c_brand_injected.txt").write_text(out, encoding="utf-8")
                except Exception:
                    pass
            else:
                logger.warning("[book] ep%d 品牌注入 pass 未过 (含品牌=%s, 长度差=%d), 人闸留意",
                               ep.ep_index, not _zh_brand_issues(inj, book.book_title) if inj else False,
                               abs(len(inj or "") - len(out)))
                try:  # 1018 C5: 未过也留档 (R7)
                    (_gen_dir / "05c_brand_injected_rejected.txt").write_text(
                        f"未过原因: 含品牌={not _zh_brand_issues(inj, book.book_title) if inj else False}, "
                        f"长度差={abs(len(inj or '') - len(out))}\n\n{inj or '(空返回)'}", encoding="utf-8")
                except Exception:
                    pass
        except Exception as _exc8:  # noqa: BLE001
            logger.warning("[book] ep%d 品牌注入 pass 异常: %s", ep.ep_index, _exc8)

    # 0928 口播卫生定向修 pass (用户实锤"书里写"成口头禅 + 判定钉黑话渗稿): 校验器抓到
    # 而单掷重试常修不完 — 定向替换非全文重写, 品牌注入 pass 同架构 (只动病灶句)
    if _is_tool_ep:
        _hyg = [i for i in validate_script(out, ep.target_duration_sec,
                                           book_title=book.book_title, require_cta=_req_cta)[1]
                if "归因句堆叠" in i or "内部黑话渗稿" in i]
        if _hyg:
            try:
                # 1009 P5: 行级 patch 优先, 失败回退全稿
                fix = _llm().edit_patch(
                    out,
                    "你是口播稿卫生编辑。只做两类替换, 禁改其它任何句子、禁动段落标签、禁动⟦⟧原话: "
                    "①'书里写/书里说/书里警告'类起手只保留直引锚段处的 (全篇≤3处), 其余换成"
                    "互不相同的变奏 (德鲁克举了个例子 / 书里那位X / 按德鲁克的诊断 / 这在书里叫…, "
                    "同款变奏禁连用); ②管线黑话 (判定钉/反馈钉/指向陈述等) 说成人话。"
                    "总字数变化 ≤40。",
                    "\n待修问题:\n" + "\n".join(_hyg),
                    model="pro", max_tokens=2000, tag="epgen:hygiene")
                if fix is None:
                    fix = _llm().chat(
                        "你是口播稿卫生编辑。只做两类替换, 禁改其它任何句子、禁动段落标签、禁动⟦⟧原话: "
                        "①'书里写/书里说/书里警告'类起手只保留直引锚段处的 (全篇≤3处), 其余换成"
                        "互不相同的变奏 (德鲁克举了个例子 / 书里那位X / 按德鲁克的诊断 / 这在书里叫…, "
                        "同款变奏禁连用); ②管线黑话 (判定钉/反馈钉/指向陈述等) 说成人话。"
                        "总字数变化 ≤40。直接输出改后的完整稿。",
                        "待修问题:\n" + "\n".join(_hyg) + f"\n\n稿件:\n{out}", model="pro", temperature=0.3, tag="epgen:hygiene")
                fix = (fix or "").strip()
                _hyg2 = [i for i in validate_script(fix, ep.target_duration_sec,
                                                    book_title=book.book_title, require_cta=_req_cta)[1]
                         if "归因句堆叠" in i or "内部黑话渗稿" in i]
                if fix and not _hyg2 and abs(len(fix) - len(out)) <= 80 \
                        and not _zh_brand_issues(fix, book.book_title):
                    out = fix
                    logger.info("[book] ep%d 口播卫生 pass ✓ (归因/黑话清零)", ep.ep_index)
                    try:
                        (_gen_dir / "05d_hygiene.txt").write_text(out, encoding="utf-8")
                    except Exception:
                        pass
                else:
                    logger.warning("[book] ep%d 口播卫生 pass 未过, 人闸留意: %s", ep.ep_index, _hyg2)
                    try:  # 1018 C5: 未过也留档 (R7)
                        (_gen_dir / "05d_hygiene_rejected.txt").write_text(
                            f"未过原因: {_hyg2}\n\n{fix or '(空返回)'}", encoding="utf-8")
                    except Exception:
                        pass
            except Exception as _exc9:  # noqa: BLE001
                logger.warning("[book] ep%d 口播卫生 pass 异常: %s", ep.ep_index, _exc9)

    # 0929e 收尾修复双 pass (生稿顽疾: 转发CTA五连缺/系列句双现/复读段反复 — 合并修
    # 实锤"治复读造出新复读" → 拆开: CTA=纯追加近零风险必落地, 去重=合并且严闸;
    # 均在幽默 pass 前清场)
    # 0930 快照 (passes.py): CTA+去重共享 pre-CTA 基线 (压测实锤 — 各自重算会漂)
    _fx_base = passes_mod.snap_issues(passes_mod.snapshot(
        out, target=ep.target_duration_sec, book_title=book.book_title, require_cta=_req_cta,
        ep_index=ep.ep_index, flow_check=_is_tool_ep, figure=_figure,
        include=("validate", "flow", "fig")))
    if _is_laotan and any("缺转发CTA" in i or "缺收藏CTA" in i for i in _fx_base):
        try:
            # 1009 P5: 行级 patch 优先; 1018 C1b 拆指令/闸错位 (R3b) — 旧版只让加
            # "一句转发号召", 验收却要转发+收藏双 CTA (过闸全靠模型顺手=采样运气,
            # ep1 实锤未过); 且"理由带工具名全称"撞 6-gram 复读检测+工具名≤2配额
            # (模板旧例'这张三面墙定位表'自身即会触发) → 统一改回指; 1018 视听一致:
            # 画面无表格, 回指禁'表'字 ('这套问话/这三句'式)。
            cta = _llm().edit_patch(
                out,
                "你是口播稿CTA编辑。任务只有一个: 在收尾段内、以'下一集/下集/下期'开头的"
                "下集预告行之前, 插入现造的转发号召与收藏号召 (各一句) — 把该预告行改为"
                "'转发句\\n收藏句\\n原预告行原样保留在最后', 其余任何行禁改、禁动段落标签与⟦⟧原话。"
                "转发句: 对象=本集秘籍对应的痛点人群, 理由用本集工具的**回指** ('这套问话'/"
                "'这套问话'式, 禁工具名全称《…》再出现, 禁'转给那个…的人'句式); "
                "收藏句: 给场景理由 ('下次…之前翻出来过一遍'式, 挂钩本集工具使用场景)。"
                "两句都禁复用稿内任何已有连续6字 (复读检测会打回)。",
                "", model="pro", max_tokens=1500, tag="epgen:cta")
            if cta is None:
                cta = _llm().chat(
                    "你是口播稿CTA编辑。任务只有一个: 在收尾段内、下集预告行之前, 追加现造的"
                    "转发号召与收藏号召各一句。转发句: 对象=本集秘籍对应的痛点人群, 理由用"
                    "本集工具的**回指** ('这套问话'式, 禁工具名全称《…》再出现, 禁'转给那个…的人'句式); "
                    "收藏句: 给场景理由 ('下次…之前翻出来过一遍'式, 挂钩本集工具使用场景)。"
                    "两句都禁复用稿内任何已有连续6字 (复读检测会打回)。"
                    "铁律: 只在收尾段内追加这两句, 禁改稿内任何已有句子、禁动段落标签与⟦⟧原话。"
                    "直接输出追加后的完整稿。",
                    "稿件:\n" + out, model="pro", temperature=0.4, tag="epgen:cta")
            cta = (cta or "").strip()
            _cta_after = passes_mod.snap_issues(passes_mod.snapshot(
                cta, target=ep.target_duration_sec, book_title=book.book_title,
                require_cta=_req_cta, include=("validate",)))
            _cta_new = passes_mod.family_new(_cta_after, _fx_base)
            if (cta and not any("缺转发CTA" in i or "缺收藏CTA" in i for i in _cta_after)
                    and not _cta_new and 0 <= len(cta) - len(out) <= 130):
                out = cta
                logger.info("[book] ep%d CTA 追加 pass ✓", ep.ep_index)
                try:
                    (_gen_dir / "05f_cta.txt").write_text(out, encoding="utf-8")
                except Exception:
                    pass
            else:
                logger.warning("[book] ep%d CTA 追加 pass 未过 (新问题=%s), 人闸留意",
                               ep.ep_index, _cta_new)
                try:  # 1018 C5: 未过也留档 — 裸启动无日志时 verdict 不丢 (R7)
                    (_gen_dir / "05f_cta_rejected.txt").write_text(
                        f"未过原因: 新问题={_cta_new}\n\n{cta or '(空返回)'}", encoding="utf-8")
                except Exception:
                    pass
        except Exception as _exc11:  # noqa: BLE001
            logger.warning("[book] ep%d CTA 追加 pass 异常: %s", ep.ep_index, _exc11)
    _dedup_trig = [i for i in _fx_base if any(k in i for k in ("系列句", "复读段", "车轱辘话", "贯穿人物"))]
    if _is_laotan and _dedup_trig:
        try:
            # 1009 P5: 行级 patch 优先, 失败回退全稿
            # 1018 C1d 解禁 (R3c): 删"以改代删禁越修越短" — 双开篇级重复段 156 字,
            # 旧令 Δ≤60 永远删不动 (ep1 实锤 + ep4/ep7 日志"去重未过 剩2/2"连发);
            # 跨⟦⟧锁段与非锁段的重复 → 裁非锁段那处; 字数交给其后的压缩 pass 兜底。
            fix = _llm().edit_patch(
                out,
                "你是口播稿去重编辑。只修列出的问题, 禁改其它任何句子、禁动段落标签、"
                "禁动⟦⟧原话: ①系列句出现2次 → 总结段保留行动册预告原句, 回顾段那处改成"
                "'第X件工具'式说法; ②复读段/车轱辘话 → 优先把重复的两句合并成一句更满的; "
                "整段级重复 (同一信息两遍介绍) → 整删其中一处, 重复信息跨⟦⟧锁段与非锁段时"
                "只删/改**非锁段**那处 (锁段一字不动); ③贯穿人物出场超额 → 合并到职能配额: "
                "钩子只留反差结论两三句, 完整故事只在中段讲一次, 拆解三改成拿工具"
                "拿工具量化 (维度/刻度/判定, 禁重述故事), 总结只留身份重述一句。"
                "总字数变化 ≤250。",
                "\n待修问题:\n" + "\n".join(_dedup_trig),
                model="pro", max_tokens=2500, tag="epgen:dedup")
            if fix is None:
                fix = _llm().chat(
                    "你是口播稿去重编辑。只修列出的问题, 禁改其它任何句子、禁动段落标签、"
                    "禁动⟦⟧原话: ①系列句出现2次 → 总结段保留行动册预告原句, 回顾段那处改成"
                    "'第X件工具'式说法; ②复读段/车轱辘话 → 优先合并成一句更满的; 整段级重复"
                    "→ 整删非锁段那处 (⟦⟧锁段一字不动); ③贯穿人物出场超额 → 合并到职能配额: "
                    "钩子只留反差结论两三句, 完整故事只在中段讲一次, 拆解三改成拿工具"
                    "拿工具量化 (维度/刻度/判定, 禁重述故事), 总结只留身份重述一句。"
                    "总字数变化 ≤250。直接输出改后的完整稿。",
                    "待修问题:\n" + "\n".join(_dedup_trig) + "\n\n稿件:\n" + out,
                    model="pro", temperature=0.3, tag="epgen:dedup")
            fix = (fix or "").strip()
            _dd_after = passes_mod.snap_issues(passes_mod.snapshot(
                fix, target=ep.target_duration_sec, book_title=book.book_title,
                require_cta=_req_cta, ep_index=ep.ep_index, flow_check=_is_tool_ep,
                figure=_figure, include=("validate", "flow", "fig")))
            _dd_left = [i for i in _dd_after if any(k in i for k in ("系列句", "复读段", "车轱辘话", "贯穿人物"))]
            _dd_new = passes_mod.family_new(_dd_after, _fx_base)
            _b_fig = next((i for i in _fx_base if "贯穿人物" in i), None)
            _a_fig = next((i for i in _dd_after if "贯穿人物" in i), None)
            _fig_improved = passes_mod.fig_better(_a_fig, _b_fig)
            if (fix and (len(_dd_left) < len(_dedup_trig) or _fig_improved) and not _dd_new
                    and abs(len(fix) - len(out)) <= 250
                    and not (_is_tool_ep and _zh_brand_issues(fix, book.book_title))):
                out = fix
                logger.info("[book] ep%d 去重 pass ✓ (%d→%d项)", ep.ep_index,
                            len(_dedup_trig), len(_dd_left))
                try:
                    (_gen_dir / "05g_dedup.txt").write_text(out, encoding="utf-8")
                except Exception:
                    pass
            else:
                logger.warning("[book] ep%d 去重 pass 未过 (剩%d/%d, 新增%s), 人闸留意",
                               ep.ep_index, len(_dd_left), len(_dedup_trig), _dd_new)
                try:  # 1018 C5: 未过也留档 (R7)
                    (_gen_dir / "05g_dedup_rejected.txt").write_text(
                        f"未过原因: 剩{len(_dd_left)}/{len(_dedup_trig)}, 新增={_dd_new}\n\n{fix or '(空返回)'}",
                        encoding="utf-8")
                except Exception:
                    pass
        except Exception as _exc12:  # noqa: BLE001
            logger.warning("[book] ep%d 去重 pass 异常: %s", ep.ep_index, _exc12)

    # 1018 C1c 压缩pass重定位 (R3a, 北斗星4裁决序): 从重试else分支移出 — 旧位触发
    # 要求重试稿"只剩字数族" (多病族永不触发), 且验收 require_cta 全过但指令"禁新增"
    # 无法补CTA = DOA。新位=去重之后/幽默之前 (去重先并重复段, 压缩最后兜底收带 —
    # 去重若在压缩后, 删 156 字重复段必破带下限, ep1 双开篇实锤; CTA 已在稿内受禁删令保护)。
    # 触发放宽为"当前稿出软带即压"; 验收=字数落带(或距带更近)+病族不新增
    # (require_cta=False — CTA 缺失归 CTA pass 管, 压缩只删不加不背别家的闸)。
    _cpr_base = passes_mod.snap_issues(passes_mod.snapshot(
        out, target=ep.target_duration_sec, book_title=book.book_title,
        require_cta=False, ep_index=ep.ep_index, flow_check=_is_tool_ep, figure=_figure,
        include=("validate", "flow", "fig")))
    _cpr_wc = next((i for i in _cpr_base if i.startswith("字数")), "")
    if ("超出" in _cpr_wc or "超硬线" in _cpr_wc):
        _wb7 = word_band(float(ep.target_duration_sec or 300))
        _tgt7 = (_wb7[0] + _wb7[1]) // 2
        try:
            # 1009 P5 (设计§七): 先走行级 patch — 未动行由代码逐字保留
            # (段落标签/coverage JSON 零漂移是构造保证), 失败回退全稿重写
            comp = _llm().edit_patch(
                out,
                "你是口播稿压缩师。只删不改, 按可牺牲度从高到低删 (六层裁决序链底向上): "
                "人设修饰(口头禅/自指) → 案例次要细节(保主案例骨架) → 趣味修饰/连接词; "
                "禁删: 黄金三秒钩子断言、转发/收藏CTA、讲解骨架(立论/判定/步骤)、"
                "⟦⟧原话、段落标签、数字锚(年份/倍数/百分比)。"
                f"把稿压到 ≤{_tgt7}字 (现超软带)。删行用空串或整行压缩, 禁新增任何内容。",
                "", model="pro", max_tokens=3000, tag="epgen:compress")
            if comp is None:
                comp = _llm().chat(
                    "你是口播稿压缩师。只删不改, 按可牺牲度从高到低删: 人设修饰 → "
                    "案例次要细节 → 趣味修饰/连接词; 禁删: 钩子断言、转发/收藏CTA、"
                    "讲解骨架、⟦⟧原话、段落标签、数字锚。"
                    f"把稿压到 ≤{_tgt7}字 (现超软带)。直接输出压缩后全稿。",
                    out, model="pro", temperature=0.3, tag="epgen:compress")
            comp = (comp or "").strip()
            _cpr_after = passes_mod.snap_issues(passes_mod.snapshot(
                comp, target=ep.target_duration_sec, book_title=book.book_title,
                require_cta=False, ep_index=ep.ep_index, flow_check=_is_tool_ep, figure=_figure,
                include=("validate", "flow", "fig")))
            _cpr_new = passes_mod.family_new(_cpr_after, _cpr_base)
            _cpr_over_left = any(("超出" in i or "超硬线" in i) for i in _cpr_after if i.startswith("字数"))
            _cpr_in = (not any(i.startswith("字数") for i in _cpr_after)
                       or passes_mod.word_band_ok(_cpr_after, _cpr_base, ep.target_duration_sec))
            if comp and _cpr_in and not _cpr_new and not _scan_occult_quotes(comp):
                out = comp
                logger.info("[book] ep%d 压缩pass ✓ (落带/距带更近, 病族不增)", ep.ep_index)
                try:
                    (_gen_dir / "05b_compressed.txt").write_text(comp, encoding="utf-8")
                except Exception:
                    pass
            else:
                logger.warning("[book] ep%d 压缩pass 未过 (字数仍出带=%s, 新病族=%s), 人闸留意",
                               ep.ep_index, _cpr_over_left, _cpr_new)
                try:  # 1018 C5: 未过也留档 (R7)
                    (_gen_dir / "05b_compressed_rejected.txt").write_text(
                        f"未过原因: 字数仍出带={_cpr_over_left}, 新病族={_cpr_new}\n\n{comp or '(空返回)'}",
                        encoding="utf-8")
                except Exception:
                    pass
        except Exception as _exc7:
            logger.warning("[book] ep%d 压缩pass 异常: %s", ep.ep_index, _exc7)

    # 0929 幽默 pass (用户令: 生稿回原方法, 轻松剂独立成遍 — 六律进 104 禁墙后两连零
    # 笑实锤, 中段稀释不可治, 拆出来单独喂): 定向加笑微编辑, 品牌注入/口播卫生同架构
    # (只动说法不动骨架); 验收闸=不引入新问题 (与改前稿对比), 非绝对达标
    if _is_laotan:
        try:
            # 0929d 脱口秀版提示词 (用户令): 变量随稿件注入 — 主题=本集路线图主题,
            # 幽默浓度默认 4 (高风险自动降 2 由提示词自治); 额外素材/禁止未配先不注
            _humor_topic = str((ep.roadmap_json or {}).get("主题") or ep.title or "")
            _humor_user = (f"主题：{_humor_topic}\n受众：24-40 岁一线职场人\n"
                           f"幽默浓度：4\n\n稿件：\n{out}")
            hum = _llm().chat(_load_prompt_template("laotan-book-humor"), _humor_user,
                              model="pro", temperature=0.75, tag="epgen:humor")
            hum = (hum or "").strip()
            # 0929f 防空转重试: 模型偶发原样退还输入稿 (两连实锤), 提温重催一次
            if not hum or hum == out:
                hum = (_llm().chat(
                    _load_prompt_template("laotan-book-humor")
                    + "\n\n上一版你原样返回了输入稿 = 未干活。重做: 按首要硬指标改写 ≥5 处笑点,"
                      "输出改后的完整全稿。",
                    _humor_user, model="pro", temperature=0.9, tag="epgen:humor_retry") or "").strip()
            if not hum or hum == out:
                logger.warning("[book] ep%d 幽默 pass 空转 (返回%s), 人闸留意",
                               ep.ep_index, "空" if not hum else "原稿原样")
            else:
                # 0930 快照+共享闸判据 (passes.py): 幽默基线不含 fig (人物配额归
                # 去重 pass 管 — 压测实锤语义差异); 病族/字数带助手见 passes 文档
                _hs = dict(target=ep.target_duration_sec, book_title=book.book_title,
                           require_cta=_req_cta, ep_index=ep.ep_index, flow_check=_is_tool_ep)
                _b_snap = passes_mod.snapshot(out, include=("validate", "flow"), **_hs)
                _h_snap = passes_mod.snapshot(hum, include=("validate", "flow"), **_hs)
                _b_iss = passes_mod.snap_issues(_b_snap)
                _h_iss = passes_mod.snap_issues(_h_snap)
                _new = passes_mod.family_new(_h_iss, _b_iss)
                # 字数豁免双前提: 新问题全为字数族 且 距带更近 (0929b 原语义保真)
                _wc_ok = (all("字数" in i for i in _new)
                          and passes_mod.word_band_ok(_h_iss, _b_iss, ep.target_duration_sec))
                _flow_new = [i for i in _h_snap.get("flow", [])
                             if i not in set(_b_snap.get("flow", []))]
                if (((not _new) or _wc_ok) and not _flow_new
                        and not passes_mod.snap_issues(passes_mod.snapshot(hum, include=("brand",), **_hs))
                        and abs(len(hum) - len(out)) <= (420 if _wc_ok else 240)):
                    out = hum
                    logger.info("[book] ep%d 幽默 pass ✓ (%d→%d字)", ep.ep_index, len(out), len(hum))
                    try:
                        (_gen_dir / "05e_humor.txt").write_text(out, encoding="utf-8")
                    except Exception:
                        pass
                else:
                    logger.warning("[book] ep%d 幽默 pass 未过 (新问题=%s, 长度差=%d), 保留原稿",
                                   ep.ep_index, _new or _flow_new,
                                   abs(len(hum) - len(out)))
                    try:
                        (_gen_dir / "05e_humor_rejected.txt").write_text(
                            f"新问题: {_new or _flow_new}\n\n{hum}",
                            encoding="utf-8")
                    except Exception:
                        pass
        except Exception as _exc10:  # noqa: BLE001
            logger.warning("[book] ep%d 幽默 pass 异常: %s", ep.ep_index, _exc10)

    # 2026-08-23: 编辑层口语化 — 生成层专注创作, 编辑层专职改口语 (身份激活真人口播语感)
    # 0912 合并 (用户令): 独立编辑层调用取消 — 口语化规则已全量在系统栈口播8条, 分层留档
    # 实测编辑层 96% 原样空转 (唯一动作=删 coverage JSON); ⟦⟧原话标记改确定性剥离。
    out = _QUOTE_MARK_RE.sub(lambda m: m.group(1), out)
    # 0913 断言去弱化 (确定性, 不赌模型): 钩子段剥 可能/也许/大概/搞不好/说不定 —
    # 料源「黄金三秒钩子」自带弱化词, 模板禁令拦不住照抄 (ep2 三连实测);
    # 只动钩子段 (叙事段的认知不确定是合法的, 断言段的犹豫才是病)
    _hk = re.search(r"^(【[^】]*】\s*\n)(.*?)(?=\n\s*【|\Z)", out, flags=re.S)
    if _hk:
        _dehedged = re.sub(r"(可能|也许|大概|搞不好|说不定)(恰恰)?", "", _hk.group(2))
        if _dehedged != _hk.group(2):
            out = out[:_hk.start(2)] + _dehedged + out[_hk.end(2):]
            logger.info("[book] 钩子段弱化词已剥离 (确定性)")
    # 1005 风险词降格 (评审实锤, 确定性不赌模型): "操控已有的认知"是《定位》书内核心术语,
    # 口播语境里"操控"带"操纵用户"贬义暗示 — 操控(已有|用户|心智|认知|人心|大众) → 借用;
    # 定位 ep3-7 锚段五连中招实证
    _sw = re.sub(r"操控(?=已有|用户|心智|认知|人心|大众)", "借用", out)
    if _sw != out:
        out = _sw
        logger.info("[book] 风险词降格: 操控→借用 (确定性)")
    try:
        (_gen_dir / "06_editor_out.txt").write_text(out, encoding="utf-8")
    except Exception:
        pass

    # 1005 尾部件序铁律 (用户实锤: 下一集预告乱插位打断上下文, 手动挪每集一次):
    # 收尾段内以"下一集"起头的预告句确定性移到段尾 (贴品牌句 = 全稿最后一行内容);
    # 件序固定: 站队问句 → 微行动 → 转发CTA → 下集预告 → 品牌句(系统注入)
    _lbls = list(re.finditer(r"【[^】]*】\s*\n", out))
    if _lbls:
        _tail = out[_lbls[-1].end():]
        _lines = _tail.split("\n")
        # 1018 C4c: pattern 扩 下集/下期 — 旧版只认"下一集"开头, "下集聊聊"不匹配
        # (ep1 实锤: 站队问句排在下集预告后, 件序铁律未执法)
        _nxt = [i for i, l in enumerate(_lines)
                if l.strip().startswith(("下一集", "下集", "下期"))]
        if _nxt:
            _line = _lines.pop(_nxt[-1])
            while _lines and not _lines[-1].strip():
                _lines.pop()
            _lines.append(_line)
            out = out[:_lbls[-1].end()] + "\n".join(_lines)

    # 人设后处理注入: 身份段放第二段末尾(钩子+回顾之后, 非段首), 固定结尾收尾
    # (2026-09-07 双人物: 身份/结尾按书的 persona 模板分流)
    _identity, _ending = persona_identity_ending(
        _persona_tpl, finale=(_is_laotan and ep.ep_index >= _total_eps and _total_eps > 1),
        ep_word=_cn(_total_eps))
    if _identity not in out:
        # 第3段标签 = 第2段内容结束点 → 身份插在它前面 (钩子+回顾完成后才自报家门)
        label3 = "【%d-%d秒｜%s】" % labels[2] if len(labels) > 2 else None
        if label3 and label3 in out:
            out = out.replace(label3, _identity + "\n" + label3, 1)
        else:
            # 兜底: 插在第二段标签行后
            out = out.replace("【%d-%d秒｜%s】" % labels[1],
                              "【%d-%d秒｜%s】\n%s" % (*labels[1], _identity), 1)
    if _ending not in out:
        # 剥离模型自写的结尾收束语(照顾好自己/一起成长/下期见/山巅相见), 防与系统结尾重复
        import re as _re
        tail = out.rstrip()
        m = _re.search(
            r"[。！？!?\s]*(照顾好自己[^。！？]*[。！？]?|一起成长[^。！？]*[。！？]?|我们?下期见[^。！？]*[。！？]?|咱们?下一?集见[^。！？]*[。！？]?|山巅[相会]?见[^。！？]*[。！？]?)\s*$",
            tail,
        )
        if m and len(tail) - m.start() < 150:
            tail = tail[: m.start()].rstrip()
        out = tail + "\n" + _ending

    # 三项自检 (记录, 不阻塞)
    checks: dict[str, Any] = {}
    # 0929 终稿复检 (修预存显示bug): 首稿 issues 在重试/压缩/注入/卫生/幽默各 pass
    # 换稿后从不清零, UI 一直显示首稿旧账 (ep4 "字数960/缺中国案例"在终稿早已不成立
    # 实锤) — 以终稿为真相源重新校验, 显示与稿一致; 0930 快照化 (validate+flow+fig,
    # 工具集加 brand), validate_script 调用 11→5
    # 1018 C4b (R5): include 补 opening (樊登逐字+ep1开场秩序) — 旧终检不含开局类
    # 检查, 人闸问题单漏报 (ep1 终稿实犯"fd段被改写"但 _issues 不显示)。
    _fin = passes_mod.snapshot(
        out, target=ep.target_duration_sec, book_title=book.book_title, require_cta=_req_cta,
        ep_index=ep.ep_index, flow_check=_is_tool_ep, figure=_figure,
        verbatim_paras=_fd_open_paras, hook_ref=_hook_ref,
        include=("validate", "flow", "fig", "opening", "hook") + (("brand",) if _is_tool_ep else ()))
    issues = passes_mod.snap_issues(_fin)
    ok = not issues
    theme = (ep.roadmap_json or {}).get("主题", "")
    checks["theme_in_script"] = bool(theme) and theme[:8] in out
    # 1018 C4c 记录级 (集型任务卡 ep1 项): 系列导览命中 — 后续集标题头4字在稿率
    if ep.ep_index == 1:
        _guide_eps = [e2 for e2 in book.episodes if e2.ep_index > 1 and (e2.title or "").strip()]
        checks["ep1_guide_hits"] = f"{sum(1 for e2 in _guide_eps if e2.title[:4] in out)}/{len(_guide_eps)}"
    if prev:
        prev_theme = (prev.roadmap_json or {}).get("主题", "")
        checks["links_prev"] = bool(prev_theme) and prev_theme[:8] in out
    if coverage and coverages:
        flat = [c for cs in coverages for c in cs]
        dup = [c for c in coverage if c in flat]
        checks["coverage_dup"] = dup[:5]

    # 六拍模块总线 (0917 架构令): 结构真相源落库 (定稿后重解析, 与最终稿一致) —
    # 正文标签只是人读视图, TTS 模块墙/切场/分镜全取 ep.module_json
    modules = parse_modules(out)
    checks["modules"] = [m["name"] for m in modules]

    ep.script_text = out
    ep.module_json = modules or None
    ep.coverage_json = coverage
    ep.status = "draft"
    ep.script_generated_at = _now()  # 成稿时间 (0912): 测试期分辨各集版本
    # 0912: 新稿基于当前总纲 → 清 stale 标记 (rerun-outline 时打的旧总纲稿警示)
    _rm_out = {k: v for k, v in (ep.roadmap_json or {}).items() if k != "_stale_outline"}
    ep.roadmap_json = {**_rm_out, "_checks": checks, "_issues": issues if not ok else []}
    try:
        (_gen_dir / "07_final.txt").write_text(out, encoding="utf-8")
        (_gen_dir / "meta.json").write_text(json.dumps({
            "ep": ep.ep_index, "title": ep.title, "chars": len(out),
            "issues": issues if not ok else [], "checks": checks,
            "coverage": coverage, "fd_used": bool(_fd_out),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    db.commit()
    return ep


def auto_confirm_if_anim_done(db: Session, ep: Episode) -> bool:
    """1004 用户令: 动画全生成完 → 逐集确认默认自过 (讲书页停在"确认第N集"
    与产线实况脱节, 操作以 API 实况为准)。

    判据: shots.json 存在且非空且全镜 anim_done, 且稿子未在动画之后重生成过
    (重稿 = 人工过稿语义回归, 不自动过)。幂等, 返回是否本次转 confirmed。"""
    if ep.status != "draft" or not ep.script_id:
        return False
    try:
        import json as _json

        from app.services.anim_pipeline.shots import shots_path

        p = shots_path(ep.book.book_title, ep.ep_index)
        if not p.exists():
            return False
        ss = (_json.loads(p.read_text(encoding="utf-8")).get("shots")) or []
        if not ss or not all(s.get("status") == "anim_done" for s in ss):
            return False
        if ep.script_generated_at and p.stat().st_mtime < ep.script_generated_at.timestamp():
            return False  # 稿子在动画之后重生成 → 回人工过稿
    except Exception:  # noqa: BLE001 — 判据失败不炸调用方 (页面照常)
        return False
    ep.status = "confirmed"
    if all(e.status == "confirmed" for e in ep.book.episodes):
        ep.book.status = "done"
    db.commit()
    logger.info("[story] ep%d 动画全生成完 → 逐集确认自动通过 (%s)", ep.ep_index, ep.book.book_title)
    return True


def auto_confirm_by_anim(book_id: str, ep_index: int) -> bool:
    """无会话版 (GPU 批终点调用): 按书ID+集号自动过确认。"""
    from contextlib import closing

    from app.database import get_session_maker

    if get_session_maker() is None:
        return False
    with closing(get_session_maker()()) as db:
        ep = (db.query(Episode).join(BookProject)
              .filter(BookProject.id == book_id, Episode.ep_index == ep_index).first())
        if not ep:
            return False
        return auto_confirm_if_anim_done(db, ep)


def confirm_episode(db: Session, ep: Episode) -> Episode:
    ep.status = "confirmed"
    if all(e.status == "confirmed" for e in ep.book.episodes):
        ep.book.status = "done"
    db.commit()
    return ep


# ── 级联重跑: 重跑 N 作废 N..6 ───────────────────────────────────
def rerun_cascade(db: Session, book: BookProject, ep_index: int) -> BookProject:
    for e in book.episodes:
        if e.ep_index >= ep_index:
            e.status = "pending"
            e.script_text = None
            e.script_generated_at = None
            e.coverage_json = []
    book.status = "writing"
    db.commit()
    return book
