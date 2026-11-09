# -*- coding: utf-8 -*-
"""v3 ① 文稿分诊 (V1) — 语态标注 + 病检 + 五类建议; 一句一行, 三去向。

分层纪律 (LLM 比抖音松教义): 确定性规则 = 执法 (伪指令/时间铁律/长句/清单/合法标记路由),
LLM = 标注与判断 (语态归类/金句相/图建议/书证建议) — LLM 失败退纯确定性层, 不炸产线。
数据: doc["v3"]["lines"] (增字段, v2 命名空间不动); 编辑稿存 v3.lines 工作副本, DB 不动。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── 合法结构标记 (确定性路由, 不进 LLM 不进口播) ────────────────────────
RE_CARD = re.compile(r"^【卡[:：](?P<name>[^】]{1,24})】(?P<body>.*)$")
RE_IMG = re.compile(r"^【图[:：](?P<name>[^】]{1,48})】$")
# 稿件段标 (拆书线节目标题, ep6 实弹: 【0–60 秒｜全新打磨开篇】en-dash+空格变体)
RE_SECTION = re.compile(r"^【\d+\s*[–\-—]\s*\d+\s*[sS秒]?\s*[｜|][^】]*】$")
# writer 注释前缀 (1103 Z+T 白名单): "金句：xxx" 非口播 — 剥前缀, 内容直接成卡
RE_NOTE = re.compile(r"^(金句|口诀|结论|标题|提示|总结|注释|备注)[：:]\s*(?P<body>.+)$")
# 六类统一口径 (用户令): 注释前缀即内容类型, 渲染方式由 shots.KIND_STYLE 决定
_NOTE_KIND = {"金句": "金句", "口诀": "口诀", "结论": "结论", "标题": "标题",
              "提示": "提示", "总结": "总结", "注释": "提示", "备注": "提示"}
RE_ANY_MARKER = re.compile(r"^【[^】]*】")

# ── 确定性病检 ─────────────────────────────────────────────────────────
RE_YEAR = re.compile(r"(19\d{2}|20\d{2})\s*年")
TIME_LINE = 2020            # 北斗星: 案例时间铁律 2020 线 (听众切身感)
LONG_LEN, LONG_PUNCT = 60, 2   # 长句: >60字且标点<2 → 听感歧义风险
LIST_STARTERS = ("第一", "第二", "第三", "首先", "其次", "再次", "最后")

MODES = ("hook", "quote", "comm", "persona", "cta", "brand", "card", "img",
         "note", "section", "unknown")
MODE_ZH = {"hook": "钩子", "quote": "书证", "comm": "评论", "persona": "人设",
           "cta": "行动召", "brand": "牌", "card": "卡指令", "img": "图指令",
           "section": "段标", "note": "注释卡", "unknown": "未分诊"}


def det_check(text: str) -> dict[str, Any] | None:
    """确定性单行病检 (执法层): 返回 issue 或 None。"""
    t = text.strip()
    if not t:
        return None
    if RE_NOTE.match(t):
        return None          # writer 注释行: 剥前缀成卡, 非病
    if RE_ANY_MARKER.match(t) and not (RE_CARD.match(t) or RE_IMG.match(t) or RE_SECTION.match(t)):
        return {"level": "block", "kind": "fake_marker",
                "msg": "伪指令/残留标记 — 非语态、非合法【卡:】【图:】格式",
                "sugg": "清洗整行, 或转为卡内容"}
    m = RE_YEAR.search(t)
    if m and int(m.group(1)) > TIME_LINE:
        return {"level": "warn", "kind": "time_rule",
                "msg": f"案例时间「{m.group(1)}年」违反时间铁律 (2020 线)",
                "sugg": "改为 2020 年前或相对时间 (前年/早些年)"}
    punct = len(re.findall(r"[，。；,;、！？]", t))
    if len(t) > LONG_LEN and punct < LONG_PUNCT:
        return {"level": "warn", "kind": "long_line",
                "msg": f"{len(t)} 字长句标点过少 — 听感歧义风险 (歧义=废句)",
                "sugg": "按语义断句 (≤30 字/句)"}
    return None


def det_list_hint(lines: list[dict[str, Any]]) -> None:
    """确定性清单建议: 连续 ≥2 行以 第一/第二/…/最后 起头 → 首行挂 list 建议。"""
    run_start = None
    for ln in lines:
        starts = any(ln["text"].strip().startswith(s) for s in LIST_STARTERS)
        if starts:
            if run_start is None:
                run_start = ln["no"]
        else:
            run_start = None
    # 简化: 找到任一起头行即挂建议 (LLM 补充更完整判断)
    for ln in lines:
        if any(ln["text"].strip().startswith(s) for s in LIST_STARTERS):
            ln.setdefault("suggs", [])
            if not any(s.get("kind") == "list" for s in ln["suggs"]):
                ln["suggs"].append({"kind": "list",
                                    "text": "成清单排比 — 建议合并制作为剪映清单卡 (口播保留)"})
            break


def route_markers(text: str) -> tuple[str, dict[str, Any] | None]:
    """合法标记路由: 返回 (mode, card/img 记录)。段标/卡/图 = 结构指令, 不进 LLM 不进口播。"""
    t = text.strip()
    m = RE_CARD.match(t)
    if m:
        return "card", {"style": "list", "name": m.group("name").strip(),
                        "text": m.group("body").strip() or m.group("name").strip()}
    if RE_IMG.match(t):
        return "img", {"style": "img", "name": RE_IMG.match(t).group("name").strip(), "text": ""}
    if RE_SECTION.match(t):
        return "section", None
    m = RE_NOTE.match(t)
    if m:
        return "note", {"kind": _NOTE_KIND.get(m.group(1), "提示"),
                        "name": m.group(1), "text": m.group("body").strip()[:60]}
    return "", None


_LLM_SYS = """你是短视频口播文稿的分诊医官。对每一行输出一个判定, 严格 JSON, 不要多余文字。
字段: {"lines":[{"no":行号,"mode":"hook|quote|comm|persona|cta|brand|unknown",
  "issue":null或{"level":"block|warn","kind":"英文短名","msg":"一句中文问题","sugg":"一句中文建议"},
  "suggs":[]}]}
mode 语义 (严格区分, 判错会毁立意):
- hook = 开场钩子 (悬念/冲突/利益点, 只为留人)
- quote = **书里原话直出**: 必须带引号引用原文, 或明写"书里原话/原文/作者写道/书里写"
- comm = **老谭自己的话**: 包括转述书的内容、概括、举例、介绍书和作者、列举清单 —
  即使内容来自书, 只要不是原话引用, 一律归 comm
- persona = **老谭亲身经历自述**: "我当年…""我管市场部时…" 第一人称做过的事
- cta = 行动召唤/关注引导/互动提问
- brand = **系列品牌句/仪式句**: "我是老谭, 读一本书, 多一个硬本事" /
  "我是老谭, 拆一本书, 翻一座山, 咱们下一集见" — 系列冻结, 每集固定位置出现
- unknown = 无法归类

关键区分 (高频误判, 务必照做):
① 介绍书/作者/推荐语 ≠ quote, 是 comm
② 转述"书里有个案例/书里给的解法" ≠ quote (没有原话引号就是转述), 是 comm
③ "我是老谭…" 开头一律 brand, 不是 persona
issue 只报: 结构失衡(连续书证无承接/缺钩子/缺行动召/语态突兀)、明显书面语不宜口播。
suggs 每行给 0-2 条创作建议, kind∈"quote"(对仗短句有金句相)/"img"(**图示**类: 公式/方程、流程图、步骤链、对照表格、多维矩阵、并列清单≥3项 —
这类"结构性信息"一旦念出来听众记不住, 必须上图示; 纯抒情/观点句不要给 img)/
"book"(转述书中观点宜调原句直出)/"event"(核心概念词宜上事件金字), text=一句中文建议。
另给 card_text = 建议实际上屏的卡文字 (严格按 kind 控制长度):
- event 事件金字: ≤8 个字的**核心词** (如「怀疑坎」「体面退出」「丑话清单」), 绝不要整句
- quote 金句卡: ≤30 字, 取句中最有劲的那一段
- img 图示: ≤30 字的公式/图表/流程图/表格内容 (只有这类才出图; 金句清单结论一律不出图)
  且 kind="img" 时必须额外给 diagram = **图示的结构化数据** (树形 JSON):
  {"name":"根","children":[{"name":"分支","children":[{"name":"叶"}]}]}
  例子: 「信任=可信度×可靠度×亲密度÷自我导向」
    → diagram: {"name":"信任","children":[{"name":"可信度"},{"name":"可靠度"},
                 {"name":"亲密度"},{"name":"自我导向"}]}
  规则: 2-3 层, 每个节点 ≤10 字, 总节点 ≤12; 纯公式无维度可拆则 diagram 给 null。
  **跨行清单** (第一项… / 第四到六项… / 最后两项… 分几句讲的同一条清单):
  只在**首行**给一条 img 建议, diagram 必须覆盖后面各行提到的**全部项**——
  实锤 (丑话清单): 只画了首句的前三项, 最后两项"验收标准/评价"整段丢失。
  分句里没进 diagram 的项, 宁可在结构里加一个分支补上。
- list 清单卡: ≤50 字
**card_text 必填**: quote/img/event/list 每条建议必须带 card_text, 缺 card_text 的建议会被系统整条丢弃 (用户采纳了也不建卡 — 1109 ep7 实锤)。"""


def source_hash(script: str) -> str:
    """源稿指纹 (1109): ① 采集, 下游比对 — 改稿后 v3 快照全链过期的判定源。"""
    import hashlib
    return hashlib.md5(str(script or "").encode("utf-8")).hexdigest()[:12]


def invalidate_downstream(v3: dict[str, Any], level: str = "arcs") -> bool:
    """① 页编辑后下游闸回退 (1109 令55 补): 改字/语态 → arcs 级; 改采纳 → shots 级。

    ① 页改字不写 DB (工作副本), 源稿指纹不变 — 但 ②③ 产物基于旧行, 闸必须
    回退强制重跑 (数据保留, ②③ 重跑自然覆盖)。返回是否有闸被动 (toast 用)。
    """
    chain = ("arcs", "shots", "direct") if level == "arcs" else ("shots", "direct")
    hit = False
    gates = v3.setdefault("gates", {})
    for g in chain:
        if gates.get(g):
            hit = True
        gates[g] = False
    return hit


def script_stale(book_ref: str, ep: int) -> bool | None:
    """DB 源稿 ≠ ① 分诊快照指纹 → True (②-⑧ 全链基于老稿); 无指纹/无快照 → None 不判。

    1109 实锤 (顾问 ep7): 用户改稿后 ⑤ TTS 仍读老稿 — ① 跑在改稿之前,
    快照无人作废。此函数 = 源稿指纹闸的判定源 (run_phase + 路由 409 共用)。
    """
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline.config import load
    from app.services.anim_pipeline.planner import fetch_episode, resolve_book
    cfg = load()
    book_id, book_title = resolve_book(cfg.db_path, book_ref)
    if not shots_mod.shots_path(book_title, ep).exists():
        return None
    doc = shots_mod.load_doc(book_title, ep)
    h = str((doc.get("v3") or {}).get("source_hash") or "")
    if not h:
        return None
    row = fetch_episode(cfg.db_path, book_id, ep)
    return source_hash(str(row.get("script_text") or "")) != h


def llm_triage(lines: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """LLM 一次标注 → {no: {mode, issue, suggs}}。失败抛异常由调用方退纯确定性。"""
    from app.services.llm_service import get_llm_service, _parse_json
    body = "\n".join(f'{ln["no"]}|{ln["text"]}' for ln in lines if ln["text"].strip())
    raw = get_llm_service().chat(_LLM_SYS, body, model="flash", temperature=0.2,
                                 response_format={"type": "json_object"}, tag="v3_triage")
    out: dict[int, dict[str, Any]] = {}
    for row in (_parse_json(raw).get("lines") or []):
        try:
            out[int(row["no"])] = {"mode": str(row.get("mode") or "unknown"),
                                   "issue": row.get("issue") or None,
                                   "suggs": list(row.get("suggs") or [])}
        except (KeyError, TypeError, ValueError):
            continue
    return out


_INLINE_MARKER = re.compile(r"【[^】]*】")


def strip_inline_markers(lines: list[dict[str, Any]]) -> int:
    """1108 出口闸: 口播行剥 LLM 自发明行内标注 (ep6 实锤: 【大字字幕】被 TTS 念出)。
    合法标记全是行首且已在 route_markers 路由; 行内【..】只会是发明物。
    注释/结构行 (note/card/img/section) 保原样 — 注释是给用户看的生产信息。"""
    n = 0
    for ln in lines:
        if ln.get("mode") not in ("section", "card", "img", "note") \
                and _INLINE_MARKER.search(str(ln.get("text") or "")):
            ln["text"] = _INLINE_MARKER.sub("", ln["text"]).strip()
            n += 1
    return n


def run(job: dict[str, Any]) -> None:
    """v3_triage 相位本体: 载稿 → 确定性层 → LLM 层 → 合并落盘。"""
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline.planner import fetch_episode, resolve_book
    from app.services.anim_pipeline.config import load

    cfg = load()
    book_id, book_title = resolve_book(cfg.db_path, job["book_id"])
    ep_row = fetch_episode(cfg.db_path, book_id, job["ep"])
    script = str(ep_row.get("script_text") or "")
    if not script.strip():
        raise SystemExit("文稿为空 — 先在文字加工线完成稿件")

    raw_lines = [ln.rstrip() for ln in script.split("\n")]
    lines: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for i, text in enumerate(raw_lines, 1):
        ln: dict[str, Any] = {"no": i, "text": text, "mode": "unknown",
                              "flag": None, "suggs": [], "disp": None}
        mode, routed = route_markers(text)
        if mode:
            ln["mode"] = mode
            ln["disp"] = "routed"
            if routed:
                routed["cid"] = f"c{len(cards) + 1:02d}"
                routed["line_no"] = i
                cards.append(routed)
        ln["flag"] = det_check(text)
        lines.append(ln)

    # LLM 层 (语态 + 补充建议); 失败退确定性层继续 (标注 unknown 由人闸补)
    try:
        job["progress"] = {"done": 0, "total": 1, "current": "LLM 标注中"}
        marked = llm_triage(lines)
        for ln in lines:
            m = marked.get(ln["no"])
            if not m or ln.get("disp") == "routed":
                continue
            if ln["mode"] == "unknown" and m["mode"] in MODES:
                ln["mode"] = m["mode"]
            if not ln["flag"] and m.get("issue"):
                ln["flag"] = m["issue"]
            for s in m.get("suggs") or []:
                if s.get("kind") in ("quote", "img", "book", "event") and s.get("text"):
                    _sug = {"kind": s["kind"], "text": str(s["text"])[:80],
                            "card_text": str(s.get("card_text") or "")[:60]}
                    if s.get("diagram"):
                        _sug["diagram"] = s["diagram"]
                    ln["suggs"].append(_sug)
        logger.info("[v3:triage] LLM 标注 %d 行 ✓", len(marked))
    except Exception as exc:  # noqa: BLE001 — LLM 失败退确定性层, 人闸补 unknown
        logger.warning("[v3:triage] LLM 标注失败 (退确定性层): %s", exc)

    det_list_hint(lines)
    _stripped = strip_inline_markers(lines)
    blocks = sum(1 for ln in lines if ln["flag"] and ln["flag"]["level"] == "block")
    job["result"] = {"lines": len(lines), "blocks": blocks, "routed": len(cards),
                     "stripped_markers": _stripped}

    # 落盘: 只写 v3 命名空间 (doc 不存在则建壳)
    doc = shots_mod.load_doc(book_title, job["ep"]) if shots_mod.shots_path(book_title, job["ep"]).exists() else \
        {"book_title": book_title, "ep": job["ep"], "shots": [], "version": 1}
    doc.setdefault("v3", {})
    doc["v3"]["lines"] = lines
    doc["v3"]["cards"] = cards
    doc["v3"].setdefault("gates", {})
    # 分诊闸: 无未处置 block 即开
    doc["v3"]["gates"]["triage"] = blocks == 0
    doc["v3"]["triage_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _old_hash = str(doc["v3"].get("source_hash") or "")   # 覆盖前采旧指纹
    doc["v3"].pop("lines_edited", None)    # 重导入: ① 页手改被 DB 稿覆盖, 旗标清
    doc["v3"]["source_hash"] = source_hash(script)   # 1109: 源稿指纹 — 下游比对
    # 1109: 源稿指纹变了 (含存量集首采) → ②③④ 产物基于老稿, 闸链回退强制重跑。
    # 数据保留不删 (②③ 重跑自然覆盖); 同稿重跑 (指纹同) 不误伤下游闸。
    if doc["v3"].get("arcs") and _old_hash != doc["v3"]["source_hash"]:
        for _g in ("arcs", "shots", "direct"):
            doc["v3"]["gates"][_g] = False
        logger.info("[v3:triage] 源稿指纹变更 — ②③④ 闸回退 (下游基于老稿, 须重跑)")
    shots_mod.save(doc)
    logger.info("[v3:triage] %s ep%d: %d 行 (block %d, 路由 %d) → shots.json v3 ✓",
                book_title, job["ep"], len(lines), blocks, len(cards))

# ── 长行拆句 (1106 用户令: K2/TTS/H3 共同槽位, 槽 ≤15s; 行是槽的原料) ──
import re as _re

_SENT_END = _re.compile(r'[。！？；!?;]+[」』"”]*')
_CLAUSE = _re.compile(r"[，、——]+")
SPLIT_ABOVE_CHARS = 40        # ≈8.9s @4.5字/s — 超过即拆 (窗 7.5s 装不下)
SPLIT_HARD_CHARS = 60         # ≈13.3s — 句拆后仍超, 退到逗号级


def split_text(t: str) -> list[str]:
    """长文本 → 句级子串 (保留句末标点); 仍超长退逗号级。尾碎片并入前段。"""
    t = str(t or "").strip()
    if len(t) <= SPLIT_ABOVE_CHARS:
        return [t]
    parts, last = [], 0
    for m in _SENT_END.finditer(t):
        parts.append(t[last:m.end()]); last = m.end()
    if last < len(t):
        if parts and len(t[last:]) < 10:
            parts[-1] += t[last:]            # 尾碎片 (<10字) 并入前段
        else:
            parts.append(t[last:])
    out: list[str] = []
    for pce in parts:
        if len(pce) > SPLIT_ABOVE_CHARS:     # 单句仍超窗 → 逗号级 (保逗号)
            c_last = 0
            for cm in _CLAUSE.finditer(pce):
                if cm.end() - c_last >= 12:
                    out.append(pce[c_last:cm.end()]); c_last = cm.end()
            if c_last < len(pce):
                if out and len(pce[c_last:]) < 10:
                    out[-1] += pce[c_last:]
                else:
                    out.append(pce[c_last:])
        else:
            out.append(pce)
    return [x for x in out if x.strip()] or [t]


def split_long_lines(lines: list[dict]) -> tuple[list[dict], dict[int, list[int]]]:
    """v3.lines 长行拆句并重编号。suggs/adopted/disp 只挂首个子行 (卡锚唯一)。

    返回 (新 lines, 旧行号→新行号映射)。arc/card 的区间 remap 由调用方做。
    """
    out: list[dict] = []
    no_map: dict[int, list[int]] = {}
    no = 0
    for ln in lines:
        text = str(ln.get("text") or "")
        mode = str(ln.get("mode") or "unknown")
        if mode in ("section", "card", "img", "note") or len(text.strip()) <= SPLIT_ABOVE_CHARS:
            no += 1
            nl = dict(ln); nl["no"] = no
            out.append(nl)
            no_map[int(ln["no"])] = [no]
            continue
        kids = split_text(text)
        no_map[int(ln["no"])] = []
        for i, k in enumerate(kids):
            no += 1
            nl = {"no": no, "text": k, "mode": mode}
            if i == 0:                        # 标注/建议挂首行 (卡锚唯一)
                for f in ("flag", "suggs", "adopted", "disp"):
                    if ln.get(f) is not None:
                        nl[f] = ln[f]
            else:
                nl["flag"] = None; nl["suggs"] = []; nl["disp"] = ("split:" + str(ln["no"]))                     if ln.get("disp") else None
            no_map[int(ln["no"])].append(no)
            out.append(nl)
    return out, no_map

