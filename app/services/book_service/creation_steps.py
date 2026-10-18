# -*- coding: utf-8 -*-
"""拆书创作步骤 1-4 + Gate0 — 输入补全 / 评论层 / 素材包 / 多集总纲 / L0 自动填充.

拆包自 orchestrator.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from app.models import BookProject, Episode
from app.services.book_service.creation_common import (
    _CORE_FIELDS,
    _first_list,
    _llm,
    _parse_json,
    source_context,
)
from app.services.book_service.distiller import GemmaClient, load_book_rules
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

__all__ = ["assess_book_risk", "complete_input", "auto_fill_from_l0",
           "build_comment_layer", "build_materials", "build_roadmap", "build_book_cases"]


# ── Gate 0: 蒸馏前置预评估 (2026-08-19, 豆包第4发框架) ────────────
def assess_book_risk(title: str, author: str | None = None,
                     meta: dict | None = None) -> dict:
    """丢书名定档: green/yellow/red + 核心雷区 + 最低安全操作 + 是否放弃.

    防几十分钟蒸馏浪费; 评估失败 fail-open 到 yellow (谨慎) 不阻塞。
    """
    rules = load_book_rules()
    g = rules.get("book_grading") or {}
    sys_p = (
        "你是短视频读书解说前置评估官。按书名/作者/书页简介快速判定适不适合短视频口播解说。"
        f"三档: 🟢低风险={g.get('green', '')}; 🟡中风险={g.get('yellow', '')}; "
        f"🔴建议放弃={g.get('red_abandon', '')}。"
        "铁律: 书能出版≠适合短视频解说; 视频+醒目大字幕=审核最严模式。"
        "输出严格 JSON: {\"tier\":\"green|yellow|red\",\"risk_points\":[str],"
        "\"min_safe_ops\":[str],\"abandon\":bool}"
    )
    user_p = f"书名：《{title}》\n作者：{author or '未知'}"
    if meta and meta.get("blurb"):
        user_p += f"\n内容简介：{(meta.get('blurb') or '')[:800]}"
    try:
        out = get_llm_service().chat(sys_p, user_p, model="pro",
                                                     temperature=0.2, timeout=300)
        a = GemmaClient.parse_json_block(out)
    except Exception as exc:
        logger.warning("[book] 预评估失败 (fail-open yellow): %s", exc)
        return {"tier": "yellow", "risk_points": [], "min_safe_ops": [],
                "abandon": False, "error": str(exc)}
    tier = a.get("tier") if a.get("tier") in ("green", "yellow", "red") else "yellow"
    return {
        "tier": tier,
        "risk_points": [str(x) for x in (a.get("risk_points") or [])][:8],
        "min_safe_ops": [str(x) for x in (a.get("min_safe_ops") or [])][:8],
        "abandon": bool(a.get("abandon")) or tier == "red",
    }


# ── 步骤 1: 输入补全 (flash) ─────────────────────────────────────
def _enrich_meta_fallback(book: BookProject, meta: dict | None) -> dict:
    """书页信息截断/缺失兜底 (2026-08-19): 页面优先, 搜索补简介, txt L0 补目录.

    用户实测部分书页简介截断/目录缺 → 阈值检测后降级补全; 全缺不编 (红线)。
    """
    from app.services.zhipu_search import ZhipuSearchError, zhipu_web_search

    meta = dict(meta or {})
    filled: list[str] = []

    # 简介: 页面 <200 字视为截断 → 搜索取最长 snippet
    if len(meta.get("blurb") or "") < 200:
        try:
            hits = zhipu_web_search(f"{book.book_title} {meta.get('author') or ''} 内容简介 简介", count=4)
            best = max((h.get("content") or "" for h in hits), key=len, default="")
            if len(best) > len(meta.get("blurb") or ""):
                meta["blurb"] = best[:3000]
                filled.append("blurb←搜索")
        except ZhipuSearchError as exc:
            logger.warning("[book] 简介兜底搜索不可用: %s", exc)

    # 目录: 页面 <5 条且 L0 为 txt(章节名干净) → 用 L0 章节名
    if len(meta.get("toc") or []) < 5 and book.source_path:
        try:
            from app.services.book_service.reader import read_book
            parsed = read_book(book.source_path)
            names = [c["name"] for c in parsed["chapters"]
                     if c["name"] != "全文" and len(c["name"]) <= 40][:40]
            if len(names) >= 5:
                meta["toc"] = names
                filled.append("toc←L0章节")
        except Exception as exc:
            logger.warning("[book] 目录兜底失败: %s", exc)

    if filled:
        meta["_gaps_filled"] = filled
    return meta
def complete_input(db: Session, book: BookProject) -> BookProject:
    def _is_laotan_book(b: BookProject) -> bool:
        """老谭读书线判定 (0908): 老谭侧专属字段只对老谭书产出, 静读书零改动."""
        pid = (b.input_json or {}).get("persona_id") if isinstance(b.input_json, dict) else None
        if not pid:
            return False
        try:
            from app.models import Persona
            p = db.query(Persona).filter(Persona.id == pid).first()
            return bool(p and "laotan" in (p.prompt_template or ""))
        except Exception:
            return False

    l0 = source_context(book)
    tier = "L0" if l0 else "L2"
    sys_p = (
        "你是拆书稿创作系统的输入补全模块。基于用户提供的书名/作者"
        + ("和【书籍来源文本】" if l0 else "（无来源文本，凭你的知识）")
        + "，补全创作输入清单。严禁编造：来源没有的字段列入 missing，不得猜测填充。"
        "目标读者画像只从书的内容锁定 (谁的什么处境被这本书直接回答), "
        "禁账号人口学套话。"
        "输出严格 JSON: {\"书籍类型\":str,\"全书核心主张\":str,\"章节结构\":[str],"
        "\"关键概念清单\":[str],\"核心金句\":[str],\"核心案例\":[str],"
        "\"目标读者画像\":str,\"missing\":[str]}"
    )
    user_p = f"书名：《{book.book_title}》\n作者：{book.author or '未知'}"
    # 知海书页 L1 (元数据/简介/目录) — 免搜索; 抓取失败静默降级
    if getattr(book, "source_url", None):
        from app.services.book_service.reader import fetch_zhihailib_meta
        meta = _enrich_meta_fallback(book, fetch_zhihailib_meta(book.source_url))
        if meta:
            pre = dict(book.input_json or {})
            pre["book_meta"] = {k: meta.get(k) for k in
                                ("title", "author", "publisher", "pub_date", "blurb", "toc")}
            book.input_json = pre
            if not book.author and meta.get("author"):
                book.author = meta["author"]
            user_p += (
                f"\n\n【书页信息 L1】作者 {meta.get('author', '')} / 出版社 {meta.get('publisher', '')} / "
                f"出版 {meta.get('pub_date', '')}\n内容简介: {(meta.get('blurb') or '')[:1500]}"
                f"\n目录: {' / '.join((meta.get('toc') or [])[:40])}")
    if l0:
        user_p += f"\n\n【书籍来源文本（节选）】\n{l0}"
    data = _parse_json(_llm().chat(sys_p, user_p, model="flash", temperature=0.3))

    inp = dict(book.input_json or {})
    for k in ("书籍类型", "全书核心主张", "章节结构", "关键概念清单",
              "核心金句", "核心案例", "目标读者画像"):
        v = data.get(k)
        if v not in (None, "", []):
            inp[k] = {"value": v, "tier": tier}
    # 目标读者画像 (2026-08-20): 账号人设级属性, 书级缺省继承 — 不再要求录入时人工补.
    # LLM 给了书级特定画像用之, 否则回退所选 persona.target_reader (0907 双人物).
    if not (inp.get("目标读者画像") or {}).get("value"):
        reader = _persona_target_reader(db, book)
        if reader:
            inp["目标读者画像"] = {"value": reader, "tier": "persona", "inherited": True}
    missing = list(data.get("missing") or [])
    # 目标读者画像已由人设兜底, 从 missing 剔除
    if "目标读者画像" in missing:
        missing.remove("目标读者画像")
    # 本书迁移视角 (2026-09-08 老谭侧专属, 静读书不产): 书级一次性产出 3~4 个
    # 迁移视角 (读书系统适应所有书, 禁一根筋只对职场人) — 稿件层视角适配引用。
    try:
        if _is_laotan_book(book) and not (inp.get("本书迁移视角") or {}).get("value"):
            v_sys = ("你是读书系统的视角规划模块。给定一本书的类型与核心主张, 产出 3~4 个"
                     "「把这本书讲给谁听」的迁移视角——既要普惠又要有侧重, 按书的性质适配"
                     "(商业战略书→企业主/部门管理者/小生意人/职场人; 心理书→自我成长/关系/"
                     "养育者……)。每视角 = 名称 + 一句适用说明。输出严格 JSON: "
                     '{"views": ["名称:说明", ...]}')
            v_user = (f"书名：《{book.book_title}》\n类型：{data.get('书籍类型') or ''}\n"
                      f"核心主张：{str(data.get('全书核心主张') or '')[:200]}")
            vd = _parse_json(_llm().chat(v_sys, v_user, model="flash", temperature=0.3))
            views = [str(v) for v in ((vd or {}).get("views") or []) if str(v).strip()][:4]
            if views:
                inp["本书迁移视角"] = {"value": views, "tier": tier}
    except Exception as exc:
        logger.warning("[book] 迁移视角生成失败(稿件层自选兜底): %s", exc)
    # 核心四字段: 无 L0 时 L2 一律标待补 (防幻觉红线)
    needs = list(missing)
    if not l0:
        for f in _CORE_FIELDS:
            if f not in needs:
                needs.append(f)
    inp["needs_supplement"] = needs
    book.input_json = inp
    book.book_type = (inp.get("书籍类型") or {}).get("value")
    book.core_claim = (inp.get("全书核心主张") or {}).get("value")
    book.status = "input_review"
    db.commit()
    return book


def _persona_target_reader(db: Session, book: BookProject | None = None) -> str | None:
    """书账号人设的目标读者画像. 供书级缺省继承.

    2026-09-07 双人物: 优先按 book.input_json.persona_id 取所选 persona 的受众
    (老谭读书=男性认知/升职人群, 静读书=女性成长); 无 persona_id / 查不到
    回退静读书 (旧书兼容)。
    0913c 定位收窄: 只做**兜底与语感层** — 目标读者本体由书的内容锁定
    (_derive_book_reader), 账号受众不再盖到书上。
    """
    from app.models import Persona

    from .persona import ensure_book_account
    try:
        p: Persona | None = None
        if book is not None:
            pid = (book.input_json or {}).get("persona_id") if isinstance(book.input_json, dict) else None
            if pid:
                p = db.query(Persona).filter(Persona.id == pid).first()
        if p is None:
            host = ensure_book_account(db)
            p = db.query(Persona).filter(Persona.host_id == host.id).first()
        return (p.target_reader or "").strip() if p else None
    except Exception as exc:
        logger.warning("[book] persona target_reader lookup failed: %s", exc)
        return None


# ── 目标读者以书定 (0913c 用户令) ────────────────────────────────
def _book_reader_value(inp: dict) -> str | None:
    """书级目标读者有效值: tier != persona (书内容锁定/人工填) 才算数.

    persona 继承值 (tier=persona, 0907 旧逻辑盖的章) 视为"未定" — 调用方
    应走 _derive_book_reader 重新从书的内容锁定。
    """
    v = inp.get("目标读者画像") if isinstance(inp, dict) else None
    if isinstance(v, dict) and v.get("value") and v.get("tier") != "persona":
        return str(v["value"])
    return None


def _derive_audience_map(book: BookProject, facing: dict) -> dict | None:
    """【受众地图锁定器 v2】(0914 用户令: 两层受众 — L1 书定主线 + L2 两轴闲话池).

    L1 = 书的内容直接锁定的主线读者 (处境+决策场景);
    L2 池按两轴划分 (跟着书的内容划, 相对 L1 一步之遥, 封闭小集合防大礼包):
      - 上下游 (价值链): 上游掐钱的/下游消费的 — 与 L1 同链同构的人;
      - 上下层 (组织): 上层定标准的/下层被摆布的 — 与 L1 同构的组织位置。
    每组带同构桥一句 (机制同构, 禁鸡汤"道理相通")。

    料 (受众无关面, 不吃 facing 三面重跑): kernel 内核 + L0 章节概念 + 书页 blurb。
    L1 锁不住 (eligible=false) 返回 None → 调用方 persona 兜底。
    """
    kernel = facing.get("kernel") or {}
    l0 = facing.get("l0") or {}
    meta = (book.input_json or {}).get("book_meta") or {} \
        if isinstance(book.input_json, dict) else {}
    if not (kernel or l0 or meta.get("blurb")):
        return None
    concepts: list[str] = []
    for ch in (l0.get("chapters") or [])[:12]:
        for c in (ch.get("concepts") or [])[:3]:
            n = str(c.get("name", "")).strip()
            if n and n not in concepts:
                concepts.append(n)
    user = (f"书名：《{book.book_title}》\n作者：{book.author or '未知'}\n"
            f"一句话内核：{kernel.get('one_liner', '')}\n"
            f"为什么值得读：{kernel.get('why_read', '')}\n"
            f"与同类书的差异：{kernel.get('positioning', '')}\n"
            f"读者认知转变：{'；'.join(str(x) for x in (kernel.get('reader_shift') or [])[:4])}\n"
            f"核心概念：{'、'.join(concepts[:15])}\n"
            f"内容简介：{str(meta.get('blurb') or '')[:300]}")
    sys_p = (
        "你是拆书系统的【受众地图锁定器】。只从书的内容锁定这本书的两层受众。\n"
        "**L1 主线读者**: 书为谁而写 — 处境+决策场景, 写成能直接当开篇筛选器的样子"
        " (一读就知道是不是自己)。\n"
        "- 禁从账号定位/平台调性出发, 禁人口学套话 (\"25-50岁男性\"这类账号级画像✗);\n"
        "- 书讲某行业案例时, 读者≠该行业从业者 (拆HBO≠只给电视人看): 书回答的是"
        "底层机制问题 → 读者=现实中处于**同构处境**的人群 (谁今天的处境会被这个机制"
        "直接摆布); 同构=处境被同一机制摆布, 沾边 (只是好奇/想提升) 不纳入;\n"
        "**L2 闲话池 — 两条轴, 各收 ≤2 组** (跟着书的内容划, 每组=相对 L1 一步之遥):\n"
        "- 上下游轴 (价值链): 与 L1 同链同构的人 — 给你定规则掏钱的/你产出交给的,"
        " 那类位置在**观众世界**里的版本;\n"
        "- 上下层轴 (组织): 与 L1 同构的组织位置 — 你头顶定标准批预算的/你手底下"
        " 被摆布交付的, 在观众世界里的版本;\n"
        "- **L2 组是观众世界里的人** (他们会刷到这个视频): 用他们自己的身份+场景指称"
        " (\"管投放预算的市场负责人\"\"靠客户预算吃饭的乙方\"\"替老板管交付的团队长\"),"
        " 禁书内角色平移 (\"制片方\"\"广告主\"\"平台\"=书的cast✗ — 他们在这行里, "
        "闲话句'你不做这行也一样'对他们不成立);\n"
        "- 轴归位: 平台/渠道/金主=上下游轴 (\"平台就是你的老板\"也归上下游, 不归上下层);"
        " 上下层只装同一组织内的层级;\n"
        "- 每组必须带 bridge=同构桥一句: 描述**那群人自己的处境**被同一机制摆布"
        " (通过了'你不做这行也一样——'句式检验), 不是他们和 L1 的买卖关系;"
        " 禁鸡汤桥 (\"道理是相通的\"✗);\n"
        "- 书的内容撑不起某条轴 → 该轴给空数组, 禁硬编。\n"
        '输出严格 JSON: {"reader":str(≤80字),"evidence":str,"eligible":bool,'
        '"chain":[{"group":str(≤20字),"bridge":str(≤40字)}],'
        '"org":[{"group":str(≤20字),"bridge":str(≤40字)}]}'
    )
    try:
        data = _parse_json(_llm().chat(sys_p, user, model="flash", temperature=0.2))
    except Exception as exc:
        logger.warning("[book] 受众地图锁定失败(走人设兜底): %s", exc)
        return None
    if not (data or {}).get("eligible") or not str(data.get("reader") or "").strip():
        return None

    def _pool(raw) -> list[dict]:
        out = []
        for it in (raw or [])[:2]:
            g = str((it or {}).get("group") or "").strip()
            br = str((it or {}).get("bridge") or "").strip()
            if g:
                out.append({"group": g[:30], "bridge": br[:60]})
        return out

    return {"L1": {"reader": str(data["reader"]).strip()[:120],
                   "evidence": str(data.get("evidence") or "").strip()[:120]},
            "上下游": _pool(data.get("chain")),
            "上下层": _pool(data.get("org"))}


def _derive_book_reader(book: BookProject, facing: dict) -> dict | None:
    """v1 兼容壳: 只取 L1。新代码用 _derive_audience_map。"""
    m = _derive_audience_map(book, facing)
    return m and {"reader": m["L1"]["reader"], "evidence": m["L1"]["evidence"]}


# ── 步骤 2: 评论层书化 (flash) — 读者反应清单 ─────────────────────
def _l0_facing(book: BookProject) -> tuple[list, list]:
    """读 L0 第二层 facing-readers/units (data/l0/{书名}/facing/), 缺失返回空.

    2026-08-22 评论层精修: 优先用 facing 产出 (读者痛点+主题单元), 评论关联单元.
    """
    try:
        from app.services.book_service.l0 import _l0_dir
        facing = _l0_dir(book.book_title) / "facing"
        readers: list = []
        units: list = []
        try:
            readers = json.loads((facing / "facing-readers.json").read_text(encoding="utf-8")).get("readers", [])
        except Exception:
            pass
        try:
            units = json.loads((facing / "facing-units.json").read_text(encoding="utf-8")).get("units", [])
        except Exception:
            pass
        return readers, units
    except Exception:
        return [], []


def _l0_facing_data(book: BookProject) -> dict:
    """读全部 facing (data/l0/{书名}/facing/*.json), 缺失字段为 None."""
    from app.services.book_service.l0 import _l0_dir
    d = _l0_dir(book.book_title)
    facing: dict = {}
    for name, f in {"kernel": "facing-kernel.json", "units": "facing-units.json",
                    "quotes": "facing-quotes.json", "cases": "facing-cases.json",
                    "compliance": "facing-compliance.json", "readers": "facing-readers.json"}.items():
        p = d / "facing" / f
        if p.exists():
            try:
                facing[name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                facing[name] = None
    # L0 合并章节
    l0p = d / "l0-chapter-v1.json"
    if l0p.exists():
        try:
            facing["l0"] = json.loads(l0p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return facing


def auto_fill_from_l0(db: Session, book: BookProject) -> list[str]:
    """从 L0/facing 自动填充 (2026-08-22): 补全字段/合规/评论层/总纲 — 免重复点击.

    蒸馏(L0+facing)已产出分析层: 内核/概念/金句/合规/读者痛点/主题单元,
    直接填充 input_json/risk_assessment/comment_layer/episodes, 不再让 LLM 重新读全文.
    返回填充说明列表; 已填字段跳过 (幂等, 人工改过的不覆盖).
    """
    facing = _l0_facing_data(book)
    if not facing:
        return []
    inp = dict(book.input_json or {})
    filled: list[str] = []

    def _set(key: str, val, *, force: bool = False) -> bool:
        if force or not inp.get(key):
            inp[key] = val
            return True
        return False

    # 0914 受众地图 (用户令: 两层受众 — L1 书定主线 + L2 两轴闲话池):
    # 书级已有非 persona 值用之; persona 继承值视为未定, 重新锁定
    _br = _book_reader_value(inp)
    _am = inp.get("受众地图", {}).get("value") if isinstance(inp.get("受众地图"), dict) else None
    if not _br:
        _d = _derive_audience_map(book, facing)
        if _d:
            _am = _d
            inp["受众地图"] = {"value": _d, "tier": "L0"}
            inp["目标读者画像"] = {"value": _d["L1"]["reader"], "tier": "L0",
                                 "evidence": _d["L1"]["evidence"]}
            _br = _d["L1"]["reader"]
            filled.append(f"受众地图←书内容锁定(上下游{len(_d['上下游'])}组/上下层{len(_d['上下层'])}组)")
            book.input_json = inp  # 提前可见: 评论层回退生成要用书定读者
    # 受众指纹校验 (0907 立, 0913c 重定向): facing 三面选题角度跟**书定的读者**走,
    # 书锁不住才退 persona 受众。0907 的病 (老谭账号拆出"全能妈妈"六集) 根因是
    # 旧 facing 绑默认静读者 — 书定读者同样治它, 且不再把账号人口学盖到书上
    try:
        from .facing import facing_audience, run_facings
        from .l0 import _l0_dir
        _book_aud = _br or _persona_target_reader(db, book)
        if _book_aud and facing_audience(_l0_dir(book.book_title)) != _book_aud:
            run_facings(_l0_dir(book.book_title),
                        facings=["units", "hooks", "readers"],
                        audience=_book_aud)
            facing = _l0_facing_data(book)  # 重载 (新受众角度)
            logger.info("[book] %s facing 三面已按书定读者重跑", book.book_title)
    except Exception as exc:
        logger.warning("[book] facing 受众重跑失败(沿用旧角度): %s", exc)

    # 1) 补全字段 ← kernel + L0 概念/金句
    kernel = facing.get("kernel")
    if kernel and _set("全书核心主张", {"value": kernel.get("one_liner", "")}):
        filled.append("核心主张←内核")
    # 目标读者: 书定已落 (上文) / 书锁不住 → 人设兜底 (语感层, 不再冒充书读者)
    if not _br and not (inp.get("目标读者画像") or {}).get("value"):
        _reader = _persona_target_reader(db, book)
        if _reader:
            inp["目标读者画像"] = {"value": _reader, "tier": "persona", "inherited": True}
            filled.append("目标读者←人设(兜底)")
    l0 = facing.get("l0")
    if l0:
        concepts: list[str] = []
        for ch in l0.get("chapters", []):
            for c in (ch.get("concepts") or []):
                n = c.get("name")
                if n and n not in concepts:
                    concepts.append(str(n))
        if concepts and _set("关键概念清单", {"value": concepts[:20]}):
            filled.append(f"概念←L0({len(concepts)}个)")
    quotes = facing.get("quotes")
    if quotes:
        g = [str(q.get("text")) for q in quotes.get("quotes", []) if q.get("type") == "格言型"][:5]
        if g and _set("核心金句", {"value": g}):
            filled.append(f"金句←facing-quotes({len(g)}格言)")

    # 2) 合规 ← facing-compliance → risk_assessment
    compliance = facing.get("compliance")
    if compliance and not inp.get("risk_assessment"):
        su = compliance.get("sensitive_units") or []
        inp["risk_assessment"] = {
            "tier": compliance.get("book_tier", "yellow"),
            "risk_points": [h for u in su for h in (u.get("hits") or [])][:10],
            "min_safe_ops": [str(a) for a in (compliance.get("safe_angles") or [])][:3],
            "abandon": bool(compliance.get("abandon")),
        }
        filled.append(f"合规←facing-compliance({compliance.get('book_tier','?')})")

    # 3) 评论层 ← facing-readers; readers 缺失时回退 LLM 生成 (2026-08-23: 防 facing 只跑部分层 → 评论层 0)
    readers = facing.get("readers")
    if not inp.get("comment_layer"):
        if readers:
            comments = [{"type": "痛点", "comment": r.get("pain", ""), "unit_id": r.get("unit_id", "")}
                        for r in readers.get("readers", [])]
            if comments:
                inp["comment_layer"] = comments
                filled.append(f"评论层←facing-readers({len(comments)}痛点)")
        else:
            try:
                # 用主题单元 flash LLM 生成 (同 确认1 路径); 提交后重读 inp 保住其结果
                build_comment_layer(db, book)
                inp = dict(book.input_json or {})
                if inp.get("comment_layer"):
                    filled.append(f"评论层←LLM回退({len(inp['comment_layer'])}条)")
            except Exception as exc:
                logger.warning("[book] 评论层回退生成失败: %s", exc)

    book.input_json = inp

    # 4) 总纲 ← facing-units (编排 episodes, 每集一单元, 对应书中内容←L0 章节映射)
    units = facing.get("units")
    l0 = facing.get("l0") or {}
    if units:
        from app.models import Episode
        unit_list = units.get("units", [])
        unit_titles = {str(u.get("title", "")).strip() for u in unit_list}
        # 重建条件: 无 episodes / 缺字段(对应书中内容/卖点) / units 已变更(标题不同步) / 对应书中内容含旧文件名 — 人工编辑过的保留
        needs_rebuild = (not book.episodes) or not all(
            (e.roadmap_json or {}).get("对应书中内容") and (e.roadmap_json or {}).get("卖点")
            and (e.title or "").strip() in unit_titles
            and not re.search(r"text\d+\.html", str((e.roadmap_json or {}).get("对应书中内容", "")))
            for e in book.episodes)
        # E1 系列预告升级: 多集时 E1 必须带"系列预告"字段
        if not needs_rebuild and len(unit_list) > 1:
            e1 = next((e for e in book.episodes if e.ep_index == 1), None)
            if not (e1 and (e1.roadmap_json or {}).get("系列预告")):
                needs_rebuild = True
        if needs_rebuild:
            for old in list(book.episodes):
                db.delete(old)
            db.flush()
            for i, u in enumerate(unit_list, 1):
                # 对应书中内容: 单元概念匹配 L0 章节标题 (文件名友好化为"第N章")
                uc = set(str(c) for c in (u.get("concepts") or []))
                hit = []
                for ch in (l0.get("chapters") or []):
                    if any(str(c.get("name", "")) in uc or str(c.get("name", "")) == x
                           for c in (ch.get("concepts") or []) for x in uc):
                        t = str(ch.get("title", ""))
                        if re.match(r"text\d+\.html$", t, re.I):  # 旧 L0 文件名 → 第N章
                            t = f"第{ch.get('idx', '?')}章"
                        hit.append(t)
                prev_ref = f"第{i - 1}集" if i > 1 else "全书导入"
                next_ref = f"第{i + 1}集" if i < len(unit_list) else "系列完结"
                selling = (u.get("selling_point") or "") or (u.get("analogy_hooks") or [""])[0] or str(u.get("core_claim", ""))[:40]
                # 2026-08-22: E1 导读带系列预告 (已知全集数 + 各集主题 → 引导追更/关注)
                preview = ""
                if i == 1 and len(unit_list) > 1:
                    others = [f"第{j}集《{unit_list[j - 1].get('title', '')}》"
                              for j in range(2, len(unit_list) + 1)]
                    preview = (f"本系列共{len(unit_list)}集：第1集导读全书，为什么值得读；"
                               + "；".join(others) + "。"
                               + ("最后一集教你落地应用。本期开头预告整体系列，引导关注追更。" if len(unit_list) > 2 else ""))
                db.add(Episode(
                    book_id=book.id, ep_index=i, title=u.get("title", ""),
                    roadmap_json={"ep": i, "unit_id": u.get("id", ""), "主题": u.get("title", ""),
                                  "对应书中内容": "、".join(hit[:3]) or str(u.get("core_claim", ""))[:40],
                                  "核心任务": u.get("core_claim", ""),
                                  "卖点": selling,
                                  "系列预告": preview,
                                  "承上": f"承接{prev_ref}", "启下": f"引出{next_ref}",
                                  "概念": u.get("concepts", [])},
                    target_duration_sec=_ep_target_duration(book), status="pending",
                ))
            book.status = "roadmap_review"
            filled.append(f"总纲←facing-units({len(unit_list)}集)")

    db.commit()
    # 1019 一案一库: 总纲在库即补本书案例库 (幂等 — 已存在不重产)
    filled += [f"本书案例库: {i}" for i in build_book_cases(book)]
    return filled


def _ep_target_duration(book: BookProject) -> float:
    """单集目标时长 (秒). 0930 重构: 人格时长从代码分支搬进 Host.config_json.pacing
    (老谭=300s 五分钟五分区论, 见 persona.LAOTAN_PACING 演化注); 无 pacing 人格
    fallback 600s (静读书/新闻线现状)。历史: 0907 300 → 0922 240 → 0930 300。
    """
    try:
        pid = (book.input_json or {}).get("persona_id") if isinstance(book.input_json, dict) else None
        from app.database import get_session_maker
        from app.models import Host, Persona
        smk = get_session_maker()
        if smk is None:
            return 600.0
        with smk() as _db:
            if pid:
                _p = _db.query(Persona).filter(Persona.id == pid).first()
                if _p and _p.host_id:
                    _h = _db.query(Host).filter(Host.id == _p.host_id).first()
                    if _h:
                        from app.services.book_service.persona import get_pacing
                        _pc = get_pacing(_db, _h)
                        if _pc and _pc.get("target_duration_sec"):
                            return float(_pc["target_duration_sec"])
            return 600.0
    except Exception:
        return 600.0


def build_comment_layer(db: Session, book: BookProject) -> BookProject:
    inp = dict(book.input_json or {})  # 必须拷贝: 同实例原地改 SQLAlchemy 不记脏
    l0 = source_context(book, cap=6000)
    readers, units = _l0_facing(book)
    # 0913c 优先级翻转: 书定读者 > 本书 persona (旧代码漏传 book, 拿的是默认 host
    # 的人设 — 老谭书可能吃到静读书受众) > 旧 inherited 值
    reader_txt = (_book_reader_value(inp) or _persona_target_reader(db, book)
                  or (inp.get("目标读者画像") or {}).get("value") or "")
    if readers or units:
        # 精修 (2026-08-22): 输入 facing 产出, 每条评论关联主题单元 → 逐集按单元戳痛点
        sys_p = (
            "你是拆书项目的评论层模块。基于目标读者痛点与主题单元，模拟读者看完这本书后的真实评论，"
            "供逐集创作戳痛点/制造共鸣。"
            "每条评论必须关联一个主题单元(unit_id)，覆盖主要单元。"
            "输出严格 JSON 数组: [{\"type\":\"痛点|质疑|共鸣|好奇|唱反调\",\"comment\":str,\"unit_id\":str}]，8-12 条。"
        )
        user_p = f"书名：《{book.book_title}》\n读者画像：{reader_txt}\n"
        if readers:
            user_p += "\n【读者痛点】\n" + "\n".join(
                f"- {r.get('pain', '')} (unit:{r.get('unit_id', '')})" for r in readers[:10])
        if units:
            user_p += "\n【主题单元】\n" + "\n".join(
                f"- {u.get('id', '')} {u.get('title', '')}: {str(u.get('core_claim', ''))[:80]}" for u in units[:10])
        if l0:
            user_p += f"\n\n【来源节选】\n{l0}"
    else:
        # 回退旧逻辑 (无 facing 时)
        sys_p = (
            "你是拆书项目的评论层模块。模拟目标读者看完这本书后的真实反应，"
            "产出读者反应清单，供逐集创作戳痛点/制造共鸣。"
            "输出严格 JSON 数组: [{\"type\":\"痛点|质疑|共鸣|好奇|唱反调\",\"comment\":str}]，8-10 条。"
        )
        user_p = (
            f"书名：《{book.book_title}》\n核心主张：{book.core_claim or ''}\n"
            f"读者画像：{reader_txt}\n"
            + (f"\n【来源节选】\n{l0}" if l0 else "")
        )
    data = _first_list(_parse_json(_llm().chat(sys_p, user_p, model="flash", temperature=0.8)))
    inp["comment_layer"] = data
    book.input_json = inp
    db.commit()
    return book


# ── 步骤 3: 素材包书化 — 搜索 L1 (书评/访谈/延伸阅读) ─────────────
def build_materials(db: Session, book: BookProject) -> BookProject:
    from app.services.zhipu_search import ZhipuSearchError, zhipu_web_search

    queries = [
        f"{book.book_title} {book.author or ''} 书评",
        f"{book.book_title} 核心观点 解读",
        f"{book.author or book.book_title} 访谈",
    ]
    items: list[dict] = []
    for q in queries:
        try:
            hits = zhipu_web_search(q, count=3)
        except ZhipuSearchError as exc:
            logger.warning("[book] 素材搜索不可用: %s", exc)
            break
        for h in hits:
            items.append({"title": h.get("title"), "media": h.get("media"),
                          "snippet": (h.get("content") or "")[:300], "link": h.get("link")})
    inp = dict(book.input_json or {})  # 拷贝防 ORM 同实例不记脏
    inp["materials"] = items
    book.input_json = inp
    db.commit()
    return book


# ── 步骤 4: 多集总纲 (pro) + 追溯自检 ────────────────────────────
def build_roadmap(db: Session, book: BookProject) -> tuple[BookProject, list[str]]:
    inp = book.input_json or {}

    def _v(k):
        return (inp.get(k) or {}).get("value")

    # 2026-08-22: 按 L0 第二层 facing-units 自适应集数 (units.length = 该拆几集);
    # 无 units 回退旧 6 集逻辑.
    _readers, units = _l0_facing(book)
    if units:
        n = len(units)
        unit_lines = "\n".join(
            f"- {u.get('id')}《{u.get('title')}》: {str(u.get('core_claim', ''))[:100]}"
            f" 概念:{','.join(str(c) for c in (u.get('concepts') or [])[:4])}"
            for u in units)
        sys_p = (
            "你是拆书稿创作系统的总纲模块。已给出本书的 " + str(n) + " 个主题单元（每单元=可独立讲 5-8 分钟的主题），"
            "按单元生成 N 集路线图，每集对应一个单元（顺序可按讲述逻辑调整，第 1 集可作为全书地图）。"
            "每集必须保留对应单元的 unit_id，主题可追溯到单元 core_claim，严禁编造单元外内容。"
            '每集另产: 本集秘籍(该集观众可带走的工具/方法, 一句话具体可执行) + 黄金三秒钩子(开头3秒抓人的钩子句, 断言式禁弱化词) '
            '+ 核心问题(该集病灶陈述一句: 观众处境里什么卡住了 — 痛点锚, 逐集稿痛点段对它开刀)。'
            '承上/启下必须写具体叙事交接 (承上=上集的什么结论/悬念本集从哪接, 首集写"无(系列首集)—入口钩子"; '
            '启下=本集结尾抛给下集的具体钩子) — 禁「承接第N集/引出第N集」式零信息套话 (1006 实锤 7 集全套话)。'
            '输出严格 JSON 数组 ' + str(n) + ' 项: {"ep":int,"unit_id":str,"主题":str,"对应书中内容":str,'
            '"本集秘籍":str,"黄金三秒钩子":str,"核心问题":str,"核心任务":str,"卖点":str,"承上":str,"启下":str,"概念":[str]}'
        )
        user_p = (
            f"书名：《{book.book_title}》\n核心主张：{book.core_claim or ''}\n【主题单元】\n{unit_lines}\n"
            f"读者反应清单：{json.dumps(inp.get('comment_layer') or [], ensure_ascii=False)[:2000]}"
        )
    else:
        n = 6
        sys_p = (
            "你是拆书稿创作系统的总纲模块。按 6 集系列拆解规则生成【6集拆解路线图】："
            "第1集全书地图/第2-5集核心模块(每集1-2概念, 层层深入)/第6集落地收尾(行动清单)。"
            "每集主题必须可追溯到书籍来源，严禁凭空编造。"
            "每集另产: 本集秘籍(观众可带走的工具/方法一句话) + 黄金三秒钩子(开头3秒断言式钩子句) "
            "+ 核心问题(该集病灶陈述一句, 痛点锚)。"
            "承上/启下写具体叙事交接 (上集结论/悬念→本集切入; 本集结尾→下集钩子), 禁「承接第N集」式套话。"
            "输出严格 JSON 数组 6 项: {\"ep\":int,\"主题\":str,\"对应书中内容\":str,"
            "\"本集秘籍\":str,\"黄金三秒钩子\":str,\"核心问题\":str,\"核心任务\":str,\"卖点\":str,\"承上\":str,\"启下\":str,\"概念\":[str]}"
        )
        user_p = (
            f"书名：《{book.book_title}》\n核心主张：{book.core_claim or ''}\n"
            f"关键概念：{_v('关键概念清单')}\n章节结构：{_v('章节结构')}\n"
            f"读者反应清单：{json.dumps(inp.get('comment_layer') or [], ensure_ascii=False)[:2000]}"
        )
    rows = _first_list(_parse_json(_llm().chat(sys_p, user_p, model="pro", temperature=0.5)))
    if not isinstance(rows, list) or len(rows) != n:
        raise ValueError(f"总纲须为 {n} 行, 实际 {len(rows) if isinstance(rows, list) else type(rows)}")

    # 追溯自检: 关键概念须在总纲有落点 (金句/案例落逐集稿, 不在总纲级查)
    issues: list[str] = []
    blob = json.dumps(rows, ensure_ascii=False)
    for item in (_v("关键概念清单") or [])[:10]:
        key = str(item)[:12]
        if key and key not in blob:
            issues.append(f"关键概念未入总纲: {key}…")

    # 落库 N 集 (N = units 数或回退 6)
    for old in list(book.episodes):
        db.delete(old)
    for r in rows:
        db.add(Episode(
            book_id=book.id, ep_index=int(r.get("ep", 0)) or rows.index(r) + 1,
            title=str(r.get("主题", "")), roadmap_json=r,
            target_duration_sec=_ep_target_duration(book), status="pending",
        ))
    book.status = "roadmap_review"
    db.commit()
    # 1019 一案一库 (用户令): 总纲定稿即产本书案例库 — force 重产 (总纲重建概念可能变)
    issues += build_book_cases(book, force=True)
    return book, issues


# ── 步骤 4b: 本书专用中国案例库 (1019 用户令 ①案一库: 大纲时生成, 调性一致) ──
def build_book_cases(book: BookProject, *, force: bool = False) -> list[str]:
    """产 12-15 条与总纲概念对齐的中国案例 → data/l0/{书}/案例库-本书.json。

    幂等 (已存在跳过, force 重产); 消费端三处并入 (episode_gen 喂料+排重词表,
    creation_common._zh_brands) — 海底捞式提名案例入库后跨集可查重 (ep1+ep3 撞实锤)。
    返回 issues (空=成功或已存在)。"""
    issues: list[str] = []
    try:
        from app.services.book_service.l0 import _l0_dir as _l0d
        _bp = _l0d(book.book_title) / "案例库-本书.json"
        if _bp.exists() and not force:
            return issues
        eps = sorted(book.episodes or [], key=lambda e: e.ep_index)
        concepts: list[str] = []
        for e in eps:
            for c in (e.roadmap_json or {}).get("概念") or []:
                c = str(c).strip()
                if c and c not in concepts:
                    concepts.append(c)
        if not concepts:
            return ["本书案例库跳过: 总纲概念为空 (先出总纲)"]
        ep_lines = "\n".join(
            f"- 第{e.ep_index}集《{e.title}》 概念: "
            f"{','.join(str(c) for c in ((e.roadmap_json or {}).get('概念') or [])[:5])}"
            for e in eps)
        _aud = ""
        try:
            _aud = str((((book.input_json or {}).get("受众地图") or {}).get("value") or {})
                       .get("L1", {}).get("reader", ""))[:120]
        except Exception:  # noqa: BLE001 — 受众字段形态漂移只弃锚不弃库
            _aud = ""
        sys_p = (
            "你是拆书稿创作系统的本书案例库模块。为这本拆书系列定制 12-15 条中国企业案例, "
            "筛选铁律 (1019 用户令): ①只收 **A股或港股上市公司** (沪深京港交易所 — 美股/未上市不收; "
            "唯一特例: 华为, 未上市但用户钦点); ②必须是该行业**绝对龙头** (市占率/规模公认 No.1, "
            "老二老三不收, 中兴通讯式跟随者剔除); ③行业覆盖分散, 禁同行业双条 (含细分重叠: "
            "海尔/格力同属家电只收其一, 汇川/中控同属工业自动化只收其一); "
            "④用户黑名单品牌不收: 联想; "
            "⑤禁近两年热点争议企业 (万科债务/药明康德法案类 2024+ 风波的剔除, 只取已沉淀公知史)。"
            "调性必须与本书一致 — concepts 键必须能对上总纲概念清单 (原文或同义表述), 禁硬凑无关行业。"
            "铁律 (0927 案例口径三则): ①只述管理动作禁评企业好坏; "
            "②facts 只用公知口径骨架 (公开报道/当年对外披露/早期), 禁编细节数字; "
            "③禁近两年热点争议事件 (2024+年份与品牌同句即废), 只取已沉淀公知商业史。"
            '每条 JSON: {"name":str(纯品牌名,禁括号后缀/时代标注 — 时代入era, 排重词表按裸名拆),'
            '"industry":str,"era":str,"fame":str(国民级/行业知名),'
            '"concepts":[str],"role":str(正面/反面/格局),"facts":[str×2-4],"src":str(公知来源口径),'
            '"status":"可用","boundary":str(失效边界一句),"region":"cn"}。'
            "输出严格 JSON 数组 12-15 项, 无其他文字。"
        )
        user_p = (
            f"书名：《{book.book_title}》\n核心主张：{book.core_claim or ''}\n受众：{_aud}\n"
            f"【总纲各集】\n{ep_lines}\n【总纲概念清单 (concepts 键从此对齐)】\n{'、'.join(concepts)}"
        )
        rows = _first_list(_parse_json(_llm().chat(sys_p, user_p, model="pro", temperature=0.4)))
        valid, seen = [], set()

        def _norm_key(n: str) -> str:
            # 同企变体归一 (联想集团→联想): 企业后缀剥掉才查重, 防同企双条分喂两集
            return re.sub(r"集团|公司|中国|（[^）]*）|\([^)]*\)", "", n)

        for c in rows if isinstance(rows, list) else []:
            name = re.sub(r"（[^）]*）|\([^)]*\)", "", str(c.get("name") or "")).strip()
            facts = [str(f) for f in (c.get("facts") or []) if str(f).strip()]
            cc = [str(x) for x in (c.get("concepts") or []) if str(x).strip()]
            if not name or str(c.get("region")) == "global" or not facts or not cc:
                continue
            if not any("一" <= ch <= "鿿" for ch in name):
                continue  # 纯ASCII名 (IBM式) — 中国本土库不收
            _k = _norm_key(name)
            if _k in seen:
                continue
            seen.add(_k)
            c["name"], c["facts"], c["concepts"], c["status"] = name, facts, cc, "可用"
            valid.append(c)
        if len(valid) < 8:
            return [f"本书案例库生成不合格 ({len(valid)} 条 < 8), 不落盘"]
        _bp.parent.mkdir(parents=True, exist_ok=True)
        _bp.write_text(json.dumps({"cases": valid}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        _aligned = sum(1 for c in valid if any(x in concepts for x in c["concepts"]))
        if _aligned < len(valid) // 2:
            issues.append(f"本书案例库概念对齐率低: {_aligned}/{len(valid)}")
        logger.info("[book] 本书案例库落盘 %d 条: %s", len(valid),
                    "、".join(c["name"] for c in valid[:5]))
    except Exception as exc:  # noqa: BLE001 — 案例库失败不炸大纲步
        issues.append(f"本书案例库生成失败: {exc}")
    return issues
