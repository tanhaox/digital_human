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
SLOT_MAX_S = 15.0                      # 槽位硬上限 (H3 I2VA 生成上限; 1106 用户令:
                                       # 槽位=K2/TTS/H3 共同轴, 任何镜不得超)
BATCH = 8                              # LLM 每批镜数
KF_MIN_CHARS = 80                      # 画面句下限 (产线同尺)

# writer_failures 中属后续相位 (④ 导演才产) 的条目 — 文字层不误报
_LATER_PHASE_MARKERS = ("缺 keyframe_en", "缺 motion_zh", "缺 motion_en",
                        "motion_en 泄漏", "motion_zh 泄漏", "幻影实体", "motion 只动")

CARD_OF_SUGG = {"event": "event", "quote": "quote", "img": "img",
                "book": "quote", "list": "list"}

# ── 统一口径 (用户令): 内容类型 = 稿件 writer 注释六类; 渲染方式 = 三种通道 ──
# 内容性质 (kind) 与渲染方式 (style) 是两个维度, 不混为一列:
#   金句/口诀 → 金句卡  |  结论 → 事件字  |  标题 → 标题卡
#   提示/总结 → 清单卡
# 六类 = 纯文字, 全部走剪映后期文字 (可编辑、改字不重渲染)
# 图示 = 唯一需要出图的内容: 公式/图表/流程图/表格 — 剪映排不出精确图形,
#        AI 生图会画成乱码 → 走封面同源 HTML 渲染 (用户令: 图表也可以)
KINDS = ("金句", "口诀", "结论", "标题", "提示", "总结", "图示")
KIND_STYLE = {"金句": "quote", "口诀": "quote", "结论": "event",
              "标题": "title", "提示": "list", "总结": "list",
              "图示": "img", "公式": "img"}     # "公式" 为旧名, 兼容存量
# 建议 kind → 内容类型 (分诊建议词表与注释六类对齐)
_SUGG_KIND = {"quote": "金句", "book": "金句", "event": "结论",
              "list": "总结", "img": "图示"}
# 同一句多建议时只建一张卡 (实锤: 配图+清单同句各出一张, 文字完全相同=重叠)
# 取舍 (1105 二次修正): 图示优先。分诊只在"念不出来必须上图示"时才给 img,
# 实锤 L35「期望管理清单·十一项」的完整图示被同行「边界前置」事件字挤掉, 结果
# 整条清单只剩 L41/L43 两张半截卡 → 用户: "后面的字跟不上"。
_SUGG_PRIORITY = {"img": 0, "list": 1, "quote": 2, "book": 3, "event": 4}
# 清单卡的内容性质: 回顾型=总结, 提醒型=提示 (实锤: 丑话清单被标成「总结」)
_SUMMARY_WORDS = ("总结", "回顾", "以上", "这节课", "本集", "总之", "一句话说", "复盘")


def _list_kind(text: str) -> str:
    t = str(text or "")
    return "总结" if any(w in t for w in _SUMMARY_WORDS) else "提示"


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
        # 1106 用户令: 全弧型统一按窗切槽 — 非叙事位冻结的是画面语言, 不是槽长
        # (旧 "整段一镜" 造成 CTA 152字=33.8s 单槽, 超 H3 15s 上限实锤)
        cur: list[int] = []
        for no in range(a["no_start"], a["no_end"] + 1):
            ln = line_of.get(no)
            if not ln or not str(ln.get("text") or "").strip():
                continue
            if str(ln.get("mode") or "") in ("section", "card", "img", "note", "unknown"):
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
        if float(s.get("est_s") or 0) > SLOT_MAX_S:
            warns.append(f"{s['sid']} 槽长 {s['est_s']:.1f}s 超 H3 {SLOT_MAX_S:g}s 上限 — 必须拆行/拆镜")
    # 1108 用户令 (卡镜空镜底): 口播承载已采纳卡建议的镜, 生图前锁定空镜底 —
    # join = 镜.line_refs × line.adopted (③ 装包时点, 早于 K2/H3)。
    # 下游自动闭合: 空镜画面句 → K2 空镜头变体+_EMPTY_NEG (批A-3 判据) + h3 工位路由。
    for s in shots:
        s["card_backdrop"] = any(
            [k for n in s["line_refs"] if (ln := line_of.get(n))
             for k in (ln.get("adopted") or []) if k in CARD_OF_SUGG])
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

_BREAK = "。！？；，、,.!?;"


def sanitize_diagram(node: Any, depth: int = 0, budget: list[int] | None = None,
                     max_children: int = 6) -> dict | None:
    """LLM 图示结构清洗: ≤3 层, 总节点 ≤12, 节点名 ≤10 字; 非法返回 None。"""
    if not isinstance(node, dict) or depth > 2:
        return None
    if budget is None:
        budget = [12]
    if budget[0] <= 0:
        return None
    name = str(node.get("name") or "").strip()[:10]
    if not name:
        return None
    budget[0] -= 1
    out: dict[str, Any] = {"name": name}
    kids = []
    for c in (node.get("children") or [])[:max_children]:
        k = sanitize_diagram(c, depth + 1, budget, max_children)
        if k:
            kids.append(k)
    if kids:
        out["children"] = kids
    return out


def _clip(s: str, n: int) -> str:
    """截断到 n 字内, 尽量在标点处断开 (实锤: 硬截 40 字会切在「这本书叫《值得信」)。"""
    s = str(s or "").strip()
    if len(s) <= n:
        return s
    for i in range(n, 1, -1):
        if s[i - 1] in _BREAK:
            return s[:i].rstrip(_BREAK)
    return s[:n]


def build_cards(v3: dict[str, Any], shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """卡来源二路: ①卡指令行 (v3.cards) + ②分诊采纳的建议 (line.adopted)。
    全部挂到「句」(line_no), 再映射到包含该句的镜 (sid)。"""
    # 1108 用户令 (img 回写防丢): ③ 重跑重建 cards 会抹掉已渲染卡图链接
    # (11-05 渲染 3 张 → 11-06+ 重跑后 img 全 None, 装配失联实锤)。
    # cid 经 _merge_same_list 重编号会漂, 用内容锚 (line_no, text前12) 保留。
    _old_img: dict[tuple, tuple] = {}
    for c in v3.get("cards") or []:
        if c.get("image"):
            _old_img[(int(c.get("line_no") or 0),
                      str(c.get("text") or "")[:12])] = (str(c["image"]),
                                                         c.get("image_file"))
    line2sid = {}
    for s in shots:
        for no in s["line_refs"]:
            line2sid[no] = s["sid"]
    cards: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    # 稿件指令卡 (【卡:】行) 按行去重 — 历史脏数据收敛: 旧版逻辑把生成的卡也
    # 标成了 from=marker, 同一行会留两份 (实锤: s17 配图+清单文字完全相同)
    _marker_by_line: dict[int, dict] = {}
    for c in v3.get("cards") or []:
        if str(c.get("from") or "marker") not in ("marker", ""):
            continue
        _no = int(c.get("line_no") or 0)
        _prev = _marker_by_line.get(_no)
        if _prev is None or (_SUGG_PRIORITY.get(str(c.get("style")), 99)
                             < _SUGG_PRIORITY.get(str(_prev.get("style")), 99)):
            _marker_by_line[_no] = c
    for c in _marker_by_line.values():
        no = int(c.get("line_no") or 0)
        # key 与 adopted 路一致 (行号, 样式) — 实锤: 两路 key 不同型 → marker 与
        # adopted 同一行双卡, 文字完全相同 (s1/s3/s4 全中)
        key = (no, str(c.get("style") or "list"))
        if key in seen:
            continue
        seen.add(key)
        _kind = str(c.get("kind") or c.get("name") or "总结")
        if _kind not in KINDS:
            _kind = "总结"
        cards.append({"cid": c.get("cid") or f"c{len(cards) + 1:02d}",
                      "kind": _kind,                                  # 内容性质 (六类)
                      "style": KIND_STYLE.get(_kind) or c.get("style") or "list",  # 渲染方式
                      "text": _clip(str(c.get("text") or c.get("name") or ""), 60),
                      "line_no": no, "sid": line2sid.get(no, ""), "from": "marker"})
    _marker_lines = set(_marker_by_line.keys())
    for ln in v3.get("lines") or []:
        if int(ln["no"]) in _marker_lines:
            continue          # 该行已有稿件指令卡 (【卡:】), 不再叠建议卡
        adopted = [k for k in (ln.get("adopted") or []) if k in CARD_OF_SUGG]
        if not adopted:
            continue
        # 同一句只建一张卡。1105 实锤 (L41 丑话清单): 确定性 list 建议没有 card_text,
        # 按老优先级 (list=0 最高) 选中后只能拿口播整句硬截 40 字 →
        # 「第一项，会做什么、不会做什么。第二项…第三项」断在半句。
        # 改为: 先看哪条建议带了 card_text, 再比优先级 (同为文字卡时仍文字优先)。
        _ct_of = {k: next((str(g.get("card_text") or "").strip()
                           for g in (ln.get("suggs") or [])
                           if g.get("kind") == k and str(g.get("card_text") or "").strip()), "")
                  for k in adopted}
        kind = min(adopted, key=lambda k: (0 if _ct_of.get(k) else 1,
                                           _SUGG_PRIORITY.get(k, 99)))
        style = CARD_OF_SUGG[kind]
        no = int(ln["no"])
        _ct = _ct_of.get(kind, "")
        if not _ct:
            # 没卡文字就不硬造: 清单卡用口播整句截断 = 断句残片, 宁缺勿滥
            logger.info("[v3:shots] L%s 采纳建议 %s 无 card_text, 跳过建卡", no, kind)
            continue
        text = _clip(_ct, 12 if style == "event" else 40)
        key = (no, style)
        if key in seen:
            continue
        seen.add(key)
        _kind = _SUGG_KIND.get(kind) or (_list_kind(ln.get("text")) if kind == "list"
                                         else "总结")
        _card = {"cid": f"c{len(cards) + 1:02d}",
                 "kind": _kind,
                 "style": style, "text": text,
                 "line_no": no, "sid": line2sid.get(no, ""), "from": "adopted"}
        # 图示类: 带上 LLM 产出的结构 (脑图/流程图), 无则退回文字版式
        _dg = next((g.get("diagram") for g in (ln.get("suggs") or [])
                    if g.get("kind") == kind and g.get("diagram")), None)
        if _dg:
            _card["diagram"] = _dg
        cards.append(_card)
    cards = _merge_same_list(cards)
    # 1108 img 回写防丢: 合并重编号后按内容锚回填渲染链接
    for c in cards:
        _hit = _old_img.get((int(c.get("line_no") or 0),
                             str(c.get("text") or "")[:12]))
        if _hit and not c.get("image"):
            c["image"], c["image_file"] = _hit
    return cards


_LISTISH_KINDS = ("图示", "提示", "总结")


def _grams(t: str, n: int = 4) -> set[str]:
    t = re.sub(r"\s+", "", str(t or ""))
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def _merge_same_list(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一条清单被拆成多张卡 → 只留覆盖最全的一张。

    实锤 (ep5 丑话清单): L35 完整 11 项 / L41 前三项 / L43 4-9 项, 三行各建一张卡,
    同一条清单上三次屏且每次只上一截 (用户: "后面的字跟不上")。
    判定: 相邻 (≤12 行) 的两张清单/图示卡, 卡文字或图示根名有 ≥4 字公共片段 → 同一清单,
    保留覆盖更全的那张 (文字更长 / 节点更多)。cid 合并后重编号。
    """
    def _anchor(c: dict[str, Any]) -> set[str]:
        return _grams(c.get("text")) | _grams((c.get("diagram") or {}).get("name"))

    def _score(c: dict[str, Any]) -> tuple[int, int]:
        return (len(str(c.get("text") or "")), len(str(c.get("diagram") or "")))

    keep: list[dict[str, Any]] = []
    dropped: list[str] = []
    for c in cards:
        if c.get("kind") not in _LISTISH_KINDS:
            keep.append(c)
            continue
        merged = False
        for i, k in enumerate(keep):
            if k.get("kind") not in _LISTISH_KINDS:
                continue
            if abs(int(k.get("line_no") or 0) - int(c.get("line_no") or 0)) > 12:
                continue
            if not (_anchor(c) & _anchor(k)):
                continue
            if _score(c) > _score(k):
                dropped.append(str(k.get("cid")))
                keep[i] = c
            else:
                dropped.append(str(c.get("cid")))
            merged = True
            break
        if not merged:
            keep.append(c)
    if dropped:
        logger.info("[v3:shots] 清单卡合并, 丢弃重复: %s", ",".join(dropped))
    for i, c in enumerate(keep, 1):
        c["cid"] = f"c{i:02d}"
    return keep


# ── 图示补全 (1105 用户令: 清单跨行漏项是机制问题, 系统自己补) ──────────
# 实锤: 丑话清单 11 项被拆成 L35/L41/L43/L45 四行, 首行的 diagram 只画到 7-9,
# 「最后两项 验收标准/评价」整段没进图。分诊 prompt 已收紧; 存量卡用这个定点补。

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14}
_RANGE_RE = re.compile(r"第([一二三四五六七八九十两]{1,3})(?:到|至|-|~)([一二三四五六七八九十两]{1,3})项")
_SINGLE_RE = re.compile(r"第([一二三四五六七八九十两]{1,3})项")
_LAST_RE = re.compile(r"最后([一二三四五六七八九十两]{1,3})项")


def expected_item_count(text: str) -> int:
    """从「第一项/第四到六项/最后两项」这类说法估算清单应有多少项 (0=说不清)。"""
    def _n(w: str) -> int:
        return _CN_NUM.get(w, 0)

    t = str(text or "")
    total = 0
    ranged = set()
    for m in _RANGE_RE.finditer(t):
        a, b = _n(m.group(1)), _n(m.group(2))
        if a and b and b >= a:
            total += b - a + 1
            ranged.update(range(a, b + 1))
    for m in _SINGLE_RE.finditer(t):
        if _n(m.group(1)) in ranged:
            continue
        total += 1
    for m in _LAST_RE.finditer(t):
        total += _n(m.group(1))
    return total if 0 < total <= 20 else 0


_LIST_LINE = re.compile(r"第[一二三四五六七八九十]+|最后|项|清单|条")

_DIAGRAM_SYS = """你在补全一张清单类图示的结构化数据。输入是清单原文 (可能分几句话讲完)
和当前已有的结构。输出一棵覆盖**全部条目**的树:

{"diagram":{"name":"根","children":[{"name":"分支","children":[{"name":"叶"}]}]}}

规则:
1. 2-3 层; 每个节点 ≤10 字; 总节点 ≤14。
2. 原文提到的每一项都要进树, 一项都不能漏 (尤其"最后两项""剩下几项"这类收尾句)。
3. 条目 ≤8 项时可以分组; 超过 8 项**不要分组**, 直接用「根 + 各项」的扁平结构
   (分组会撑爆节点上限, 导致末尾几项被截掉 — 实锤: 丑话清单 11 项丢了验收/评价)。
4. 当前结构仅供参考, 与原文冲突时**以原文为准重画**, 不要照抄。
5. 叶子不要加序号 (不要写「1 会做什么」), 只写项名。
6. 只输出 JSON, 不要解释。"""


def complete_diagram(card: dict[str, Any], lines: list[dict[str, Any]],
                     arcs: list[dict[str, Any]] | None, book_title: str, ep: int) -> bool:
    """按整段原文补全图示结构 (含本条弧后续行)。成功写回 card["diagram"]。"""
    from app.services.llm_service import get_llm_service, _parse_json
    no = int(card.get("line_no") or 0)
    # 跨度不能按弧截 (实锤: 丑话清单 L35 在 A6 末尾, 后续 L39/L41/L43/L45 已属 A7 —
    # 按弧取只拿到首句, 最后两项永远补不进来)。改为顺读: 一路收到不再像清单行的句子为止。
    anchor = _grams(card.get("text") or "")
    span: list[dict[str, Any]] = []
    for ln in lines:
        if int(ln["no"]) < no:
            continue
        t = str(ln.get("text") or "").strip()
        if str(ln.get("mode") or "") == "section" or not t:
            continue
        if int(ln["no"]) > no and not (_LIST_LINE.search(t) or (anchor & _grams(t))):
            break
        span.append(ln)
        if len(span) >= 8:
            break
    if not span:
        return False
    body = "\n".join(f'L{int(l["no"])}|{str(l["text"])[:120]}' for l in span)
    # 项数硬提示: 模型多次只画到一半 (6/11), 把原文的项数算出来钉死
    want = expected_item_count(" ".join(str(l.get("text") or "") for l in span))
    hint = f"\n【本清单原文共 {want} 项, 树里必须有 {want} 个叶子, 少一项算错】" if want else ""
    try:
        raw = get_llm_service().chat(
            _DIAGRAM_SYS,
            # 不喂旧结构: 给了它就会照抄 (实锤: 补全后仍是缺项的三分支版)
            f"《{book_title}》第{ep}集\n\n清单原文:\n{body}{hint}",
            model="flash", temperature=0.3,
            response_format={"type": "json_object"}, tag="v3_diagram")
        data = _parse_json(raw)
    except Exception as exc:  # noqa: BLE001 — 补全失败不阻断
        logger.warning("[v3:shots] 图示补全失败: %s", exc)
        return False
    node = data.get("diagram") if isinstance(data.get("diagram"), dict) else data
    # children 上限必须放宽: 默认 6 会把 11 项清单砍到 6 项 (实锤)
    dg = sanitize_diagram(node, budget=[14], max_children=14)
    if not dg:
        return False
    card["diagram"] = dg
    # 回写源头: 重跑分镜会用 lines[].suggs[].diagram 重建卡, 不回写等于白补
    for ln in lines:
        if int(ln["no"]) == no:
            for g in (ln.get("suggs") or []):
                if g.get("kind") == "img":
                    g["diagram"] = dg
                    break
            break
    return True


# ── LLM: 画面句 ───────────────────────────────────────────────────────

_SYS = """你是动画分镜的画师。给定「镜」清单 (镜号|所属弧立意|口播原文|目标时长秒),
为每一镜写一句**画面句** keyframe_zh。

硬性要求 (违反即被打回):
1. 80-200 字, 写清楚这一镜**画什么**: 主体 + 动作前一刻的静态构图 + 环境/光源/景深。
2. 必须是**动画起点帧** — 禁写终态 ("已完成""落定""熄灭后" 一律不行)。
3. 画面里**不出现任何文字, 也不选「没字就奇怪」的文字载体场景** (1108 源头治理:
   一部电影几乎没有文字特写; K2 会把字抹掉, 空招牌/空信纸在画面里反而假):
   招牌/广告牌/屏幕内容/白板/黑板/信件/翻开的书页/文件报表/清单/菜单/海报/
   告示/横幅/报刊/账本 一律不进画面句。信息用动作/构图/道具形态/人物反应传达
   (合同→握手与按印章的手, 消息→看屏幕人物的表情, 书→合上的封面形态)。
   实在躲不开的环境物件才写「空白白板/熄灭屏幕」等无字形态 (次选, 不当主体)。
   也不要写"申报""二段合成"这类元说明。
   剧情标签 (该拨未拨/先存本金) 是给你理解用的, 不许写进画面句。
   **禁写"大字/巨大字幕/字体"这类字面** — 上屏大字走剪映卡, 不归画面句。
4. 继承所属弧的视觉世界与隐喻; **相邻镜剥掉景别词后的构图相似度必须 <70%** (只换景别词
   = 换皮克隆, 打回)。
5. **画面句里一个引号都不许用** (「」『』"" 全禁): 主题词直接描述, 不要括起来。
6. **景深节奏**: 每 4 镜至少 1 镜大场景 (大全景/俯瞰/辽阔环境, 主体占比小+参照物显纵深);
   特写/桌面级不得超过半数, 连排特写必拆。
7. **开篇留人**: 前 60 秒的镜 (标 [hook]) 优先让人物在场 (背影/手/半个身位都算)。
8. **零运镜词** (1106 s2 大乱套实锤: 画面句写"低机位跟拍同速平移"→视频全按剧本演,
   运镜归导演层, 画师只画定格): 禁 镜头/机位/跟拍/平移/推近/拉远/摇镜/摇摄/
   升降/航拍/俯拍/仰拍/变焦/慢动作 一律不写; 景别词 (特写/中景/全景/大场景) 可用。
9. **单场景一瞬**: 一镜只画一个场所的一个定格瞬间, 禁场景串联 (飞出/进入/回到/
   开进办公室 这类跨地点动作是视频自己演的, 不是画面句写的)。
10. **角色卡名制 (选角)**: 行内有「本弧角色」列且非空时, 画面句中的该角色必须直接
   用卡名指代 (如: 阿特金森的银色轿车), 禁用泛称 (中年男士/西装男人/客户/顾问)
   顶替 — 卡名是跨镜身份锚; 该列为空的镜不写具体人名, 群演用身份词 (路人/店员)。
   **画面句只许用本弧 cast 内的卡名** — 演员名单里未绑本弧的卡名 (含其他弧绑定的)
   不许冒名 (1109 反向闸: 选角走 ② 正门, 画师不擅自定角)。
11. **实体原词制 (词汇表)**: 行内有「本弧实体」列且非空时, 弧内实体一律用表内
   zh 原词指代 (如: 登山杖), 禁同义改写 (手杖/雪杖/登山棍) — 实体词是跨镜视觉
   一致锚 (K2 同实体同画, 英译同实体同词); 表外新实体用自然词即可。
12. **高续写引力姿势不进画面句** (s4 四轮实锤: 捏笔欲写=最强写字邀请, 0.5s 起笔
   行内免责压不住): 画面句只选中性稳定态 (桌上放着合上的笔记本), 禁 欲写/欲击/
   欲掷 等「动作前一刻」持械姿势 — 动作本身交给事件层 (导演层 motion) 正向描述
   (1024 铁律: 正向指定 > 负向禁止)。
13. **角色外观与卡 look 一致** (1108 用户令: 眼镜/胡子跨镜随机漂移实锤): 画面句
   不得给角色新增卡片 look 之外的五官特征 — 卡 look 无眼镜就不画眼镜, 卡 look
   写「下巴光洁无胡须」就不画胡茬; 发型与服装色以卡 look 为准, 弧内每镜同角色
   外观连续一致 (面部特征漂移=废片级不一致)。
14. **手部镜袖口锚** (1108 批A-5: s52/s55 K2 首帧断手实锤 — 画面句只写孤手,
   图上腕部无来处, 视频全程断手漂浮): 画面句出现手/手部特写/手部动作时, 必须写明
   袖口或前臂来处 (颜色接角色卡 look, 如: 深红色冲锋衣的袖口与前臂), 禁孤立悬空手。
15. **大字卡底镜** (1108 用户令, 行标 [卡底]): 该镜口播将叠大字卡, 画面句必须是
   **无人物空镜或简单静物** (桌面静物/风景/纯环境/建筑一角), 画面让位文字层 —
   禁人物、禁手部、禁复杂多主体 (大字压复杂画面=脏, 且人物镜=外观四病重灾区,
   空镜=零病 批A 实证)。用本弧视觉世界内的中性场景承接。
16. **空镜禁人物残留与搁笔** (1109 s3 实锤: 画面句写「玻璃后透出模糊人影」+
   「一支笔搁在纸上」→ 视频把剪影实体化成与全片风格冲突的路人执笔书写, 废片):
   主体不是人物的画面句 (静物/环境/空场) 禁写 人影/剪影/身影/背影/路人/行人
   等任何人物残留词 (剪影连物体剪影也一并换写法, 如「错落的支架轮廓」);
   要人气用环境暗示 (空着的椅子/未收的杯子/亮着的屏幕)。
   空镜静物**禁搁笔** — 一支笔/钢笔/铅笔搁放=书写邀请, 视频会造人来执笔
   (规则12 同因: 静物有笔=邀请函)。
17. 每条只输出画面句, 严格 JSON: {"shots":[{"sid":"s1","keyframe_zh":"..."}]}"""

# 运镜词黑名单 (规则 8 的执法词表; 景别词不算)
KEYFRAME_CAM_WORDS = ("镜头", "机位", "跟拍", "平移", "推近", "拉远", "摇镜",
                      "摇摄", "航拍", "俯拍", "仰拍", "变焦", "慢动作", "一镜到底",
                      "移动镜头")


def keyframe_camera_issues(text: str) -> list[str]:
    """画面句运镜词检查 (1106 s2 实锤: 画面句混"低机位跟拍同速平移"→H3 全演)。"""
    t = str(text or "")
    return [w for w in KEYFRAME_CAM_WORDS if w in t]


def cast_gate_issues(shots: list[dict[str, Any]],
                     arcs: list[dict[str, Any]],
                     book_title: str = "") -> dict[str, list[str]]:
    """卡名闸 (批1 选角): 弧绑定 cast 而画面句零卡名 → 亮灯拦闸。

    泛称 (西装男士) 是跨镜身份漂移源头; 卡名不入句, ⑧ 的 r2v 定妆照路由
    永不命中 (h3._pick_video_station: keyframe 含圣经角色名才走 r2v)。

    1109 反向条款 (s1 实锤): 画面句出现**未绑定**的圣经卡名 → 拦。③ writer
    跨弧偷看同批 cast 列 (A1 hook cast=[] 却写了 老谭+冲锋衣 look) — 选角
    必须走 ② 正门绑定, ③ 不许冒名 (书中有谁上镜是选角决策, 不是画师自由)。
    """
    cast_of = {a["arc_id"]: a.get("cast") or [] for a in arcs}
    cards = _look_catalog(book_title) if book_title else {}
    out: dict[str, list[str]] = {}
    for s in shots:
        if s.get("brand_card"):
            continue                      # 品牌镜走冻结骨架, 不经 LLM
        names = cast_of.get(s.get("arc_id") or "") or []
        kf = str(s.get("keyframe_zh") or "")
        if not kf:
            continue                      # 空画面句由长度闸管, 不双报
        if names and not any(n in kf for n in names):
            out.setdefault(s["sid"], []).append(
                f"{s['sid']} 弧绑定角色 {'/'.join(names)} 未以卡名入画面句 — "
                "用卡名指代该角色 (泛称=身份漂移源头, 且 ⑧ 定妆照路由永不命中)")
        # 反向: 未绑卡名冒名 (含空绑弧冒任何卡名) — 选角走 ② 正门。
        # 1109 子串修 (s7 实锤「几次都修不过」): 短名卡「大卫」是绑定名
        # 「大卫·梅斯特」的子串, 裸 `n in kf` 把已绑定的长名提及误判冒名 →
        # 与正向闸 (卡名必须入画) 永久乒乓, LLM 无论怎么改都过不了。
        # 修法: 先把已绑定卡名从句中掩蔽, 再扫外名; 外名互为子串只报最长。
        _masked = kf
        for _n in names:
            if _n:
                _masked = _masked.replace(_n, "◇")
        _alien = [n for n in cards if n and n in _masked and n not in names]
        _alien = [n for n in _alien
                  if not any(o != n and n in o for o in _alien)]
        if _alien:
            out.setdefault(s["sid"], []).append(
                f"{s['sid']} 画面句出现未绑定卡名 {'/'.join(_alien)} "
                f"(本弧 cast={'/'.join(names) or '空'}) — 该角色上镜是 ② 选角决策: "
                "回 ② 立意页补绑, 或画面句改身份词 (不冒卡名)")
    return out


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


def is_big_scene(text: str) -> bool:
    """大场景判定 — 与 final_gate.depth_quota_issues 同一套词表 (生成期自检用)。"""
    from app.services.anim_pipeline import constitution as CST
    t = str(text or "")
    return any(w in t for w in tuple(CST.depth_big_words()))


def batch_quota(batch: list[dict[str, Any]], zone_of: dict[str, str]) -> str:
    """本批硬配额 (用户令: 规则只在检查器里、写手不知道 → 事后重投, token 暴涨)。

    景深按「每 4 镜至少 1 镜大场景」逐批下硬指标 (逐批向上取整, 总量必然达标);
    开篇 60 秒内的镜点名要求人物在场。
    """
    n = len(batch)
    if not n:
        return ""
    need_big = max(1, -(-n // 4))
    hook = [s["sid"] for s in batch if zone_of.get(s["sid"]) == "hook"]
    lines = [f"【本批硬配额 (违反整批打回重写)】本批 {n} 镜:",
             f"- 景深: 至少 {need_big} 镜必须是**大场景构图** (大全景/俯瞰/辽阔环境, "
             "主体占比小+参照物显纵深); 特写/桌面级不得超过半数。"]
    if hook:
        need_p = max(1, -(-len(hook) * 3 // 4))
        lines.append(f"- 人物: {'/'.join(hook)} 落在开篇 60 秒, 其中至少 {need_p} 镜必须有人物在场 "
                     "(背影/手/半个身位都算)。")
    return "\n".join(lines)


def _batch_prompt(batch: list[dict[str, Any]], arc_of: dict[str, dict],
                  line_of: dict[int, dict], zone_of: dict[str, str],
                  cards: dict[str, str] | None = None) -> str:
    rows = []
    for s in batch:
        a = arc_of.get(s["arc_id"]) or {}
        text = str(s.get("narration") or "")[:100]
        tag = ("[hook]" if zone_of.get(s["sid"]) == "hook" else "") \
            + ("[卡底]" if s.get("card_backdrop") else "")
        # 本弧角色 (批1 选角): 卡名+look 摘要 — 卡名入画面句是身份锚 + ⑧ r2v 路由前提
        cast_txt = ""
        _cast = a.get("cast") or []
        if _cast:
            cast_txt = "、".join(
                f"{n}({cards[n]})" if cards and n in cards else n for n in _cast)
        # 本弧实体词表 (批3 vocab): zh=en 对 — ③画面句用原词, 英译同实体同词
        vocab_txt = "、".join(f'{v["zh"]}={v["en"]}' for v in (a.get("vocab") or []))
        rows.append(f'{s["sid"]}{tag}|{a.get("world", "")}|{a.get("metaphor", "")[:60]}|'
                    f'{str(a.get("narrative") or "")[:40]}|{cast_txt}|{vocab_txt}|{text}|{s["est_s"]}s')
    quota = batch_quota(batch, zone_of)
    # narrative (本弧讲什么) 必须进提示 — 实锤 s2: 只给世界+隐喻, 写手不知道
    # 弧的叙事任务, 画面接不住口播爆点 (客户拍桌画成了一个人发呆)
    return (quota + "\n" if quota else "") \
        + "镜号|视觉世界|立意|本弧讲什么|本弧角色|本弧实体|口播原文|目标时长\n" \
        + "\n".join(rows)


def _llm_fill(shots: list[dict[str, Any]], arcs: list[dict[str, Any]],
              line_of: dict[int, dict], job: dict[str, Any] | None = None,
              cards: dict[str, str] | None = None) -> int:
    from app.services.llm_service import get_llm_service, _parse_json
    arc_of = {a["arc_id"]: a for a in arcs}
    # 时间分区 (前 60s = hook): 用句长估时累加, 与 QC 同一把尺
    zone_of: dict[str, str] = {}
    acc = 0.0
    for s in shots:
        zone_of[s["sid"]] = "hook" if acc < 60 else ""
        acc += float(s.get("est_s") or 0)
    total_b = -(-len(shots) // BATCH)
    filled = 0
    for bi, i in enumerate(range(0, len(shots), BATCH)):
        if job is not None and job.get("cancel"):
            raise SystemExit("已取消")      # 批间生效 (面板 ⛔)
        batch = [s for s in shots[i:i + BATCH]
                 if s.get("arc_type") != "brand" and not s.get("keyframe_zh")]
        if not batch:
            continue                     # 品牌镜已由冻结骨架填好, 不进 LLM
        if job is not None:
            job["progress"] = {"done": bi, "total": total_b,
                               "current": f"画面句 {batch[0]['sid']}.."}
        sys_p = _SYS
        if any(s.get("arc_type") in ("hook", "cta") for s in batch):
            # 钩子/行动召: 注入特区宪法 (v2 同一份规则, 非 LLM 自由发挥)
            try:
                from app.services.anim_pipeline.hook_zone import ZONE_RULES
                sys_p += "\n\n【特区宪法 — 钩子/行动召镜必须遵守】\n" + str(ZONE_RULES)
            except Exception:  # noqa: BLE001
                pass
        prompt = _batch_prompt(batch, arc_of, line_of, zone_of, cards)
        raw = get_llm_service().chat(sys_p, prompt,
                                     model="pro", temperature=0.6,
                                     response_format={"type": "json_object"}, tag="v3_shots")
        got = {str(r.get("sid")): str(r.get("keyframe_zh") or "")[:600]
               for r in (_parse_json(raw).get("shots") or [])}
        # 批内自检 (用户令: 机制放在生成时, 不留给事后整集重掷):
        # 景深配额/画面句长度不达标 → 只重投这一批一次 (代价 1/5 集, 不是一整集)
        need_big = max(1, -(-len(batch) // 4))
        n_big = sum(1 for t in got.values() if is_big_scene(t))
        short = [sid for sid, t in got.items() if t and len(t) < KF_MIN_CHARS]
        if n_big < need_big or short:
            probs = []
            if n_big < need_big:
                probs.append(f"大场景 {n_big}/{len(batch)} (至少要 {need_big} 镜全景/俯瞰/辽阔环境)")
            if short:
                probs.append(f"{'/'.join(short)} 画面句不足 {KF_MIN_CHARS} 字")
            logger.info("[v3:shots] 批 %s 不达标 (%s), 补投一次", batch[0]["sid"], "; ".join(probs))
            try:
                raw2 = get_llm_service().chat(
                    sys_p, prompt + "\n【上一版不达标: " + "; ".join(probs) + "。整批重写。】",
                    model="pro", temperature=0.5,
                    response_format={"type": "json_object"}, tag="v3_shots")
                got2 = {str(r.get("sid")): str(r.get("keyframe_zh") or "")[:600]
                        for r in (_parse_json(raw2).get("shots") or [])}
                ok2 = (sum(1 for t in got2.values() if is_big_scene(t)) >= need_big
                       and not any(t and len(t) < KF_MIN_CHARS for t in got2.values()))
                if ok2:
                    got = got2
            except Exception as exc:  # noqa: BLE001 — 补投失败沿用首版
                logger.warning("[v3:shots] 批补投失败 (沿用首版): %s", exc)
        for s in batch:
            if got.get(s["sid"]):
                s["keyframe_zh"] = got[s["sid"]]
                filled += 1
    return filled


# ── 质检: 产线同尺 (只取文字层可判项) ────────────────────────────────

ARC_LEVEL_KEY = "弧级"      # 无法归因到单镜的场级问题 (宪法配额等)

# 1105 用户令 (v3 口径调整, v2 不动): 弧 ≠ 场。
# v3 弧是按语态切的立意单元 — ep5 实测 34 镜/17 弧 (平均 2 镜), 其中 11 个弧只有 1 镜;
# 把 v2 的场级配额 (每场 1-2 镜申报 / 每场有人物 / 每场大场景) 按弧套上去 = 对小弧强制 100%。
#   · 图内字通道在 v3 停用 (上屏字统一走剪映卡, 图示走 PNG) → 零申报不再报;
#   · 人物/景深配额改全集口径 (一次算全片, 与 final_gate 全场尺同源)。
_ARC_QUOTA_MARKERS = ("整场零申报", "分区人物配额", "景深配额")
# 提示词口径「背影/手/半个身位都算人物」→ 检查器必须同口径
_PERSON_EXTRA = ("背影", "侧影", "侧身", "身位", "半身", "人影", "人员",
                 "工程师", "顾问", "客户", "工人", "司机", "柜员", "地勤")
_CARRIER_MSG = re.compile(r"^(s\d+) 画面句含文字载体「(.+?)」但未申报 in_image_text")
_QUOTE_IN_TEXT = re.compile(r"[「『\"“][^」』\"”]{1,20}[」』\"”]")

# ── 误报豁免 (v2 writer_failures 的已知局限, v3 层过滤保 v2 零扰动) ──
_CARRIER_HIT = re.compile(r"含文字载体「(.+?)」")
# 1107 补: 熄灭/漆黑/黑屏 = 屏幕类载体的「无内容」形态 (ep5 s20 大屏幕漆黑/
# s55 屏幕熄灭 误报实锤) — 与「空白/留白」同权豁免
_BLANK_LIMIT = re.compile(r"空白|留白|无字|无文字|未写|空无一字|没有字|不写字|净面|光面"
                          r"|熄灭|漆黑|黑屏|全黑")
# 载体旁出现「写着/显示/字样」等内容动词 → 即便别处写了空白, 也不算同一无字物件
_TEXT_CONTENT = re.compile(r"写着|写了|字样|字迹|写满|刻着|刻字|显示(?!器|屏)|印着|标着")
_NEG_PREFIX = re.compile(r"[无没不][^，。；,;]{0,6}$")   # 紧贴载体词前的否定: 「无反光字迹」
# 引号镜位标注豁免 (实锤: LLM 用「图纸台前」: 标镜位, 不是要渲进画面的文字)
_QUOTED = re.compile(r"[「『\"]([^」』\"]{1,12})[」』\"](\s*[：:])?")
_QUOTE_HIT = re.compile(r"含引号文本")

# ── 终态词形态豁免 (1107 实弹): v2 尺 (_ENDSTATE_RE) 抓字面「定格|停在」,
# 分不清「定格在动作前一刻」(合规起点帧) 与「已完成定格」(真终态)。
# ep5 ③ 新码 9 连报实锤全是前者 — 补形态判定, v2 尺零扰动。 ──
_PREBEAT_RE = re.compile(r"(定格|停在)[^。]{0,20}(一瞬|一刻|一秒|即将|将发未发)")


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
    blank_n = 0
    for h in hits:
        # 窗口收窄 (实锤: 14 字窗口会把上一句的「无文字」算到同句第二个载体头上)
        pre = text[max(0, h.start() - 8):h.start()]
        post = text[h.end():h.end() + 12]
        # 含载体自身的窄窗口 — 实锤: 「空白板面」的「空白」跨在载体词上,
        # 只查前后文永远看不到 → 7 镜假红字 (白板 的 白 就是 空白 的白)
        span = text[max(0, h.start() - 2):h.end() + 2]
        if (_BLANK_LIMIT.search(span) or _BLANK_LIMIT.search(pre)
                or _BLANK_LIMIT.search(post) or _NEG_PREFIX.search(pre)):
            blank_n += 1
    if blank_n == len(hits):
        return True
    # 同一载体在别处已明确留白, 且全句无内容动词 → 同一无字物件 (「立式白板板面空白…白板板面微微反光」)
    return bool(blank_n) and not _TEXT_CONTENT.search(text)



def has_blocking_issues(issues: dict[str, list[str]] | None) -> bool:
    """闸只看镜级: 弧级是策略提醒 (宪法配额, 人检可保留), 不拦闸。

    实锤: 旧实现 `not issues` 把弧级提醒也算进闸 → 镜级清零后闸仍锁死,
    而弧级 (人物/景深配额) 在 v3 是全集口径的长期目标, 会永久挡路。
    """
    return any(v for k, v in (issues or {}).items() if k != ARC_LEVEL_KEY)


def text_layer_issues(shots: list[dict[str, Any]],
                      book_title: str = "") -> dict[str, list[str]]:
    """按弧跑 writer_failures, 过滤掉 ④ 导演才产的项。

    返回 {sid: [...]} — 镜级问题按镜归因; 无法归因到镜的场级配额问题归 ARC_LEVEL_KEY
    (实锤: 旧版用 split(' ')[0] 把「分区人物配额: …」当 sid, 44 条场级提醒淹没 14 条镜级真问题)。
    book_title (1109 令53): 空镜人物残留/搁笔检查的人物判定需要圣经角色名 (v2 镜缺省零扰动)。
    """
    from app.services.anim_pipeline.scene_writer import writer_failures
    # 1105 修 (ep5 实锤): v3 文字阶段还没有实测时间, 旧实现给每镜假 t_start=0 →
    # constitution.zone_of 把所有镜都判进「hook 区 (0-60s)」→ 分区人物配额对着 1 镜弧
    # 要求 100% 有人物, 9 条假警报。改用句长估时累加出与成片同比例的假时间轴。
    t0_of: dict[str, float] = {}
    _t = 0.0
    for s in shots:
        t0_of[str(s["sid"])] = _t
        _t += float(s.get("est_s") or 0)
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
                           "t_start": t0_of.get(str(s["sid"]), 0.0),
                           "keyframe_zh": s["keyframe_zh"],
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
            if any(m in f for m in _ARC_QUOTA_MARKERS):
                continue          # 弧级配额走全集口径, 循环外统一算
            # 误报豁免: 载体已限定「空白/无字」→ 合规; 品牌镜已在上方剔除
            mm = re.match(r"^(s\d+)\b", str(f))
            _txt = text_of.get(mm.group(1), "") if mm else ""
            if mm and _carrier_already_blanked(str(f), _txt):
                continue
            if "含终态词" in str(f) and _PREBEAT_RE.search(_txt):
                continue          # 定格在…前一刻/一瞬 = 合法起点帧表述
            if _QUOTE_HIT.search(str(f)) and _quotes_are_position_labels(_txt):
                continue          # 引号全是镜位标注 → 非画内文字
            # 只有「以镜号开头」才算镜级 (实锤: 场级「…需≥1 — 补 s1」正文带 s1 被误归)
            m = re.match(r"^(s\d+)\b", str(f))
            key = m.group(1) if (m and m.group(1) in valid_sids) else ARC_LEVEL_KEY
            txt = str(f)
            cm = _CARRIER_MSG.match(txt)   # v3 无申报通道 → 1108 源头治理: 换陈设优先
            if cm:
                txt = (f"{cm.group(1)} 画面句含文字载体「{cm.group(2)}」— 不选文字载体"
                       f"场景 (空载体反而假): 换无字陈设或用动作/形态传达; 实在躲不开"
                       f"才写「空白{cm.group(2)}」"
                       "(v3 图内字通道已停用, 上屏字走剪映卡/图示 PNG)")
            elif "含引号文本" in txt:
                _q = _QUOTE_IN_TEXT.search(text_of.get(key, ""))
                txt = (f"{key} 画面句含引号文字{_q.group(0) if _q else ''} — "
                       "v3 图内字通道已停用: 删掉引号, 改成普通描述 (上屏字走剪映卡)")
            elif "大字" in txt and "字面" in txt:
                txt = (f"{key} 画面句出现「大字/字幕」字面 — v3 上屏大字走剪映卡, "
                       "画面句只描述构图, 不描述字")
            out.setdefault(key, []).append(txt[:160])
    # ── 全集口径配额 (一次算全片) ─────────────────────────────────────
    arc_q: list[str] = []
    try:
        from app.services.anim_pipeline.final_gate import (depth_quota_issues,
                                                           zone_quota_issues)
        # 1109 修 (ep7 配额「修不过」实锤): book_title 必须带上 — zone_quota_issues
        # 靠它把圣经卡名加进人物词表; 缺了 → 只写卡名的镜 (选角律的正确写法)
        # 全被数成「无人」→ 配额永远差数, 点名镜改一万遍也修不过。
        all_fake = {"book_title": book_title,
                    "shots": [{"shot_id": s["sid"], "arc_id": "",
                               "brand_card": s.get("brand_card"),
                               "t_start": t0_of.get(str(s["sid"]), 0.0),
                               "keyframe_zh": s.get("keyframe_zh"),
                               "camera": s.get("camera"),
                               "in_image_text": []} for s in shots]}
        # 人物词表补齐提示词里的口径 (背影/半个身位/手 都算人物) — 实锤: 写手写了
        # 「工程师背影」「地勤人员背影」, 检查器不认 → 4/7 假警报
        arc_q = (zone_quota_issues(all_fake, extra_words=_PERSON_EXTRA)
                 + depth_quota_issues(all_fake, arc_id=None))
        arc_q = [x.replace("场[", "全集[").replace("景深配额: 场 ", "景深配额: 全集 ")
                 for x in arc_q]
    except Exception as exc:  # noqa: BLE001 — 质检失败不炸产线
        logger.warning("[v3:shots] 全集配额质检异常 (跳过): %s", exc)
    if arc_q:
        out.setdefault(ARC_LEVEL_KEY, []).extend(arc_q)
    # ── 1109 令53: 空镜人物残留/搁笔确定性检查 (s3 实锤: 空镜冒 2D 路人) ──
    # 人物判定不上 extra_names (③ 期 cast 可后编辑): 宪法词表+圣经名 (book_title)。
    try:
        from app.services.anim_pipeline.k2 import _scene_has_person
        for s in shots:
            if s.get("brand_card"):
                continue
            kf = str(s.get("keyframe_zh") or "")
            if not kf or _scene_has_person(kf, book_title):
                continue
            sid = str(s["sid"])
            _res = [w for w in ("人影", "剪影", "背影") if w in kf]
            if _res:
                out.setdefault(sid, []).append(
                    f"{sid} 空镜残留人物词 ({'/'.join(_res)}) — 人物词不上桌才防视频冒人 "
                    "(1109 s3 实锤): 删掉, 或换空椅/未收的杯子/亮着的屏幕等环境暗示")
            if any(p in kf for p in ("钢笔", "铅笔", "圆珠笔", "毛笔", "马克笔",
                                     "粉笔", "一支笔", "一枝笔")):
                out.setdefault(sid, []).append(
                    f"{sid} 空镜搁笔 — 笔在画=书写邀请, 视频会造人执笔 (s3 实锤): "
                    "静物镜去笔; 需要笔的镜让人物执笔")
    except Exception as exc:  # noqa: BLE001 — 质检失败不炸产线
        logger.warning("[v3:shots] 空镜残留检查异常 (跳过): %s", exc)
    # 弧级排后 (前端先看镜级)
    if ARC_LEVEL_KEY in out:
        out[ARC_LEVEL_KEY] = out.pop(ARC_LEVEL_KEY)
    return out


# ── 1109 用户令: 问题要给解法 — 一键修 (LLM 定点重写) / 一键拆行 ────────

def _look_catalog(book_title: str) -> dict[str, str]:
    """圣经角色卡 look 摘要 ({卡名: look[:120]}) — run()/fix/拆行共用的组装件。"""
    try:
        import json as _json

        from app.services.anim_pipeline.director2 import _bible_cache_path
        bible = _json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
        return {str(c.get("name") or "").strip(): str(c.get("look") or "").strip()[:120]
                for c in (bible.get("characters") or [])
                if str(c.get("name") or "").strip()}
    except Exception:  # noqa: BLE001 — 无圣经 = 空目录 (行内不注 look)
        return {}


def _shot_feedback(s: dict[str, Any],
                   iss: dict[str, list[str]]) -> list[str]:
    """镜级待修清单 = 质检命中 + 运镜词 + 长度 (80-200)。"""
    out = [str(t) for t in (iss.get(str(s.get("sid"))) or [])]
    cam = keyframe_camera_issues(str(s.get("keyframe_zh") or ""))
    if cam:
        out.append(f"画面句混运镜词 {'/'.join(cam)} — 只画定格, 运镜归导演层")
    n = len(str(s.get("keyframe_zh") or "").strip())
    if n and n < KF_MIN_CHARS:
        out.append(f"画面句 {n} 字不足 {KF_MIN_CHARS} 下限")
    elif n > 220:
        out.append(f"画面句 {n} 字超 200 上限")
    return out


_FIX_SYS_ADDENDUM = """

【修复模式 (1109 用户令: 质检问题必须给解法, 不只列出)】
输入行 = 镜号[标签]|视觉世界|立意|本弧讲什么|本弧角色|本弧实体|口播原文|目标时长|旧画面句|质检问题
任务: 只消除「质检问题」列点名的毛病, **最小改动** — 场景/主体/构图/光源保持旧画面句原样
(修复≠重做: 禁换方案、禁换场景); 重写后的整句仍须满足上方全部硬性要求 (80-200字/起点帧/
零文字载体/空镜零人物残留零搁笔/零运镜词/零引号)。
质检问题点名的改动优先于「保持原样」: 如「卡名入画」须添人物时, 在原场景里让该角色入画即可
(人物一入画, 空镜禁令随之解除, 不算换方案)。
每条只输出画面句, 严格 JSON: {"shots":[{"sid":"s1","keyframe_zh":"..."}]}"""

_ARC_FIX_RE = re.compile(r"补 ((?:s\d+)(?:/s\d+)*)")


def _arc_quota_targets(iss: dict[str, list[str]],
                       cast_by_sid: "dict[str, list] | None" = None
                       ) -> dict[str, list[str]]:
    """弧级问题 → 点名镜反馈 (1109 令54 补): 分区人物配额等自带「补 s3/s4/…」
    修复指令 (final_gate.zone_quota_issues 确定性生成, 格式同源可控) —
    分发为点名镜的「改人物镜」反馈, 进 fix_shot_text 的 LLM 输入。

    1109 修 (hint 按 cast 分流): 无绑定 cast 的弧若按旧提示「卡名入画」,
    写了卡名 → 反向闸拦 (冒名) ↔ 删卡名 → 配额再拦 = 永久乒乓无解;
    无 cast 的点名镜须指明「泛称人物, 禁用卡名」这条唯一出路。"""
    out: dict[str, list[str]] = {}
    for msg in iss.get(ARC_LEVEL_KEY) or []:
        m = _ARC_FIX_RE.search(str(msg))
        if not m:
            continue
        for sid in re.findall(r"s\d+", m.group(1)):
            if (cast_by_sid or {}).get(sid):
                hint = ("分区人物配额不足 — 本镜改为人物镜: 弧绑定角色入画 "
                        "(卡名+定妆锚同句, 主体可小, 场景构图保住)")
            else:
                hint = ("分区人物配额不足 — 本镜改为人物镜: 本弧无绑定角色, "
                        "写一位泛称人物 (如 一位职场男士/一位职业女性, 只描述"
                        "身份与外貌, 禁用任何圣经卡名 — 卡名=选角, 归 ② 管)")
            out.setdefault(sid, []).append(hint)
    return out


def fix_shot_text(doc: dict[str, Any], sids: list[str],
                  book_title: str = "") -> dict[str, Any]:
    """质检问题一键修 (1109 用户令): LLM 按问题清单定点重写画面句。

    与 ♻️ 列队重做 (redo, 换方案) 不同: 修复保场景保构图只消除违例;
    应用后仍带问题的镜带反馈补投一次。只动 doc, 落盘归调用方 (ep_guard 内)。
    返回 {fixed: [sid], unfixed: {sid: [残留问题]}, issues, gate}。
    """
    from app.services.llm_service import get_llm_service, _parse_json
    v3 = doc.setdefault("v3", {})
    shots = v3.get("shots") or []
    arcs = v3.get("arcs") or []
    line_of = {ln["no"]: ln for ln in (v3.get("lines") or [])}
    arc_of = {a["arc_id"]: a for a in arcs}
    want = {str(x) for x in sids}
    iss_all = text_layer_issues(shots, book_title)
    # 1109 补 (s13 实锤): 卡名闸同入修复宇宙 — 否则纯卡名问题的镜文字层干净,
    # 反馈退化「画面不满意」, LLM 只能化妆式微调, 页面条目永不消失
    for _sid, _lst in cast_gate_issues(shots, arcs, book_title).items():
        iss_all.setdefault(_sid, []).extend(_lst)
    # 1109 令54 补: 弧级问题 (分区人物配额「补 sN/sN」) 分发为点名镜反馈
    _cast_by_sid = {str(s.get("sid")): (arc_of.get(s.get("arc_id")) or {}).get("cast") or []
                    for s in shots}
    _arc_fb = _arc_quota_targets(iss_all, _cast_by_sid)
    todo = [s for s in shots
            if str(s.get("sid")) in want and not s.get("brand_card")]
    if not todo:
        return {"fixed": [], "unfixed": {}, "issues": iss_all,
                "gate": not has_blocking_issues(iss_all)}
    cards = _look_catalog(book_title)
    # hook 分区 (与 _llm_fill 同尺: 句长估时累加, 前 60s)
    zone_of: dict[str, str] = {}
    acc = 0.0
    for s in shots:
        zone_of[str(s.get("sid"))] = "hook" if acc < 60 else ""
        acc += float(s.get("est_s") or 0)

    def _rows(batch: list[dict[str, Any]]) -> str:
        rows = []
        for s in batch:
            a = arc_of.get(s.get("arc_id")) or {}
            tag = ("[hook]" if zone_of.get(str(s.get("sid"))) == "hook" else "") \
                + ("[卡底]" if s.get("card_backdrop") else "")
            cast_txt = "、".join(
                f"{n}({cards[n]})" if cards.get(str(n)) else str(n)
                for n in (a.get("cast") or []))
            vocab_txt = "、".join(f'{v.get("zh")}={v.get("en")}'
                                  for v in (a.get("vocab") or []))
            text = " ".join(str((line_of.get(n) or {}).get("text") or "")
                            for n in (s.get("line_refs") or []))[:100]
            _fb = (_shot_feedback(s, iss_all)
                   + _arc_fb.get(str(s["sid"]), []))
            if str(s["sid"]) in dropped:   # 1109 修: 拒稿原因上桌, 不让 LLM 盲猜
                _fb.append(f"上一版输出仅 {dropped[str(s['sid'])]} 字被系统拒收 "
                           "(不在 80-200 字窗) — 保场景保构图写满 80 字以上, "
                           "用环境/光影/前后景细节补足")
            fb = "; ".join(_fb) or "画面不满意"
            rows.append(
                f'{s["sid"]}{tag}|{a.get("world", "")}|{str(a.get("metaphor") or "")[:60]}|'
                f'{str(a.get("narrative") or "")[:40]}|{cast_txt}|{vocab_txt}|{text}|'
                f'{s.get("est_s")}s|{str(s.get("keyframe_zh") or "")[:160]}|{fb}')
        return ("镜号|视觉世界|立意|本弧讲什么|本弧角色|本弧实体|口播原文|目标时长|旧画面句|质检问题\n"
                + "\n".join(rows))

    sys_p = _SYS
    if any(zone_of.get(str(s.get("sid"))) == "hook" for s in todo):
        try:
            from app.services.anim_pipeline.hook_zone import ZONE_RULES
            sys_p += "\n\n【特区宪法 — 钩子/行动召镜必须遵守】\n" + str(ZONE_RULES)
        except Exception:  # noqa: BLE001
            pass
    sys_p += _FIX_SYS_ADDENDUM

    def _ask(batch: list[dict[str, Any]]) -> dict[str, str]:
        raw = get_llm_service().chat(
            sys_p, _rows(batch), model="pro", temperature=0.4,
            response_format={"type": "json_object"}, tag="v3_fix")
        return {str(r.get("sid")): str(r.get("keyframe_zh") or "").strip()
                for r in (_parse_json(raw).get("shots") or [])}

    def _still_bad(s: dict[str, Any], zh: str) -> list[str]:
        probe = {**s, "keyframe_zh": zh}
        out = _shot_feedback(probe, text_layer_issues([probe], book_title))
        # 1109 补: 卡名闸同步进复检 — 与页面条目同宇宙, 闸过了才许报 fixed
        out += _shot_feedback(probe, cast_gate_issues([probe], arcs, book_title))
        return out

    applied: dict[str, str] = {}
    dropped: dict[str, int] = {}   # 1109 修: 短于下限被静默拒收的输出 — 复投带反馈
    got = _ask(todo)
    for s in todo:
        zh = got.get(str(s["sid"]))
        if zh and len(zh) >= KF_MIN_CHARS:
            applied[str(s["sid"])] = zh[:600]
        elif zh:
            dropped[str(s["sid"])] = len(zh)
    retry = [s for s in todo
             if str(s["sid"]) not in applied
             or _still_bad(s, applied[str(s["sid"])] )]
    if retry:  # 带反馈补投一次 (与 ③ 自愈环同构)
        got2 = _ask(retry)
        for s in retry:
            zh = got2.get(str(s["sid"]))
            if zh and len(zh) >= KF_MIN_CHARS:
                applied[str(s["sid"])] = zh[:600]
    by_sid = {str(s["sid"]): s for s in shots}
    fixed = []
    for sid, zh in applied.items():
        s = by_sid.get(sid)
        if s is not None and zh != str(s.get("keyframe_zh") or ""):
            s["keyframe_zh"] = zh
            fixed.append(sid)
        elif s is not None and zh == str(s.get("keyframe_zh") or ""):
            fixed.append(sid)   # LLM 判无需改 (原句即合规/已按新规豁免)
    issues = text_layer_issues(shots, book_title)
    for sid, lst in cast_gate_issues(shots, arcs, book_title).items():
        issues.setdefault(sid, []).extend(lst)
    v3["shot_issues"] = issues
    v3.setdefault("gates", {})["shots"] = not has_blocking_issues(issues)
    unfixed = {sid: _shot_feedback(by_sid[sid], issues)
               for sid in fixed if _shot_feedback(by_sid[sid], issues)}
    # 1109 令54 补: 弧级配额复检 — 修完仍点名 → 残留上报 (与页面条目同宇宙)
    for sid, lst in _arc_quota_targets(issues, _cast_by_sid).items():
        if sid in fixed:
            unfixed.setdefault(sid, []).extend(lst)
    return {"fixed": fixed, "unfixed": unfixed, "issues": issues,
            "gate": not has_blocking_issues(issues)}


def _split_halves(text: str) -> tuple[str, str] | None:
    """超长口播行按标点对半拆 (。！？； 优先, 退 ，、：); 拆不出返回 None。"""
    t = str(text or "").strip()
    if len(t) < 20:
        return None
    mid = len(t) // 2
    for stops in ("。！？；", "，、："):
        best = None
        for i, ch in enumerate(t[:-1]):        # 末位标点不拆 (后半空)
            if ch in stops and (best is None or abs(i - mid) < abs(best - mid)):
                best = i
        if best is not None and best >= 8 and len(t) - best - 1 >= 8:
            return t[:best + 1], t[best + 1:].lstrip()
    return None


def split_slot_line(doc: dict[str, Any], sid: str,
                    book_title: str = "") -> dict[str, Any]:
    """超限槽一键拆行 (1109 用户令: 槽长>15s 必须给解法)。

    超限根因 = 槽内最长口播行独大 (装包按行粒度, 单行>15s 切不开) →
    该行按标点对半拆成两行 → 重装包 → line_refs 未变的镜原句保留,
    拆出的新槽画面句由 LLM 补 (走 ③ 同一套提示词与铁律)。
    结构变 = 下游作废清戳 (与重跑 ③ 同语义)。
    """
    v3 = doc.setdefault("v3", {})
    shots = v3.get("shots") or []
    lines = v3.get("lines") or []
    arcs = v3.get("arcs") or []
    s = next((x for x in shots if str(x.get("sid")) == str(sid)), None)
    if s is None:
        raise ValueError(f"镜 {sid} 不存在")
    if s.get("brand_card"):
        raise ValueError("品牌镜走冻结骨架, 不拆行")
    if float(s.get("est_s") or 0) <= SLOT_MAX_S:
        raise ValueError(f"{sid} 槽长未超限, 无需拆行")
    line_of = {ln["no"]: ln for ln in lines}
    target = max((n for n in (s.get("line_refs") or []) if n in line_of),
                 key=lambda n: len(str(line_of[n].get("text") or "")))
    halves = _split_halves(str(line_of[target].get("text") or ""))
    if halves is None:
        raise ValueError(f"L{target} 无可拆标点 — 到 ① 页手动拆行")
    # 插行 (h1 保留 suggs/adopted — 卡挂前半; h2 继承其余字段) + 顺序重编号
    new_lines: list[dict[str, Any]] = []
    for ln in lines:
        if ln["no"] == target:
            h1 = dict(ln)
            h1["text"] = halves[0]
            h2 = {k: v for k, v in ln.items()
                  if k not in ("text", "suggs", "adopted")}
            h2["text"] = halves[1]
            new_lines += [h1, h2]
        else:
            new_lines.append(ln)
    for i, ln in enumerate(new_lines, 1):
        ln["no"] = i
    # 弧行号段平移: 祸源行所在弧 end+1 (行数+1), 其后弧 start/end 各+1
    for a in arcs:
        start, end = int(a.get("no_start") or 0), int(a.get("no_end") or 0)
        if start > target:
            start += 1
        if end >= target:
            end += 1
        a["no_start"], a["no_end"] = start, end
        a["line_count"] = max(0, end - start + 1)
    # 重装包 + 原句保留 (line_refs 键锚定 — 未受拆行影响的镜零扰动)
    old_by_refs = {tuple(x.get("line_refs") or []): x for x in shots}
    shots2, warns = pack_shots(arcs, new_lines)
    for s2 in shots2:
        old = old_by_refs.get(tuple(s2.get("line_refs") or []))
        if old is not None:
            for k in ("keyframe_zh", "in_image_text", "text_layer"):
                if old.get(k) is not None:
                    s2[k] = old[k]
    v3["lines"] = new_lines
    v3["shots"] = shots2
    # 拆出的新槽 (画面句空) 走 ③ 同尺 LLM 补
    n_fill = _llm_fill(shots2, arcs, {ln["no"]: ln for ln in new_lines},
                       None, _look_catalog(book_title) or None)
    v3["cards"] = build_cards(v3, shots2)
    issues = text_layer_issues(shots2, book_title)
    for k2, lst in cast_gate_issues(shots2, arcs, book_title).items():
        issues.setdefault(k2, []).extend(lst)
    v3["shot_issues"] = issues
    v3["shot_warnings"] = warns
    v3.setdefault("gates", {})["shots"] = not has_blocking_issues(issues)
    # 结构变 = 下游作废 (重跑 ③ 同语义)
    if v3.get("frozen_at") or v3.get("direct"):
        v3.pop("frozen_at", None)
        v3.pop("direct", None)
        v3.pop("direct_issues", None)
        v3.setdefault("gates", {})["direct"] = False
        logger.info("[v3:shots] 拆行 %s (L%d) — 结构变, ④/英化/冻结已清", sid, target)
    return {"split": {"line": target, "halves": halves},
            "filled": n_fill, "shots": len(shots2),
            "warnings": warns, "issues": issues,
            "gate": not has_blocking_issues(issues)}


# ── 景别推断 (④导演页 UI 预填; 生成仍由 LLM 做主) ──────────────────────

_SIZE_WORDS = (("大全景", "大场景"), ("大场景", "大场景"), ("远景", "大场景"),
               ("全景", "全景"), ("中景", "中景"), ("近景", "近景"), ("特写", "特写"))


def suggest_size(text: str) -> str:
    """从画面句推断景别 (只是预填, 与 final_gate 的大场景词表同源)。"""
    t = str(text or "")
    for w, size in _SIZE_WORDS:
        if w in t:
            return size
    return "中景"


# ── 一键空白化 (1105 用户令: v3 停用图内字通道) ────────────────────────

_CARRIER_WORD = re.compile(r"含文字载体「(.+?)」")
# 1108 词前白名单制废除 (ep6 实锤 9/9 全灭: 巨型屏幕/背景屏幕墙/米色便签 全不是量词语境,
# 白名单只接得住「几张便签」) — 改词后黑名单: 载体词后 6 字内有内容动词 = 此处在描述
# 载体内容, 机械插「空白」自相矛盾 (空白屏幕布满纹路) → 手改; 其余语境一律直插
_NO_INSERT_POST = re.compile(r"布满|写满|写着|显示|列出|打出|滚动|播放")


def blankify_carriers(shots: list[dict[str, Any]],
                      issues: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """把问题镜的载体就地补「空白」(巨型屏幕→巨型空白屏幕)。

    只动首个命中处 (质检同口径); 词后是内容动词 (显示/布满) 的地方机械插空白
    会自相矛盾 → 手改。返回 (改过的 sid, 待人工清单)。
    终态词属语义改写, 一律不自动动刀。
    """
    by_sid = {s["sid"]: s for s in shots}
    fixed: list[str] = []
    manual: list[str] = []
    for sid, lst in (issues or {}).items():
        if sid == ARC_LEVEL_KEY:
            continue
        s = by_sid.get(sid)
        if not s:
            continue
        zh = str(s.get("keyframe_zh") or "")
        for iss in lst:
            m = _CARRIER_WORD.search(iss)
            if not m:
                if "终态" in iss:
                    manual.append(f"{sid}: {iss[:80]}")
                continue
            word = m.group(1)
            h0 = zh.find(word)
            if h0 < 0:
                continue                           # 画面句已改 (陈旧 issue) → 重算自清
            pre = zh[max(0, h0 - 6):h0]            # 与质检同窗 (词前 6 字)
            post = zh[h0 + len(word):h0 + len(word) + 6]
            if "空白" in pre or "留白" in pre:
                continue                           # 早已写过空白 → 无需动, 也不算改
            if _NO_INSERT_POST.search(post):
                manual.append(f"{sid}: 载体「{word}」词后是内容描述 ({post[:6]}) — "
                              "需手改成「空白{word}泛着微光」或删掉该物件")
                continue
            zh = zh[:h0] + "空白" + zh[h0:]
            s["keyframe_zh"] = zh
            if sid not in fixed:
                fixed.append(sid)
    return fixed, manual


# ── 去引号/去申报注释 (1105 用户令: v3 画面里不许有任何字) ──────────────

_RE_DECL_NOTE = re.compile(r"[（(]\s*申报[^）)]{0,40}[）)]")        # 模型自造的元说明
_RE_QT_OF = re.compile(r"[「『\"“][^」』\"”]{1,20}[」』\"”]\s*的")   # 「该拨未拨」的 → 整段删
_RE_QT = re.compile(r"[「『\"“][^」』\"”]{1,20}[」』\"”]")


def strip_quoted_labels(shots: list[dict[str, Any]],
                        issues: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """把画面句里的引号标签就地删掉 (删后不留病句片).

    LLM 常把桥段标签写成「该拨未拨」, 或自造 (申报文字载体: …) 元说明。
    v3 图内字通道已停用, 画面里不许有任何字 → 确定性删除; 删不干净的交人工。
    """
    by_sid = {s["sid"]: s for s in shots}
    fixed: list[str] = []
    manual: list[str] = []
    for sid, lst in (issues or {}).items():
        if sid == ARC_LEVEL_KEY:
            continue
        s = by_sid.get(sid)
        if not s or not any("引号" in x for x in lst):
            continue
        zh = str(s.get("keyframe_zh") or "")
        new = _RE_DECL_NOTE.sub("", zh)
        new = _RE_QT_OF.sub("", new)
        new = _RE_QT.sub("", new)
        new = re.sub(r"的的|，，|。。|；；", lambda m: m.group(0)[0], new)
        if new != zh and not _RE_QT.search(_RE_DECL_NOTE.sub("", new)):
            s["keyframe_zh"] = new.strip()
            fixed.append(sid)
        elif new != zh:
            manual.append(f"{sid}: 引号文字需手改 — 删掉引号或改写成普通描述")
    return fixed, manual


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
    # 卡目录 (批1 选角): 弧 cast → prompt 列 + 卡名闸
    card_catalog: dict[str, str] = {}
    if bible:
        # 1108 批B: [:40]→[:120] — 老谭 look 89 字, "下巴光洁无胡须"在尾部被
        # 截断丢失 = writer 看不到面部锚 (s6 K2 胡茬上游因之一)
        card_catalog = {str(c.get("name") or "").strip(): str(c.get("look") or "").strip()[:120]
                        for c in (bible.get("characters") or [])
                        if str(c.get("name") or "").strip()}
    filled = n_brand
    try:
        filled += _llm_fill(shots, arcs, line_of, job, card_catalog or None)
        logger.info("[v3:shots] 画面句 %d/%d 镜 ✓ (批 %d)", filled, len(shots), BATCH)
    except Exception as exc:  # noqa: BLE001 — LLM 失败留空待人填
        logger.warning("[v3:shots] 画面句 LLM 失败 (留空待手填): %s", exc)

    cards = build_cards(v3, shots)
    issues = text_layer_issues(shots, book_title=str(job.get("book_title") or ""))
    for sid, lst in cast_gate_issues(
            shots, arcs, str(job.get("book_title") or "")).items():
        issues.setdefault(sid, []).extend(lst)
    n_issue = sum(len(v) for v in issues.values())

    v3["shots"] = shots
    v3["cards"] = cards
    v3["shot_issues"] = issues
    v3["shot_warnings"] = warns
    # 1106 槽位令: 重跑 ③ = 结构变 → 下游全部作废清戳 (④参数/英文化/冻结/K2 状态)。
    # 旧镜的图文件留在盘上 (img/sN.png 会被新槽覆写), 状态归零防脏数据滞留。
    if v3.get("frozen_at") or v3.get("direct"):
        v3.pop("frozen_at", None)
        v3.pop("direct", None)
        v3.pop("direct_issues", None)
        v3.setdefault("gates", {})["direct"] = False
        logger.info("[v3:shots] 重跑 ③ — ④参数/英文化/冻结戳已清 (结构变, 下游作废)")
    v3.setdefault("gates", {})["shots"] = False     # 人闸: 前端放行才开
    shots_mod.save(doc)

    job["result"] = {"shots": len(shots), "filled": filled, "brand": n_brand, "cards": len(cards),
                     "issues": n_issue, "warnings": len(warns)}
    logger.info("[v3:shots] %s ep%d: %d 镜 (画面句 %d, 卡 %d, 质检问题 %d) → shots.json v3 ✓",
                job["book_title"], job["ep"], len(shots), filled, len(cards), n_issue)
