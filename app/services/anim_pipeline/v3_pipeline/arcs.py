# -*- coding: utf-8 -*-
"""v3 ② 立意 (V2) — 语态驱动切弧 + 弧界执法 + LLM 只做创作.

分层纪律 (与 ① 同构):
- 确定性层 = 骨架与执法: 连续同语态行聚成「稿模块」; 弧界必须落模块边界
  (用户令) — 1108 修订: 讲述双声部 (comm+quote) 相邻模块并弧合法 (引用句
  是笔法不是弧界, ep6 15/24 单行弧实锤), 结构位混入仍红痕拒放行。
- LLM 层 = 创作: 每弧产出 视觉世界(world) + 隐喻核心(A=B); 超长评论段可建议
  在行边界内再拆 (拆点仍须单语态)。

弧型 (画面模式, 与 ③④ 消费):
  narr=叙事隐喻弧 / quote=书证镜(书的视角, 只限原话直出) / persona=人设镜(老谭亲身经历)
  hook=系列钩子 / cta=行动召唤 / brand=品牌位 — 后三者非叙事, 不占立意世界。
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

TYPE_OF_MODE = {
    "comm": "narr", "quote": "quote", "persona": "persona",
    "hook": "hook", "cta": "cta", "brand": "brand",
}
NON_NARRATIVE = ("hook", "cta", "brand")     # 非叙事位: 系列冻结语言, 不分配立意
# 1108 用户令 (ep6 实锤: 引用句/解说句行行交替 → 15/24 弧单行): 讲述双声部
# (解说 comm + 书证 quote) 是作者的引用笔法不是弧界 — 相邻讲述模块并成一弧
# (整模块并吞, 弧界仍落模块边界); hook/cta/persona/brand 是结构位, 保持纯语态。
NARR_MERGEABLE = {"comm", "quote"}
CHARS_PER_SEC = 4.5                          # 中文口播估时 (与 ① 预估同口径)
SPLIT_SUGGEST_LINES = 15                     # 单语态段超过此值, 提示可在段内再拆


def modules_of(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """确定性稿模块: 连续同语态的有效行聚一段。

    连续判定看「上一个有效行的语态」而非行号相邻 — 中间夹空行是常态
    (ep5 实锤: 逐行空行把同类语态切成 36 个单行模块)。段标/卡/图指令
    是**硬断点** (节目段落边界); 空行/未分诊是软跳过。
    """
    mods: list[dict[str, Any]] = []
    last_mode: str | None = None
    pending_break = False                # 段标/卡/图指令后 → 下一模块 hard_start
    for ln in lines:
        mode = str(ln.get("mode") or "unknown")
        if mode in ("section", "card", "img", "note"):
            last_mode = None            # 硬断点
            pending_break = True
            continue
        if not str(ln.get("text") or "").strip() or mode == "unknown":
            continue                     # 软跳过 (不断)
        if mods and last_mode == mode:
            mods[-1]["no_end"] = ln["no"]
            mods[-1]["lines"].append(ln)
        else:
            mods.append({"mode": mode, "no_start": ln["no"], "no_end": ln["no"],
                         "lines": [ln], "hard_start": pending_break})
            pending_break = False
        last_mode = mode
    return mods


def est_seconds(lines: list[dict[str, Any]]) -> float:
    chars = sum(len(str(l.get("text") or "").strip()) for l in lines)
    return round(chars / CHARS_PER_SEC, 1)


def merge_narr_modules(mods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1108 用户令 (讲述双声部并弧): 相邻且语态均 ∈ NARR_MERGEABLE 的模块并成一弧。

    整模块并吞 (不切模块内部) → 弧界仍落模块边界 (原令精神不变);
    主导语态 = 行数多者 (平手取 comm=解说主导, 书证句是论据不是骨架)。
    hook/cta/persona/brand 模块是天然弧界 (特区宪法按弧型注入, 混弧失保护);
    段标/卡/图指令后的模块 hard_start — 段落边界不并 (ep6 二轮实锤: A7 跨
    四个段标并成 26 行/159.8s 巨弧)。
    """
    out: list[dict[str, Any]] = []
    for m in mods:
        if (out and m["mode"] in NARR_MERGEABLE and out[-1]["mode"] in NARR_MERGEABLE
                and not m.get("hard_start")):
            prev = out[-1]
            prev["lines"] += m["lines"]
            prev["no_end"] = m["no_end"]
            _c = sum(1 for l in prev["lines"] if l.get("mode") == "comm")
            prev["mode"] = "comm" if _c * 2 >= len(prev["lines"]) else "quote"
        else:
            out.append(dict(m))
    return out


def build_skeleton(mods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """骨架 (确定性): 一模块一弧, 统一 A1..An 编号 + 弧型 + 行区间 + 预估时长。"""
    arcs: list[dict[str, Any]] = []
    for i, m in enumerate(mods, 1):
        arcs.append({
            "arc_id": f"A{i}",
            "type": TYPE_OF_MODE.get(m["mode"], "narr"),
            "mode": m["mode"],
            "no_start": m["no_start"],
            "no_end": m["no_end"],
            "line_count": len(m["lines"]),
            "est_s": est_seconds(m["lines"]),
            "narrative": "", "world": "", "metaphor": "", "cast": [], "vocab": [],
        })
    return arcs


def effective_nos(lines: list[dict[str, Any]]) -> set[int]:
    """参与分弧的有效行 (段标/卡图指令/未分诊/空行除外)。"""
    return {ln["no"] for ln in lines
            if str(ln.get("text") or "").strip()
            and str(ln.get("mode") or "") not in ("section", "card", "img", "note", "unknown")}


def boundary_issues(arcs: list[dict[str, Any]],
                    lines: list[dict[str, Any]]) -> list[str]:
    """执法 (用户令: 弧界必须落模块边界):
    ① 弧内语态单一 或 ⊆ 讲述双声部 {comm, quote} (1108 修订: 引用句是笔法
    不是弧界; 结构位 hook/cta/persona/brand 混入仍违例 — 特区宪法按弧型注入)
    ② 有效行恰好归属一弧 (无遗漏/无重复/无归属到段标等无效行)
    """
    mode_of = {ln["no"]: str(ln.get("mode") or "unknown") for ln in lines}
    valid = effective_nos(lines)
    issues: list[str] = []
    seen: dict[int, str] = {}
    for a in sorted(arcs, key=lambda x: int(x.get("no_start") or 0)):
        s, e = int(a.get("no_start") or 0), int(a.get("no_end") or 0)
        aid = a.get("arc_id")
        if s <= 0 or e < s:
            issues.append(f"{aid}: 行区间非法 [{s}, {e}]")
            continue
        nos = [no for no in range(s, e + 1) if no in valid]
        if not nos:
            issues.append(f"{aid}: 区间内无有效行 [{s}, {e}]")
            continue
        modes = {mode_of.get(no) for no in nos}
        # 1108 用户令: 讲述双声部 (comm+quote) 合法混弧 — 引用句是笔法不是弧界;
        # 结构位 (hook/cta/persona/brand) 混入仍红痕 (特区宪法按弧型注入, 混弧失保护)
        if len(modes) > 1 and not modes <= NARR_MERGEABLE:
            issues.append(f"{aid}: 跨语态 {sorted(modes)} — 混弧仅限讲述类(comm/quote), "
                          "结构位(hook/cta/persona/brand)必须纯语态弧")
        for no in nos:
            if no in seen:
                issues.append(f"L{no} 同时属于 {seen[no]} 与 {aid} (重复归属)")
            seen[no] = aid
    missing = sorted(valid - set(seen))
    if missing:
        prev = missing[0]
        span = [prev]
        for no in missing[1:]:
            if no == prev + 1:
                span.append(no)
            else:
                issues.append(f"L{span[0]}-L{span[-1]} 未归属任何弧")
                span = [no]
            prev = no
        issues.append(f"L{span[0]}-L{span[-1]} 未归属任何弧")
    return issues


_SYS = """你是短视频系列的分镜立意导演。输入是一集的稿件模块 (每行: 行号|语态|文本),
以及系统已确定的弧切分骨架 (弧号|语态|行区间|行数)。你的任务**只做创作, 不改切分**:

为每个弧输出:
- world: 视觉世界 (本弧画面构成的物理场景, 8-20 字; 书证弧=书的视角/引文排印,
  人设弧=老谭出镜讲自己的亲身经历, 叙事弧=一个具体的物理装置世界)
- metaphor: 立意核心, 必须写成 A=B 映射 (信任波动=三阶段路径…), 40-80 字
- narrative: 本弧讲什么, 一句 15-30 字
- cast: 本弧画面出场的角色卡名数组 — 只能从输入的「可用角色卡」名单里选;
  纯空镜/纯物品/非叙事位 (钩子/行动召/品牌) 填 []
- vocab: 本弧实体统一词表 (道具/场景/视觉锚点, **不含角色** — 角色走 cast),
  5-10 条, 每条 {"zh":"登山杖","en":"trekking pole"}; zh 取本弧口播/画面必然
  出现的实体原词, en 给稳定直译 — 同一实体全弧同词 (③画面句/K2 图/英译
  三层同实体同词, 跨镜视觉一致的锚); 无实体可填 []

铁律:
1. 相邻弧的视觉世界禁止重复 (轮换宪法)。
2. 叙事弧的 world 必须是可拍的物理装置/机械/空间, 禁抽象概念。
3. 非叙事位 (钩子/行动召/品牌) 的 world 填空串 ""。
4. 若某段超过 15 行, 可在 hints 里建议在语义完整处拆成两弧 (只写建议, 不改骨架)。
5. cast 只能选名单内卡名 (越界剔除); 人设弧 (老谭亲身经历) 必须绑定 老谭;
   口播里提到具体人物 (客户/医生/合伙人) 时, 优先从名单选对应的卡。
6. 视觉世界/vocab/隐喻禁依赖图内文字 (1108 源头治理): 招牌/屏幕字/白板/清单/
   报表/文件/告示/书页文字这类「没字就奇怪」的载体不选 — 隐喻必须能拍成无字
   画面 (用装置形态/动作/人物反应表达); vocab 不收文字载体实体。

严格 JSON: {"arcs":[{"arc_id":"A1","narrative":"...","world":"...","metaphor":"...",
"cast":["卡名"],"vocab":[{"zh":"登山杖","en":"trekking pole"}]}],"hints":["..."]}"""


def _card_catalog(book_title: str) -> dict[str, str]:
    """圣经角色卡目录 (卡名→look 摘要≤40字); 无圣经=空 dict (cast pass 静默降级)."""
    try:
        import json

        from app.services.anim_pipeline.director2 import _bible_cache_path
        bible = json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 无圣经 = 无卡体系, 不炸
        return {}
    out: dict[str, str] = {}
    for c in bible.get("characters") or []:
        name = str(c.get("name") or "").strip()
        if name:
            # 1108 批B: [:40]→[:120] 同 shots.card_catalog — 面部锚 (无胡须/眼镜)
            # 在 look 尾部, 截断=writer 看不到 (眼镜/胡子漂移上游因)
            out[name] = str(c.get("look") or "").strip()[:120]
    return out


def ensure_persona_host(arcs: list[dict[str, Any]], cards: dict[str, str]) -> int:
    """人设弧 (老谭亲身经历) cast 缺老谭 → 确定性补。返回补入的弧数。

    卡目录无老谭 (无圣经/无此人) 时零操作 — 不造卡名。
    """
    if "老谭" not in cards:
        return 0
    n = 0
    for a in arcs:
        if a.get("type") == "persona" and "老谭" not in (a.get("cast") or []):
            a["cast"] = list(dict.fromkeys((a.get("cast") or []) + ["老谭"]))
            n += 1
    return n


def _llm_fill(arcs: list[dict[str, Any]], lines: list[dict[str, Any]],
              book_title: str, ep: int) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """LLM 创作层: 填 world/metaphor/narrative/cast; 失败抛异常由调用方退骨架。"""
    from app.services.llm_service import get_llm_service, _parse_json
    cards = _card_catalog(book_title)
    text_of = {ln["no"]: str(ln.get("text") or "") for ln in lines}
    body_lines = []
    for a in arcs:
        seg = " / ".join(text_of.get(no, "") for no in
                         range(a["no_start"], min(a["no_end"], a["no_start"] + 6) + 1))
        body_lines.append(f'{a["arc_id"]}|{a["mode"]}|L{a["no_start"]}-L{a["no_end"]}'
                          f'|{a["line_count"]}行|{seg[:140]}')
    card_block = ""
    if cards:
        card_block = ("\n可用角色卡 (卡名|look):\n"
                      + "\n".join(f"{n}|{look}" for n, look in cards.items()) + "\n")
    body = f"《{book_title}》第{ep}集\n{card_block}\n" + "\n".join(body_lines)
    raw = get_llm_service().chat(_SYS, body, model="flash", temperature=0.6,
                                 response_format={"type": "json_object"}, tag="v3_arcs")
    data = _parse_json(raw)
    out: dict[str, dict[str, Any]] = {}
    hints: list[str] = [str(h)[:120] for h in (data.get("hints") or [])][:6]
    for row in data.get("arcs") or []:
        aid = str(row.get("arc_id") or "")
        if not aid:
            continue
        cast_raw = row.get("cast") or []
        if isinstance(cast_raw, str):
            cast_raw = [cast_raw]
        cast: list[str] = []
        for nm in cast_raw:
            nm = str(nm).strip()
            if nm in cards and nm not in cast:
                cast.append(nm)
            elif nm:
                hints.append(f"{aid} cast 越界剔除「{nm}」(不在卡名单)")
                hints = hints[:8]
        out[aid] = {"narrative": str(row.get("narrative") or "")[:120],
                    "world": str(row.get("world") or "")[:40],
                    "metaphor": str(row.get("metaphor") or "")[:200],
                    "cast": cast,
                    # 批3 vocab: 实体对结构归一 (zh/en 皆非空, 去重, 上限 10)
                    "vocab": _norm_vocab(row.get("vocab"))}
    return out, hints


def _norm_vocab(raw: Any) -> list[dict[str, str]]:
    """词表归一 (② 产出 / ② 页保存 共用): 只要 {zh,en} 皆非空的对, 去重, ≤10。"""
    out: list[dict[str, str]] = []
    for pair in (raw or [])[:10]:
        if not isinstance(pair, dict):
            continue
        zh = str(pair.get("zh") or "").strip()[:20]
        en = str(pair.get("en") or "").strip()[:60]
        if zh and en and not any(v["zh"] == zh for v in out):
            out.append({"zh": zh, "en": en})
    return out


# ── 视觉世界补全 (1105 用户令: 缺字段是系统的事, 不该甩给人) ────────────
# 实锤: ep5 17 弧里 6 条叙事弧 world 为空 (A2/A3/A9/A11/A13/A16) — 写手没有世界可继承,
# 画面句退化成通用办公室 (s2 铁证)。原实现只在 prompt 里"要求", LLM 漏了没人查。
NEED_WORLD = ("narr", "persona", "quote")

_WORLD_SYS = """你在补全短视频分镜立意缺失的「视觉世界」。输入若干弧
(弧号|语态|行区间|本弧口播摘录|已有隐喻)。为每个弧补一个 world:
- 可拍的物理场景 / 装置 / 机械 / 空间, 8-20 字, 禁止抽象概念;
- 必须能承载该弧的隐喻 (隐喻里的装置优先);
- 弧与弧之间不得重复同一世界。
只输出缺的那些弧, 严格 JSON: {"arcs":[{"arc_id":"A2","world":"..."}]}"""


def missing_worlds(arcs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """叙事/书证/人设弧缺 world 的清单 (钩子/行动召/品牌位本就为空, 不算)。"""
    return [a for a in arcs
            if str(a.get("type") or "") in NEED_WORLD
            and not str(a.get("world") or "").strip()]


def fill_missing_worlds(arcs: list[dict[str, Any]], lines: list[dict[str, Any]],
                        book_title: str, ep: int) -> int:
    """只补 world 的一次定点调用 (确定性校验 + LLM 创作)。返回补上的数量。"""
    miss = missing_worlds(arcs)
    if not miss:
        return 0
    from app.services.llm_service import get_llm_service, _parse_json
    text_of = {ln["no"]: str(ln.get("text") or "") for ln in lines}
    rows = []
    for a in miss:
        seg = " / ".join(text_of.get(no, "") for no in
                         range(a["no_start"], min(a["no_end"], a["no_start"] + 5) + 1))
        rows.append(f'{a["arc_id"]}|{a.get("mode")}|L{a["no_start"]}-L{a["no_end"]}'
                    f'|{seg[:120]}|{str(a.get("metaphor") or "")[:80]}')
    body = f"《{book_title}》第{ep}集\n\n" + "\n".join(rows)
    try:
        raw = get_llm_service().chat(_WORLD_SYS, body, model="flash", temperature=0.5,
                                     response_format={"type": "json_object"},
                                     tag="v3_arcs_world")
        data = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 — 补全失败不阻断, 留待下轮
        logger.warning("[v3:arcs] 视觉世界补全失败: %s", exc)
        return 0
    got = {str(r.get("arc_id") or ""): str(r.get("world") or "").strip()[:40]
           for r in (data.get("arcs") or [])}
    n = 0
    for a in miss:
        w = got.get(a["arc_id"], "")
        if w:
            a["world"] = w
            n += 1
    if n:
        logger.info("[v3:arcs] 视觉世界补全 %d/%d 弧", n, len(miss))
    return n


def run(job: dict[str, Any]) -> None:
    """v3_arcs 相位本体: 读 v3.lines → 模块 → 骨架 → LLM 创作 → 执法 → 落盘。"""
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline.config import load

    cfg = load()
    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.setdefault("v3", {})
    lines = v3.get("lines") or []
    if not lines:
        raise SystemExit("未分诊 — 先跑 ① 文稿分诊")

    mods = merge_narr_modules(modules_of(lines))
    if not mods:
        raise SystemExit("无有效语态行 — 回 ① 分诊页补标语态")
    arcs = build_skeleton(mods)

    job["progress"] = {"done": 0, "total": 1, "current": "LLM 立意创作中"}
    hints: list[str] = []
    try:
        filled, hints = _llm_fill(arcs, lines, doc.get("book_title") or job["book_title"], job["ep"])
        for a in arcs:
            f = filled.get(a["arc_id"]) or {}
            if f:
                a["narrative"] = f["narrative"]
                a["world"] = f["world"]
                a["metaphor"] = f["metaphor"]
                if f.get("cast"):
                    a["cast"] = f["cast"]
                if f.get("vocab"):
                    a["vocab"] = f["vocab"]
        logger.info("[v3:arcs] LLM 立意 %d/%d 弧 ✓", sum(1 for a in arcs if a["metaphor"]), len(arcs))
    except Exception as exc:  # noqa: BLE001 — LLM 失败退骨架, 人闸手填
        logger.warning("[v3:arcs] LLM 创作失败 (退骨架, 立意待手填): %s", exc)

    # 人设弧=老谭亲身经历: cast 缺老谭则确定性补 (枚举执法的下游保险, 不靠 LLM 自觉)
    ensure_persona_host(arcs, _card_catalog(doc.get("book_title") or job["book_title"]))

    # 漏字段自查+补全 (1105 用户令: 缺 world 是系统的 bug, 系统自己补, 不甩给人)
    world_filled = 0
    try:
        world_filled = fill_missing_worlds(arcs, lines,
                                           doc.get("book_title") or job["book_title"],
                                           job["ep"])
    except Exception as exc:  # noqa: BLE001 — 补全失败不阻断立意落盘
        logger.warning("[v3:arcs] 视觉世界补全异常 (跳过): %s", exc)

    issues = boundary_issues(arcs, lines)
    long_mods = [a for a in mods if len(a["lines"]) > SPLIT_SUGGEST_LINES]
    for m in long_mods:
        hints.append(f"L{m['no_start']}-L{m['no_end']} {m['mode']} 段 {len(m['lines'])} 行, "
                     "可在语义完整处再拆一弧 (需回 ① 重标语态或本页手拆)")

    v3["arcs"] = arcs
    v3["arc_hints"] = hints
    v3["arc_issues"] = issues
    gates = v3.setdefault("gates", {})
    gates["arcs"] = False          # 人闸: 需前端确认放行才开
    v3["arcs_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    shots_mod.save(doc)
    job["result"] = {"arcs": len(arcs), "issues": len(issues),
                     "with_metaphor": sum(1 for a in arcs if a["metaphor"]),
                     "with_cast": sum(1 for a in arcs if a.get("cast")),
                     "world_filled": world_filled,
                     "missing_world": len(missing_worlds(arcs))}
    logger.info("[v3:arcs] %s ep%d: %d 弧 (执法问题 %d) → shots.json v3 ✓",
                job["book_title"], job["ep"], len(arcs), len(issues))
