# -*- coding: utf-8 -*-
"""v3 ③ 分镜文字 (V3) — 句集→镜分组(确定) + 画面句(LLM) + 文字层质检(产线同尺).

分层:
- 确定性: 估时 + 装包成镜 (句集引用, 3-7.5s 密度带) + 卡按句挂载 + 质检。
- LLM: 只写每镜 keyframe_zh (这一镜画什么), 批 8 镜一次 (tag=v3_shots)。
- 质检 = 产线同一把尺 (scene_writer.writer_failures) 过滤出「文字层」可判项 —
  motion/keyframe_en 族归 ④ 导演页, 此处不误报。

零 GPU: 全部文字; 出口闸病族在花钱前亮灯 (原批7 主目标前移)。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

CHARS_PER_SEC = 4.5
SHOT_MIN_S, SHOT_MAX_S = 3.0, 7.5     # 正文镜密度带
BATCH = 8                              # LLM 每批镜数
KF_MIN_CHARS = 80                      # 画面句下限 (产线同尺)

# writer_failures 中属后续相位 (④ 导演才产) 的条目 — 文字层不误报
_LATER_PHASE_MARKERS = ("缺 keyframe_en", "缺 motion_zh", "缺 motion_en",
                        "motion_en 泄漏", "motion_zh 泄漏", "幻影实体", "motion 只动")

CARD_OF_SUGG = {"event": "event", "quote": "quote", "img": "img",
                "book": "quote", "list": "list"}


# ── 确定性: 句 → 镜 ──────────────────────────────────────────────────

def pack_shots(arcs: list[dict[str, Any]], lines: list[dict[str, Any]]
               ) -> tuple[list[dict[str, Any]], list[str]]:
    """弧内句按估时装包成镜 (贪心: 累计 ≥SHOT_MIN_S 且再加超 SHOT_MAX_S 即切)。

    返回 (shots, warnings)。镜号 s1..sN 全局顺序连续。
    """
    line_of = {ln["no"]: ln for ln in lines}
    shots: list[dict[str, Any]] = []
    warns: list[str] = []
    for a in arcs:
        if a.get("type") in ("hook", "cta", "brand"):
            # 非叙事位: 整段一镜 (系列冻结语言, 不切碎)
            refs = [no for no in range(a["no_start"], a["no_end"] + 1)
                    if no in line_of and str(line_of[no].get("text") or "").strip()
                    and line_of[no].get("mode") not in ("section", "card", "img", "unknown")]
            if refs:
                shots.append(_mk_shot(a, refs, line_of))
            continue
        cur: list[int] = []
        for no in range(a["no_start"], a["no_end"] + 1):
            ln = line_of.get(no)
            if not ln or not str(ln.get("text") or "").strip():
                continue
            if str(ln.get("mode") or "") in ("section", "card", "img", "unknown"):
                continue
            cur.append(no)
            dur = est_s(cur, line_of)
            if dur >= SHOT_MIN_S:
                # 再加一句会超上限 → 此处切
                nxt = next((n for n in range(no + 1, a["no_end"] + 1)
                            if n in line_of and str(line_of[n].get("text") or "").strip()), None)
                if nxt is None or dur + est_s([nxt], line_of) > SHOT_MAX_S:
                    shots.append(_mk_shot(a, cur, line_of))
                    cur = []
        if cur:
            dur = est_s(cur, line_of)
            if dur < 1.5 and shots and shots[-1]["arc_id"] == a["arc_id"]:
                # 尾碎并回前一镜 (同弧), 防 <1.5s 死镜 (ep6 14 族同病)
                shots[-1]["line_refs"].extend(cur)
                _refresh_last(shots[-1], line_of)
            else:
                shots.append(_mk_shot(a, cur, line_of))
                if dur < 1.5:
                    warns.append(f"弧 {a['arc_id']} 尾镜仅 {dur}s (<1.5s) — 建议合并或补句")
    for i, s in enumerate(shots, 1):
        s["sid"] = f"s{i}"                     # 全局连续镜号 (s1..sN)
    return shots, warns


def est_s(nos: list[int], line_of: dict[int, dict]) -> float:
    chars = sum(len(str(line_of[n].get("text") or "").strip()) for n in nos if n in line_of)
    return round(chars / CHARS_PER_SEC, 2)


def _mk_shot(a: dict[str, Any], refs: list[int], line_of: dict[int, dict]) -> dict[str, Any]:
    s = {"sid": "", "arc_id": a["arc_id"], "arc_type": a.get("type"),
         "line_refs": list(refs), "est_s": est_s(refs, line_of),
         "narration": "", "keyframe_zh": "", "in_image_text": [],
         "text_layer": [], "status": "planned"}
    _refresh_last(s, line_of)
    return s


def _refresh_last(s: dict[str, Any], line_of: dict[int, dict]) -> None:
    s["narration"] = "".join(str(line_of[n].get("text") or "").strip()
                             for n in s["line_refs"] if n in line_of)
    s["est_s"] = est_s(s["line_refs"], line_of)


# ── 卡: 分诊产物 + 采纳建议 → 挂句 ────────────────────────────────────

def build_cards(v3: dict[str, Any], shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """卡来源二路: ①卡指令行 (v3.cards) + ②分诊采纳的建议 (line.adopted)。
    全部挂到「句」(line_no), 再映射到包含该句的镜 (sid)。"""
    line2sid = {}
    for s in shots:
        for no in s["line_refs"]:
            line2sid[no] = s["sid"]
    cards: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for c in v3.get("cards") or []:
        no = int(c.get("line_no") or 0)
        key = (no, str(c.get("text") or "")[:20])
        if key in seen:
            continue
        seen.add(key)
        cards.append({"cid": c.get("cid") or f"c{len(cards) + 1:02d}",
                      "style": c.get("style") or "list",
                      "text": str(c.get("text") or c.get("name") or "")[:60],
                      "line_no": no, "sid": line2sid.get(no, ""), "from": "marker"})
    for ln in v3.get("lines") or []:
        for kind in ln.get("adopted") or []:
            style = CARD_OF_SUGG.get(kind)
            if not style:
                continue
            no = int(ln["no"])
            text = str(ln.get("text") or "").strip()[:40]
            key = (no, style)
            if key in seen:
                continue
            seen.add(key)
            cards.append({"cid": f"c{len(cards) + 1:02d}", "style": style, "text": text,
                          "line_no": no, "sid": line2sid.get(no, ""), "from": "adopted"})
    return cards


# ── LLM: 画面句 ───────────────────────────────────────────────────────

_SYS = """你是动画分镜的画师。给定「镜」清单 (镜号|所属弧立意|口播原文|目标时长秒),
为每一镜写一句**画面句** keyframe_zh。

硬性要求 (违反即被打回):
1. 80-200 字, 写清楚这一镜**画什么**: 主体 + 动作前一刻的静态构图 + 环境/光源/景深。
2. 必须是**动画起点帧** — 禁写终态 ("已完成""落定""熄灭后" 一律不行)。
3. 禁出现文字载体 (白板/标签/屏幕字/书页字/招牌…), 除非该镜另有申报;
   无字载体写「空白板面/空白留白」。
4. 继承所属弧的视觉世界与隐喻, 相邻镜构图不重复。
5. 禁在画面句里用引号标注镜位/场景名 (「图纸台前」: … 这类写法会被当成画内文字渲染) — 直接描述即可。
6. 每条只输出画面句, 严格 JSON: {"shots":[{"sid":"s1","keyframe_zh":"..."}]}"""


def fill_brand_shots(shots: list[dict[str, Any]], bible: dict | None) -> int:
    """品牌位画面 = 冻结骨架确定性注入 (用户令: 不分配立意≠没画面; 品牌 DNA 不许随机).

    v2 同源: brand.shot_design 给 登山客三拍骨架+圣经色板, 槽长等比拉伸, LLM 零参与。
    """
    from app.services.anim_pipeline import brand
    n = 0
    for s in shots:
        if s.get("arc_type") != "brand":
            continue
        try:
            d = brand.shot_design(float(s.get("est_s") or 5.0), bible)
            s["keyframe_zh"] = str(d["keyframe_zh"])[:600]
            s["anim"] = d.get("anim") or {}
            s["brand_card"] = True                 # v2 字段: K2/H3 走品牌直通
            s["source"] = "brand_card"             # 画面来源标记 (前端显示)
            n += 1
        except Exception as exc:  # noqa: BLE001 — 单镜失败不炸
            logger.warning("[v3:shots] 品牌镜骨架注入失败 %s: %s", s.get("sid"), exc)
    return n


def _batch_prompt(batch: list[dict[str, Any]], arc_of: dict[str, dict], line_of: dict[int, dict]) -> str:
    rows = []
    for s in batch:
        a = arc_of.get(s["arc_id"]) or {}
        text = str(s.get("narration") or "")[:100]
        rows.append(f'{s["sid"]}|{a.get("world", "")}|{a.get("metaphor", "")[:60]}|'
                    f'{text}|{s["est_s"]}s')
    return "镜号|视觉世界|立意|口播原文|目标时长\n" + "\n".join(rows)


def _llm_fill(shots: list[dict[str, Any]], arcs: list[dict[str, Any]],
              line_of: dict[int, dict]) -> int:
    from app.services.llm_service import get_llm_service, _parse_json
    arc_of = {a["arc_id"]: a for a in arcs}
    filled = 0
    for i in range(0, len(shots), BATCH):
        batch = [s for s in shots[i:i + BATCH]
                 if s.get("arc_type") != "brand" and not s.get("keyframe_zh")]
        if not batch:
            continue                     # 品牌镜已由冻结骨架填好, 不进 LLM
        sys_p = _SYS
        if any(s.get("arc_type") in ("hook", "cta") for s in batch):
            # 钩子/行动召: 注入特区宪法 (v2 同一份规则, 非 LLM 自由发挥)
            try:
                from app.services.anim_pipeline.hook_zone import ZONE_RULES
                sys_p += "\n\n【特区宪法 — 钩子/行动召镜必须遵守】\n" + str(ZONE_RULES)
            except Exception:  # noqa: BLE001
                pass
        raw = get_llm_service().chat(sys_p, _batch_prompt(batch, arc_of, line_of),
                                     model="pro", temperature=0.6,
                                     response_format={"type": "json_object"}, tag="v3_shots")
        got = {str(r.get("sid")): str(r.get("keyframe_zh") or "")[:600]
               for r in (_parse_json(raw).get("shots") or [])}
        for s in batch:
            if got.get(s["sid"]):
                s["keyframe_zh"] = got[s["sid"]]
                filled += 1
    return filled


# ── 质检: 产线同尺 (只取文字层可判项) ────────────────────────────────

ARC_LEVEL_KEY = "弧级"      # 无法归因到单镜的场级问题 (宪法配额等)

# ── 误报豁免 (v2 writer_failures 的已知局限, v3 层过滤保 v2 零扰动) ──
_CARRIER_HIT = re.compile(r"含文字载体「(.+?)」")
_BLANK_LIMIT = re.compile(r"空白|留白|无字|未写|空无一字|没有字|不写字|净面|光面")
_NEG_PREFIX = re.compile(r"[无没不][^，。；,;]{0,6}$")   # 紧贴载体词前的否定: 「无反光字迹」
# 引号镜位标注豁免 (实锤: LLM 用「图纸台前」: 标镜位, 不是要渲进画面的文字)
_QUOTED = re.compile(r"[「『\"]([^」』\"]{1,12})[」』\"](\s*[：:])?")
_QUOTE_HIT = re.compile(r"含引号文本")


def _quotes_are_position_labels(text: str) -> bool:
    """所有引号都紧跟冒号 = 镜位标注 (「图纸台前」: …), 非画内文字。"""
    hits = list(_QUOTED.finditer(text or ""))
    return bool(hits) and all(h.group(2) for h in hits)


def _carrier_already_blanked(msg: str, text: str) -> bool:
    """载体词全部出现在「空白/留白/无字」等限定语境里 → 已合规, 非真问题。

    实锤误报: 「窗户玻璃无反光字迹」「格面标签留白无字」「一张空白便签」全被抓。
    """
    m = _CARRIER_HIT.search(msg)
    if not m or not text:
        return False
    word = m.group(1)
    hits = list(re.finditer(re.escape(word), text))
    if not hits:
        return False
    for h in hits:
        pre = text[max(0, h.start() - 14):h.start()]
        post = text[h.end():h.end() + 14]
        if _BLANK_LIMIT.search(pre) or _BLANK_LIMIT.search(post) or _NEG_PREFIX.search(pre):
            continue
        return False          # 任一次出现未限定空白 → 真问题
    return True



def text_layer_issues(shots: list[dict[str, Any]]) -> dict[str, list[str]]:
    """按弧跑 writer_failures, 过滤掉 ④ 导演才产的项。

    返回 {sid: [...]} — 镜级问题按镜归因; 无法归因到镜的场级配额问题归 ARC_LEVEL_KEY
    (实锤: 旧版用 split(' ')[0] 把「分区人物配额: …」当 sid, 44 条场级提醒淹没 14 条镜级真问题)。
    """
    from app.services.anim_pipeline.scene_writer import writer_failures
    by_arc: dict[str, list[dict]] = {}
    for s in shots:
        by_arc.setdefault(s["arc_id"], []).append(s)
    out: dict[str, list[str]] = {}
    for arc_id, arc_shots in by_arc.items():
        # 品牌镜走冻结骨架 (brand.shot_design 确定性产物), 不参与 LLM 文字层质检
        arc_shots = [s for s in arc_shots if not s.get("brand_card")]
        if not arc_shots:
            continue
        fake = {"shots": [{"shot_id": s["sid"], "arc_id": s["arc_id"],
                           "t_start": 0, "keyframe_zh": s["keyframe_zh"],
                           "in_image_text": s.get("in_image_text") or [],
                           "text_layer": s.get("text_layer") or []}
                          for s in arc_shots]}
        try:
            fails = writer_failures(fake, arc_id)
        except Exception as exc:  # noqa: BLE001 — 质检失败不炸产线
            logger.warning("[v3:shots] 弧 %s 质检异常 (跳过): %s", arc_id, exc)
            continue
        valid_sids = {s["sid"] for s in arc_shots}
        text_of = {s["sid"]: str(s.get("keyframe_zh") or "") for s in arc_shots}
        for f in fails:
            if any(m in f for m in _LATER_PHASE_MARKERS):
                continue
            # 误报豁免: 载体已限定「空白/无字」→ 合规; 品牌镜已在上方剔除
            mm = re.match(r"^(s\d+)", str(f))
            _txt = text_of.get(mm.group(1), "") if mm else ""
            if mm and _carrier_already_blanked(str(f), _txt):
                continue
            if _QUOTE_HIT.search(str(f)) and _quotes_are_position_labels(_txt):
                continue          # 引号全是镜位标注 → 非画内文字
            # 只有「以镜号开头」才算镜级 (实锤: 场级「…需≥1 — 补 s1」正文带 s1 被误归)
            m = re.match(r"^(s\d+)\b", str(f))
            key = m.group(1) if (m and m.group(1) in valid_sids) else ARC_LEVEL_KEY
            out.setdefault(key, []).append(str(f)[:160])
    # 弧级排后 (前端先看镜级)
    if ARC_LEVEL_KEY in out:
        out[ARC_LEVEL_KEY] = out.pop(ARC_LEVEL_KEY)
    return out


# ── 相位本体 ─────────────────────────────────────────────────────────

def run(job: dict[str, Any]) -> None:
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline.config import load

    _ = load()
    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.setdefault("v3", {})
    arcs = v3.get("arcs") or []
    lines = v3.get("lines") or []
    if not arcs:
        raise SystemExit("无立意弧 — 先跑 ② 立意")
    if not (v3.get("gates") or {}).get("arcs"):
        raise SystemExit("立意闸未放行 — 回 ② 确认放行后再来")

    shots, warns = pack_shots(arcs, lines)      # 内含全局镜号 s1..sN
    line_of = {ln["no"]: ln for ln in lines}

    job["progress"] = {"done": 0, "total": 1, "current": f"写画面句 ({len(shots)} 镜)"}
    # 品牌位: 冻结骨架确定性注入 (零 LLM); 其余镜才走 LLM
    bible = None
    try:
        from app.services.anim_pipeline import director2
        _bp = director2._bible_cache_path(job["book_title"])
        if _bp.exists():
            import json as _json
            bible = _json.loads(_bp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 圣经缺失退无色彩注入
        bible = None
    n_brand = fill_brand_shots(shots, bible)
    if n_brand:
        logger.info("[v3:shots] 品牌镜骨架注入 %d 镜 (零 LLM)", n_brand)
    filled = n_brand
    try:
        filled += _llm_fill(shots, arcs, line_of)
        logger.info("[v3:shots] 画面句 %d/%d 镜 ✓ (批 %d)", filled, len(shots), BATCH)
    except Exception as exc:  # noqa: BLE001 — LLM 失败留空待人填
        logger.warning("[v3:shots] 画面句 LLM 失败 (留空待手填): %s", exc)

    cards = build_cards(v3, shots)
    issues = text_layer_issues(shots)
    n_issue = sum(len(v) for v in issues.values())

    v3["shots"] = shots
    v3["cards"] = cards
    v3["shot_issues"] = issues
    v3["shot_warnings"] = warns
    v3.setdefault("gates", {})["shots"] = False     # 人闸: 前端放行才开
    shots_mod.save(doc)

    job["result"] = {"shots": len(shots), "filled": filled, "brand": n_brand, "cards": len(cards),
                     "issues": n_issue, "warnings": len(warns)}
    logger.info("[v3:shots] %s ep%d: %d 镜 (画面句 %d, 卡 %d, 质检问题 %d) → shots.json v3 ✓",
                job["book_title"], job["ep"], len(shots), filled, len(cards), n_issue)
