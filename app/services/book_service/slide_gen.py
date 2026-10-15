# -*- coding: utf-8 -*-
"""拆书页规划器 v2  — 口播稿 → bs1 十三页型页单.

v1  废弃: 版式归 bs1 模板族 (深空蓝金 13 页型,
templates/hf_prep_v3/bs1_*_v1), 本模块只做内容规划 — LLM 分页选型 + 硬校验 +
确定性兜底, 输出 PagePlan 列表; template_id = bs1-{type}-v1 由渲染串联环节消化。
台词逐字切片零改写, 展示字段从台词提炼。

铁律 (用户令 0908): 老谭读书用 bs1 族, 静读书线 (hf-* 纸墨系) 不共用。
契约规格书: .tmp/bs1_preview/index.html 预览页规则块。
bookquote/bookinfo 尾卡走 ppt_pipeline 既有链路, 不进本规划器。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

__all__ = ["PagePlan", "plan_episode_pages"]

_CPS = 4.7            # 口播字/秒 (与 episode_gen 字数基线同源)
_COVER_MAX = 38       # 前15秒铁律: cover ≤8s ≈38字
_CONT_MAX = 33        # contents ≤7s ≈33字 (cover+contents 合计 ≤15s)
_QUOTE_MIN, _QUOTE_MAX = 2, 3   # 全屏金句页张数区间
_MIN_PAGES, _MAX_PAGES = 6, 48
_GAP_MIN = 20         # 页间台词缺口超过此字数 → 确定性补 passage 页
_SENT_END = "。！？!?；;"

_CN_NUM = "零一二三四五六七八九十"

# 十一规划页型 (十三页型中 bookquote/bookinfo 走尾卡, 不在此): 字段白名单
_FIELDS: dict[str, set[str]] = {
    "cover": {"title"},
    "contents": set,
    "chapter": {"no", "title", "subtitle"},
    "list": {"kicker", "title", "items", "quote"},
    "passage": {"kicker", "title", "body", "quote"},
    "compare": {"kicker", "title", "left", "right"},
    "case": {"kicker", "title", "story", "moral"},
    "quote": {"quote"},
    "data": {"kicker", "title", "stats", "note"},
    "chart": {"kicker", "title", "bars", "note"},
    "outro": {"quote", "question", "next"},
}
_BODY_TYPES = set(_FIELDS) - {"cover", "contents", "outro"}


@dataclass
class PagePlan:
    """规划单页: type 定 template_id, fields 即模板内容字段, 版式/品牌/图由产线注入."""
    type: str
    narration: str = ""           # 该页台词 = 原稿逐字切片 (标签行除外)
    fields: dict = field(default_factory=dict)
    img_query: str = ""           # Pexels 英文检索词 (≤3 词名词短语)
    img_cap: str = ""             # 图注中文 ≤15 字

    @property
    def template_id(self) -> str:
        return f"bs1-{self.type}-v1"

    @property
    def est_sec(self) -> float:
        """预估页停留秒数 (B 环节用真实 TTS 时长覆盖)."""
        return round(len(re.sub(r"\s", "", self.narration)) / _CPS + 0.5, 1)


# ── 稿件预处理 ────────────────────────────────────────────────
_LABEL_RE = re.compile(r"^【(\d+)-(\d+)秒｜([^】]+)】\s*$")
# 金句层行标记: 新契约=独立短行(0907后 laotan-book); 老稿=「金句来了：/金句：/收尾金句：」前缀
_QUOTE_LINE_RE = re.compile(r"^(?:收尾)?金句(?:来了)?[:：]\s*(.+)$")


def _clean_script(script_text: str) -> tuple[list[dict], list[tuple[str, str]], str]:
    """稿 → (段列表, 金句池[(展示句, 整行)], 纯口播 norm 串).

    段 = 空行分隔的行组, 记录段界前最近的六段标签名 (钩子/核心概念拆解（一）…);
    标签行与正文末尾 coverage JSON 块不是台词, 剥离。
    金句层: 独立成行的 ≤15 字短句 (新契约) 或带「金句：」前缀行 (老稿), 是口播
    的一部分照常念 — 整行保留在台词流, 池记 (去前缀本体, 整行) 供对账。
    """
    paras: list[dict] = []
    cur: list[str] = []
    label = ""
    in_json_tail = False
    prefix_quotes: list[tuple[str, str]] = []  # 前缀行本体 (剥前缀时顺手收集, 池不断链)
    for ln in (script_text or "").splitlines():
        s = ln.strip()
        if in_json_tail:
            continue
        if s.startswith("{") and "coverage" in s:
            in_json_tail = True
            continue
        m = _LABEL_RE.match(s)
        if m:
            if cur:
                paras.append({"label": label, "lines": cur})
                cur = []
            label = m.group(3)
            continue
        if not s:
            if cur:
                paras.append({"label": label, "lines": cur})
                cur = []
            continue
        # 老稿金句前缀行 (「金句来了：/收尾金句：」) 是机器标记不是台词 — 剥前缀
        # 只留金句本体进台词流 (0908 实锤: 前缀被 TTS 念出来 + 字幕带出)
        qm = _QUOTE_LINE_RE.match(s)
        if qm and qm.group(1).strip():
            body = qm.group(1).strip()
            prefix_quotes.append((body[:24], body))
            cur.append(body)
            continue
        cur.append(s)
    if cur:
        paras.append({"label": label, "lines": cur})
    quotes: list[tuple[str, str]] = list(prefix_quotes)
    quote_set = {d for d, _ in prefix_quotes}
    for p in paras:
        if len(p["lines"]) == 1 and 4 <= len(p["lines"][0]) <= 15:
            ln0 = p["lines"][0]
            if ln0 not in quote_set:
                quotes.append((ln0, ln0))  # 新契约: 独立短行段
    plain = _norm("".join(ln for p in paras for ln in p["lines"]))
    return paras, quotes, plain


def _norm(text: str) -> str:
    return re.sub(r"\s", "", text)


# ── LLM 规划 ──────────────────────────────────────────────────
_PLAN_SYS = """你是老谭读书口播稿的分页导演。把口播稿切成逐页画面, 为每页选页型并填展示字段, 只输出 JSON。

【稿件结构】稿中有【0-30秒｜钩子】这类标签行, 仅是段边界标记不是台词, 不得进入 narration。独立成行的 ≤15 字短句是金句层(是台词, 照常念, 保留在所在页 narration 内)。

【通用规则】
- 每页 12~28 秒台词(约 60~130 字); 页界只落在段落/语义边界, 禁止切在句子中间; 金句行与其铺垫内容同页。
- narration 逐字取自原稿连续片段(跳过标签行), 各页按序拼接≈全稿。禁止改写/增删台词。
- 展示字段(title/items 等)从该页台词提炼浓缩, 不编造; 展示文字≠台词。
- 第一页 type=cover, 第二页 type=contents, 最后一页 type=outro。
- **页型交替铁律 (抖音前段跳出)**: 相邻正文页禁止同型 — 开头 P3~P8 (前90秒) 是跳出高发区,
  页型必须交替变化 (passage/list/compare/case 穿插), 连续两张同型=违规; 全篇同型连排最多视为事故。

【页型规格】
1. cover: narration=开场钩子前约8秒(≤38字); title=集题≤20字; img_query 必填(封面底图检索词)。
2. contents: narration=钩子剩余约7秒(≤33字), 极简念法不逐条展开; 只填 type 和 narration(条目系统按章题自动生成)。
3. chapter(每集3~5张, 落在大段边界): no="01"起连续编号; title=章题≤12字; subtitle=本章钩子≤30字; img_query 必填。
4. list(清单页): kicker=英文类别眉标(如 STRATEGY/MODEL/CASE); items=2~4条同层级要点(全并列概念/全步骤/全对照, 禁概念与结论混排, 结论归 quote), 每条≤20字; quote=该页金句条≤15字(可空)。
5. passage(通用页): body=2~3行解读每行≤30字; quote 竖线金句≤15字(可空)。
6. compare(旧vs新对撞, 每集1~3张分散放置, 禁止相邻): left/right={"title":栏题≤8字,"points":[1~3条每条≤15字]}, left=旧模式, right=新模式(胜方)。
7. case(案例页): story=2~3行案例叙事每行≤30字; moral=落点金句≤15字; 建议配 img_query(案例人物/场景)。
8. quote(全屏金句页, 全稿最有力处共2~3张): quote=金句≤24字, 优先取稿中独立成行的短句; 不填出处(系统注入书名)。
9. data(2~3组数字): stats=[{"num":数字≤7字,"label":标签,"note":注解}]; note=数据来源注≤40字。
10. chart(数据≥4组且同类可比才用, 不同量纲禁同图): bars=[{"label":标签,"value":纯数字,"top":口播读法如"15美元"}]。
11. outro: narration=收尾段; next=下期钩子≤30字(系列完结则写完结语); img_query 必填(收束意象)。(主字位=品牌句/互动问模块已固化在模板, 勿产 question)

【img_query 具象化铁律】英文≤3个单词的名词短语, 摄像机拍得着的画面(讲攀登→mountain summit dusk; 深夜奋斗→office desk night; 护城河→castle moat stone)。禁抽象词(AI/technology/success/future), 禁修饰词堆砌。**cover/chapter/outro/list/passage/case 全部必配** (模板图位恒在, 空图=右侧留白残次品, 0910 P5/P12 实锤; 意象弱就选氛围图 night office/old street 之类)。

输出严格 JSON(无围栏无注释): {"pages":[{"type":"...","narration":"...", 加该页型字段}]}
"""


def _cut(txt: str, n: int) -> str:
    return (txt or "")[:n].strip()


def _ok_query(q: str) -> str:
    """英文 ≤3 词名词短语校验, 不合规置空 (B 侧回退纯色)."""
    q = (q or "").strip().lower()
    if not q or len(q.split()) > 3 or not re.fullmatch(r"[a-z0-9 ]{2,26}", q):
        return ""
    return q


def _str_lines(v, per: int, rows: int) -> list[str]:
    """字段值(数组或裸串) → 限长行数组."""
    arr = v if isinstance(v, list) else ([v] if v else [])
    return [_cut(str(x), per) for x in arr if str(x).strip()][:rows]


def _dict_rows(v, keys: tuple, per: int, rows: int) -> list[dict]:
    out: list[dict] = []
    for d in (v if isinstance(v, list) else [])[:rows]:
        if not isinstance(d, dict):
            continue
        item = {k: _cut(str(d.get(k) or ""), per) for k in keys}
        if any(item.values()):
            out.append(item)
    return out


def _demote(narration: str, title: str, body: list) -> PagePlan:
    """页型不可救 → 降级通用 passage 页 (台词不丢)."""
    body = [b for b in (_cut(str(x), 30) for x in body) if b][:3]
    return PagePlan("passage", narration, {"title": title, "body": body})


def _sanitize_page(p: dict) -> PagePlan | None:
    """LLM 单页 → 清洗/降级后的 PagePlan; type 非法返回 None."""
    ptype = str(p.get("type") or "").strip()
    if ptype not in _FIELDS:
        return None
    narration = str(p.get("narration") or "").strip()
    if ptype not in ("cover", "contents") and not narration:
        return None  # 正文页零台词 = 闪页事故 (0908 实锤 P4 0.5s), 直接丢
    f = dict(p)  # 字段平铺在页对象 (提示词约定), 多余键在取值时自然忽略
    quote = _cut(f.get("quote"), 24 if ptype == "quote" else 15)
    img_query = _ok_query(f.get("img_query"))
    img_cap = _cut(f.get("img_cap"), 15)
    kicker = _cut(f.get("kicker"), 12).upper()
    title = _cut(f.get("title"), 20 if ptype in ("cover", "contents") else 12)

    if ptype == "cover":
        return PagePlan("cover", narration, {"title": title}, img_query, img_cap)
    if ptype == "contents":
        return PagePlan("contents", narration)
    if ptype == "chapter":
        if not title:
            return _demote(narration, "", [])
        return PagePlan("chapter", narration,
                        {"title": title, "subtitle": _cut(f.get("subtitle"), 30)},
                        img_query, img_cap)
    if ptype == "list":
        items = _str_lines(f.get("items"), 20, 4)
        if len(items) < 2:  # 单条不成清单 → 通用页
            return _demote(narration, title, items + ([quote] if quote else []))
        return PagePlan("list", narration, {
            "kicker": kicker, "title": title, "items": items, "quote": quote},
            img_query, img_cap)
    if ptype == "passage":
        return PagePlan("passage", narration, {
            "kicker": kicker, "title": title,
            "body": _str_lines(f.get("body"), 30, 3), "quote": quote},
            img_query, img_cap)
    if ptype == "compare":
        def _side(key: str) -> tuple[str, list[str]]:
            v = f.get(key) if isinstance(f.get(key), dict) else {}
            return (_cut(v.get("title"), 8),
                    _str_lines(v.get("points"), 15, 3))
        lt, lp = _side("left")
        rt, rp = _side("right")
        if not (lt and rt and lp and rp):
            return _demote(narration, title, [lt or rt] + lp + rp)
        return PagePlan("compare", narration, {
            "kicker": kicker, "title": title,
            "left": (lt, lp), "right": (rt, rp)}, "", "")
    if ptype == "case":
        story = _str_lines(f.get("story"), 30, 3)
        if not story:
            return _demote(narration, title, [quote] if quote else [])
        return PagePlan("case", narration, {
            "kicker": kicker, "title": title, "story": story, "moral": quote},
            img_query, img_cap)
    if ptype == "quote":
        if not quote:
            return _demote(narration, "", [narration[:30]])
        return PagePlan("quote", narration, {"quote": quote})
    if ptype == "data":
        stats = _dict_rows(f.get("stats"), ("num", "label", "note"), 7, 3)
        if len(stats) < 2:
            return _demote(narration, title,
                           [f"{s.get('num', '')} {s.get('label', '')}".strip() for s in stats])
        return PagePlan("data", narration, {
            "kicker": kicker, "title": title, "stats": stats,
            "note": _cut(f.get("note"), 40)}, "", "")
    if ptype == "chart":
        bars: list[dict] = []
        for b in (f.get("bars") if isinstance(f.get("bars"), list) else [])[:6]:
            if not isinstance(b, dict):
                continue
            try:
                val = float(str(b.get("value", "")).replace("%", "").replace(",", ""))
            except (TypeError, ValueError):
                continue
            bars.append({"label": _cut(b.get("label"), 8), "value": val,
                         "top": _cut(b.get("top") or b.get("value"), 8)})
        if len(bars) < 4:  # 不足4柱 → 降 data 行式 (2~3组)
            stats = [{"num": b["top"], "label": b["label"], "note": ""}
                     for b in bars[:3]]
            if len(stats) < 2:
                return _demote(narration, title,
                               [f"{b['top']} {b['label']}" for b in bars])
            return PagePlan("data", narration, {
                "kicker": kicker, "title": title, "stats": stats,
                "note": _cut(f.get("note"), 40)}, "", "")
        return PagePlan("chart", narration, {
            "kicker": kicker, "title": title, "bars": bars,
            "note": _cut(f.get("note"), 40)}, "", "")
    if ptype == "outro":
        return PagePlan("outro", narration, {
            "quote": quote, "question": _cut(f.get("question"), 20),
            "next": _cut(f.get("next"), 30)}, img_query, img_cap)
    return None


# ── 硬校验 / 确定性修补 ────────────────────────────────────────
def _align_and_fill(plans: list[PagePlan], plain: str) -> list[PagePlan] | None:
    """序对齐: 每页 narration 必须是纯口播的有序子串; 缺口>_GAP_MIN 补 passage 页.

    未对齐页 (narration 不在稿内 = LLM 编造) 直接丢弃 — 展示字段同样不可信,
    缺口由后续页的 find 位置自然暴露并补页。首页对不上 → 整体不可信。
    """
    cursor = 0
    spans: list[tuple[int, int]] = []
    for p in plans:
        n = _norm(p.narration)
        idx = plain.find(n, cursor) if n else cursor
        spans.append((idx, idx + len(n)) if idx >= 0 else (-1, -1))
        if idx >= 0:
            cursor = idx + len(n)
    if not spans or spans[0][0] < 0:
        return None
    out: list[PagePlan] = []
    cursor = 0
    for p, (s, e) in zip(plans, spans):
        if s < 0:
            continue  # 编造台词页: 丢弃, 缺口逻辑接管
        if s - cursor >= _GAP_MIN:
            out.append(_gap_page(plain[cursor:s]))
        out.append(p)
        cursor = max(cursor, e)
    if len(plain) - cursor >= _GAP_MIN:  # 尾部残余台词
        tail = plain[cursor:]
        if out and out[-1].type == "outro":
            out[-1].narration += tail
        else:
            out.append(_gap_page(tail))
    return out


def _gap_page(text: str) -> PagePlan:
    """缺口台词 → 确定性 passage 页 (title=首句12字, body=前3句)."""
    first = re.split(r"[。！？!?；;]", text)[0] if text else ""
    segs: list[str] = []
    for piece in re.split(r"(?<=[。！？!?；;])", text):
        if piece.strip():
            segs.append(piece)
    return PagePlan("passage", text,
                    {"title": first[:12], "body": [s[:30] for s in segs[:3]]})


def _fix_first15(plans: list[PagePlan]) -> list[PagePlan] | None:
    """前15秒铁律: cover+contents 台词超 71 字 → 溢出部分按句边界挪给正文首页."""
    if len(plans) < 3 or plans[0].type != "cover" or plans[1].type != "contents":
        return None
    over = (len(_norm(plans[0].narration)) + len(_norm(plans[1].narration))
            - _COVER_MAX - _CONT_MAX)
    if over <= 0:
        return plans
    cont = plans[1].narration
    cut_n = max(1, len(_norm(cont)) - over)
    best, norm_seen = 0, 0
    for i, ch in enumerate(cont):
        if not ch.isspace:
            norm_seen += 1
        if ch in _SENT_END:
            if norm_seen >= cut_n * 0.5:
                best = i + 1
        if norm_seen >= cut_n and best:
            break
    if not best:  # 无句边界可用 → norm 计数硬切
        norm_seen = 0
        for i, ch in enumerate(cont):
            if not ch.isspace:
                norm_seen += 1
                if norm_seen >= cut_n:
                    best = i + 1
                    break
    if best <= 0:
        return None
    moved, plans[1].narration = cont[best:].strip(), cont[:best].strip()
    plans[2].narration = (moved + plans[2].narration).strip()
    return plans


def _rewrite_contents(plans: list[PagePlan]) -> list[PagePlan]:
    """目录↔chapter 一一对应: contents items 由 chapter 章题确定性重写, 章号重编."""
    chapters = [p for p in plans if p.type == "chapter"]
    for i, ch in enumerate(chapters, 1):
        ch.fields["no"] = f"{i:02d}"
    items = [_cut(ch.fields.get("title"), 20) for ch in chapters]
    if not items:  # 无 chapter 页 → 正文页题前4
        items = [_cut(p.fields.get("title"), 20)
                 for p in plans if p.type in _BODY_TYPES][:4]
    for p in plans:
        if p.type == "contents":
            p.fields["items"] = items
    return plans


def _reconcile_quotes(plans: list[PagePlan], quotes_pool: list[tuple[str, str]],
                      plain: str) -> list[PagePlan]:
    """金句对账: 全屏 quote 页保 [2,3] 张且 quote 必须是稿中原文; 不足从正文页剥出补."""
    def _qidx() -> list[int]:
        return [i for i, p in enumerate(plans) if p.type == "quote"]

    def _pool_pick(used: list[str]) -> tuple[str, str] | None:
        for disp, line in quotes_pool:
            if _norm(disp) not in used and _norm(disp) in plain:
                return disp, line
        return None

    used: list[str] = []
    qi = _qidx()
    for i in qi:
        q = _norm(plans[i].fields.get("quote") or "")
        if q and q in plain:
            used.append(q)
            continue
        pick = _pool_pick(used)
        if pick:
            plans[i].fields["quote"] = pick[0]
            used.append(_norm(pick[0]))
        else:  # 无池可换 → 降 passage
            plans[i] = _demote(plans[i].narration, "", [plans[i].fields.get("quote") or ""])
    for i in qi[_QUOTE_MAX:]:  # 超额降级
        plans[i] = _demote(plans[i].narration, "", [plans[i].fields.get("quote") or ""])
    while len(qi) < _QUOTE_MIN:
        pick = _pool_pick(used)
        if not pick:
            break
        disp, line = pick
        host = next((i for i, p in enumerate(plans)
                     if p.type in _BODY_TYPES and line in p.narration), -1)
        if host < 0:
            # 池已尽或宿主页找不到 — 把展示句从 used 标记后跳出防死循环
            used.append(_norm(disp))
            if len(used) >= len(quotes_pool):
                break
            continue
        plans[host].narration = plans[host].narration.replace(line, "").strip()
        plans.insert(host + 1, PagePlan("quote", line, {"quote": disp[:24]}))
        used.append(_norm(disp))
    return plans


# 页型节奏修复 (0908 用户令: 抖音前段同型连排=跳出)
_MONOTONY_TYPES = {"passage", "list", "case", "compare", "data", "chart"}
_ROTATE = ("compare", "list", "case", "chapter")  # 换型候选 (对撞/清单优先)


def _fix_monotony(plans: list[PagePlan], *, book_title: str) -> tuple[list[PagePlan], int]:
    """相邻正文页同型 → 后页 LLM 换型 (提示词失守的确定性兜底).

    返回 (页列表, 修复数); replan 失败保留原页 (尽力而为)。
    """
    body_idx = [i for i, p in enumerate(plans)
                if p.type not in ("cover", "contents", "outro")]
    changes = 0
    for a, b in zip(body_idx, body_idx[1:]):
        pa, pb = plans[a], plans[b]
        if pb.type != pa.type or pb.type not in _MONOTONY_TYPES:
            continue
        target = next((t for t in _ROTATE if t != pb.type), "list")
        np = replan_page(pb.narration, new_type=target,
                         old_fields=pb.fields, book_title=book_title)
        if np is None:
            continue
        np.img_query = np.img_query or pb.img_query  # 换型保配图意图
        np.img_cap = np.img_cap or pb.img_cap
        plans[b] = np
        changes += 1
        logger.info("[slide_gen] 节奏修复: 第%d页 %s→%s", b + 1, pb.type, np.type)
    return plans, changes


def _cn_num(n: int) -> str:
    if n <= 10:
        return _CN_NUM[n]
    if n < 20:
        return "十" + (_CN_NUM[n - 10] if n % 10 else "")
    return f"{n // 10}十" + (_CN_NUM[n % 10] if n % 10 else "")


def _finalize_fields(plans: list[PagePlan], *, ep_index: int, total_eps: int,
                     book_title: str) -> list[PagePlan]:
    """分隔符串拼装 + 确定性字段注入 (ep_tag/quote_src/章号)."""
    role = "导读" if ep_index <= 1 else ("落地" if ep_index >= max(total_eps, 1) else "拆解")
    ep_tag = f"第{_cn_num(ep_index)}集 · {role}"
    for p in plans:
        f = p.fields
        if isinstance(f.get("items"), list):  # 编号统一在此追加 (contents=章题, list=要点)
            f["items"] = "|||".join(f"{i:02d} {t}" for i, t in enumerate(f["items"], 1))
        for k in ("body", "story"):
            if isinstance(f.get(k), list):
                f[k] = "\n".join(f[k])
        if isinstance(f.get("stats"), list):
            f["stats"] = "|||".join("|".join((s.get("num", ""), s.get("label", ""),
                                              s.get("note", ""))) for s in f["stats"])
        if isinstance(f.get("bars"), list):
            f["bars"] = "|||".join("|".join((b.get("label", ""), str(b.get("value", "")),
                                             b.get("top", ""))) for b in f["bars"])
        for side in ("left", "right"):
            if isinstance(f.get(side), tuple):
                t, pts = f[side]
                f[side] = "|".join([t] + list(pts))
        if p.type == "cover":
            f["ep_tag"] = ep_tag
        if p.type == "quote":
            f["quote_src"] = f"《{book_title}》" if book_title else ""
    return plans


def _try_plan(script_text: str, plain: str, quotes_pool: list[str], *,
              ep_index: int, total_eps: int, ep_title: str, book_title: str,
              model: str) -> list[PagePlan] | None:
    """LLM 单轮规划 + 全链硬校验; 任一环失败返回 None (调用方重试/兜底)."""
    from .creation_common import _llm, _parse_json
    user = (f"【本集】第{ep_index}集(共{total_eps}集) · {ep_title} · 拆《{book_title}》\n\n"
            f"【口播稿】\n{script_text}")
    try:
        raw = _llm().chat(_PLAN_SYS, user, model=model, temperature=0.3)
        data = _parse_json(raw)
    except Exception as exc:
        logger.warning("[slide_gen] LLM 规划异常: %s", exc)
        return None
    pages_raw = (data or {}).get("pages") if isinstance(data, dict) else data
    if not isinstance(pages_raw, list):
        return None
    plans: list[PagePlan] = []
    for pr in pages_raw:
        if isinstance(pr, dict):
            sp = _sanitize_page(pr)
            if sp:
                plans.append(sp)
    # 结构契约: 首cover/次contents/末outro/页数区间
    if (len(plans) < _MIN_PAGES or len(plans) > _MAX_PAGES
            or plans[0].type != "cover" or plans[1].type != "contents"
            or plans[-1].type != "outro"):
        logger.warning("[slide_gen] 页结构不合契约 (%d 页), 弃", len(plans))
        return None
    plans = _align_and_fill(plans, plain)
    if plans is None:
        logger.warning("[slide_gen] 台词序对齐失败 (首页即不对)")
        return None
    plans = _fix_first15(plans)
    if plans is None:
        logger.warning("[slide_gen] 前15秒结构异常")
        return None
    got = sum(len(_norm(p.narration)) for p in plans)
    if not plain or got / len(plain) < 0.85:
        logger.warning("[slide_gen] 台词覆盖率 %.0f%%, 弃", 100 * got / max(len(plain), 1))
        return None
    # 质量硬校验 (0908 三版规划实证 flash 不稳: 单页 40s+ / 字段大面积空)
    body = [p for p in plans if p.type in _BODY_TYPES]
    if body and max(len(_norm(p.narration)) for p in body) > 160:  # ~34s 上限
        logger.warning("[slide_gen] 存在超长页 (>%d字), 切页太粗, 弃", 160)
        return None
    empty_titled = sum(1 for p in body
                       if not (p.fields.get("title") or p.fields.get("quote")
                               or p.fields.get("story") or p.fields.get("stats")
                               or p.fields.get("bars")))
    # passage 专项 (0908 实锤: 只填 quote、body 空的空壳 passage 滑过闸门)
    empty_titled += sum(1 for p in body
                        if p.type == "passage" and not p.fields.get("body"))
    if body and empty_titled / len(body) > 0.3:  # 页题/主字段空 >30% = 展示层崩
        logger.warning("[slide_gen] %d/%d 正文页主字段空, 弃", empty_titled, len(body))
        return None
    plans = _rewrite_contents(plans)
    plans = _reconcile_quotes(plans, quotes_pool, plain)
    # 页型节奏 (0908 用户令): 相邻正文同型连排 → 后页换型 (LLM 单页重排)
    plans, _ = _fix_monotony(plans, book_title=book_title)
    plans = _rewrite_contents(plans)  # 换型可能增删 chapter → 章号/目录再对齐
    return _finalize_fields(plans, ep_index=ep_index, total_eps=total_eps,
                            book_title=book_title)


# ── 确定性兜底 (无 LLM) ────────────────────────────────────────
def _fallback_pages(paras: list[dict], *, ep_index: int, total_eps: int,
                    book_title: str, quotes: list[tuple[str, str]] | None = None,
                    ep_title: str = "") -> list[PagePlan]:
    """段级确定性切分 (无 LLM): 标签段首→chapter, 金句段→quote, 正文~120字打包
    passage, 总结段→outro。行对齐切分保证台词覆盖率 100% 零重叠。"""
    lines: list[str] = []
    seg_of: list[int] = []
    for si, para in enumerate(paras):
        for ln in para["lines"]:
            lines.append(ln)
            seg_of.append(si)
    if not lines:
        return []

    def _line_pack(start: int, max_chars: int) -> int:
        """从 start 行起整行累积 ≤max_chars, 返回行尾 idx (至少收 1 行)."""
        acc = 0
        for j in range(start, len(lines)):
            if acc and acc + len(lines[j]) > max_chars:
                return j
            acc += len(lines[j])
            if acc >= max_chars:
                return j + 1
        return len(lines)

    cover_end = _line_pack(0, _COVER_MAX)          # cover ≤38字
    cont_end = _line_pack(cover_end, _CONT_MAX)    # contents ≤33字
    plans: list[PagePlan] = [
        PagePlan("cover", "\n".join(lines[:cover_end]).strip(), {"title": ep_title[:20], "ep_tag": f"第{ep_index}集 · 导读"}),
        PagePlan("contents", "\n".join(lines[cover_end:cont_end]).strip()),
    ]
    buf: list[str] = []
    chapter_no = 0
    last_label = paras[seg_of[cont_end - 1]]["label"] if cont_end else ""

    def _flush():
        if buf:
            first = re.split(r"[。！？!?；;]", buf[0])[0][:12]
            if len(buf) >= 2 and plans and plans[-1].type == "passage":
                # 节奏轮换 (0908): 连排 passage 后打包成 list (行=条目), 防同型单调
                plans.append(PagePlan("list", "\n".join(buf), {
                    "kicker": "POINTS", "title": first,
                    "items": [l[:20] for l in buf[:4]], "quote": ""}))
            else:
                plans.append(PagePlan("passage", "\n".join(buf),
                                      {"title": first, "body": [l[:30] for l in buf[:3]]}))
            buf.clear()

    def _finalize() -> list[PagePlan]:
        _flush()
        return _finalize_fields(_rewrite_contents(plans), ep_index=ep_index,
                                total_eps=total_eps, book_title=book_title)

    _quote_display_set = {d for d, _ in quotes}
    i = cont_end
    while i < len(lines):
        ln, si = lines[i], seg_of[i]
        label = paras[si]["label"]
        if label != last_label:  # 六段标签变化 = 章/段边界
            if "总结" in label or "预告" in label:
                _flush()
                tail = lines[i:]
                outro_q = ""
                if not outro_q and quotes:  # 0910: 无前缀金句回填池首句/集题
                    outro_q = (quotes[0][0] if quotes else "")[:20]
                for t in tail:  # 收尾金句 (老稿前缀行) 填 outro.quote
                    mq = _QUOTE_LINE_RE.match(t)
                    if mq and mq.group(1).strip():
                        outro_q = mq.group(1).strip()[:24]
                if sum(len(t) for t in tail) > 140:  # 末段过长: 前部打包, 尾~100字归 outro
                    k = len(tail)
                    acc = 0
                    for j in range(len(tail) - 1, -1, -1):
                        acc += len(tail[j])
                        if acc > 100:
                            k = j + 1
                            break
                    plans.append(PagePlan("passage", "\n".join(tail[:k]),
                                          {"title": re.split(r"[。！？!?；;]", tail[0])[0][:12],
                                           "body": [l[:30] for l in tail[:3]]}))
                    tail = tail[k:]
                plans.append(PagePlan("outro", "\n".join(tail),
                                      {"quote": outro_q, "question": "", "next": ""}))
                return _finalize()
            _flush()
            chapter_no += 1
            take = min(2, len(paras[si]["lines"]))
            head_lines = paras[si]["lines"][:take]
            plans.append(PagePlan("chapter", "\n".join(head_lines), {
                "no": f"{chapter_no:02d}",
                "title": re.split(r"[。！？!?；;]", head_lines[0])[0][:12] if head_lines else "",
                "subtitle": head_lines[0][:30] if head_lines else ""}))
            last_label = label
            i += take
            continue
        if ln in _quote_display_set:  # 金句池行 (剥前缀后按池匹配) → 独立升格
            _flush()
            plans.append(PagePlan("quote", ln, {"quote": ln[:24]}))
            i += 1
            continue
        if paras[si]["lines"] == [ln] and 4 <= len(ln) <= 15:  # 新契约独立短行段
            _flush()
            plans.append(PagePlan("quote", ln, {"quote": ln[:24]}))
            i += 1
            continue
        buf.append(ln)
        if sum(len(b) for b in buf) >= 120:
            _flush()
        i += 1
    _flush()
    plans.append(PagePlan("outro", "", {"quote": (quotes[0][0][:20] if quotes else ep_title[:20]),
                        "question": "", "next": (lines[-1][:24] if lines else "")}))
    return _finalize()


def plan_episode_pages(script_text: str, *, ep_index: int, total_eps: int = 6,
                       ep_title: str = "", book_title: str = "") -> list[PagePlan]:
    """对外主入口: 口播稿 → bs1 页单列表 (LLM 规划 + 硬校验 + 确定性兜底)."""
    paras, quotes_pool, plain = _clean_script(script_text)
    if len(plain) < 80:
        logger.warning("[slide_gen] 稿过短 (%d 字), 不规划", len(plain))
        return []
    for attempt in range(2):
        plans = _try_plan(script_text, plain, quotes_pool, ep_index=ep_index,
                          total_eps=total_eps, ep_title=ep_title,
                          book_title=book_title, model="flash")
        if plans:
            return plans
    logger.warning("[slide_gen] LLM 规划两次未过, 走确定性兜底")
    return _fallback_pages(paras, ep_index=ep_index, total_eps=total_eps,
                           book_title=book_title, quotes=quotes_pool, ep_title=ep_title)


# ── 单页重规划 (bs1_story 页单工坊: 换页型 / 重产字段; narration 铁律不动) ──
_RETYPEABLE = _BODY_TYPES  # chapter/list/passage/compare/case/quote/data/chart 互换

_REPLAN_SYS = """你是拆书页面的单页重排师。给定一页的台词和目标页型, 产出该页的展示字段, 只输出 JSON。
展示字段从台词提炼浓缩, 不编造; 台词本身不可改动。如用户给了调整指令, 优先遵守。

【页型规格】
- chapter: no(沿用原值) / title=章题≤12字 / subtitle=本章钩子≤30字
- list: kicker=英文类别眉标≤12字符 / title≤12字 / items=2~4条同层级要点每条≤20字(全并列/全步骤, 结论归quote) / quote=金句条≤15字(可空)
- passage: kicker / title≤12字 / body=2~3行解读每行≤30字 / quote≤15字(可空)
- compare: kicker / title≤12字 / left/right={"title":栏题≤8字,"points":[1~3条每条≤15字]}, left=旧,right=新(胜方)
- case: kicker / title≤12字 / story=2~3行案例每行≤30字 / moral=落点金句≤15字
- quote: quote=金句≤24字(优先台词中的金句短句)
- data: kicker / title≤12字 / stats=[{"num":≤7字,"label":标签,"note":注解}]×2~3 / note=来源注≤40字
- chart: kicker / title≤12字 / bars=[{"label":≤8字,"value":纯数字,"top":口播读法}]×4~6(同类可比) / note≤40字

【img_query 具象化铁律】英文≤3词名词短语, 摄像机拍得着的画面; 禁抽象词(AI/success/future); 无强意象留空。
输出严格 JSON: {"type":"...","fields":{...},"img_query":"...","img_cap":"中文图注≤15字或空"}
"""


def replan_page(narration: str, *, new_type: str = "", hint: str = "",
                old_fields: dict | None = None, book_title: str = "") -> PagePlan | None:
    """单页重规划: 换页型 (new_type) 或同页型重产字段 (可带用户指令 hint).

    narration 逐字保留 (台词铁律); 返回 None = LLM 失败 (调用方保留原页).
    """
    from .creation_common import _llm, _parse_json
    new_type = (new_type or "").strip()
    if new_type and new_type not in _RETYPEABLE:
        logger.warning("[slide_gen] 换页型非法: %s", new_type)
        return None
    user = (f"【本页台词】\n{narration}\n\n【目标页型】{new_type or '维持原页型'}\n"
            f"【原字段】{old_fields or {}}\n"
            + (f"【调整指令】{hint}\n" if hint else ""))
    try:
        raw = _llm().chat(_REPLAN_SYS, user, model="flash", temperature=0.4)
        data = _parse_json(raw)
    except Exception as exc:
        logger.warning("[slide_gen] 单页重规划失败: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    ptype = str(data.get("type") or new_type or "").strip()
    if ptype not in _RETYPEABLE:
        return None
    if new_type and ptype != new_type:
        ptype = new_type  # 目标页型优先
    # 字段平铺进页对象 (_sanitize_page 平铺读取, 与整集规划同构)
    flat: dict = dict(data.get("fields")) if isinstance(data.get("fields"), dict) else dict(data)
    flat.update({"type": ptype, "narration": narration,
                 "img_query": data.get("img_query"), "img_cap": data.get("img_cap")})
    page = _sanitize_page(flat)
    if page is None or page.type != ptype:
        return None
    # chapter 保号: sanitize 不产 no, 单页重排沿用原章号 (整集重排才重编)
    if page.type == "chapter" and isinstance(old_fields, dict) and old_fields.get("no"):
        page.fields.setdefault("no", str(old_fields["no"]))
    # 分隔符串拼装 + quote_src 注入 (与整集规划同一收口)
    page = _finalize_fields([page], ep_index=1, total_eps=6, book_title=book_title)[0]
    return page
