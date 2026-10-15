# -*- coding: utf-8 -*-
"""系列层结构导演  — 书的蒸馏材料 → 灵魂三问 + 六集总纲.

管线位置: 书级末站 (facing/迁移视角之后), Step 1 集级导演之前。
Gate 0.5: 蒸馏材料组装时过 compliance_gate (敏感句换安全版/剔除),
导演只见安全料。产物: input_json.灵魂三问 + episodes.roadmap_json 六集重排
(用户在讲书页可继续手改)。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import BookProject, Episode
from app.services.book_service.creation_steps import _ep_target_duration

logger = logging.getLogger(__name__)

__all__ = ["run_series_outline"]

def _director_prompt() -> str:
    """调用时现读 (0912): 提示词改完即生效, 不用重启后端."""
    return (Path(__file__).resolve().parents[3]
            / "config" / "laotan_series_director.txt").read_text(encoding="utf-8")


def _material(book: BookProject) -> tuple[str, list[str]]:
    """蒸馏材料组装 (Gate 0.5 过滤) → (user_prompt 材料, 合规命中说明).

    v2 : sections/ 按需取料优先 (一页核/元问题/方法/故事库/概念),
    input_json 字段 fallback。
    """
    ij = book.input_json if isinstance(book.input_json, dict) else {}
    hits: list[str] = []

    def _val(key: str):
        v = ij.get(key)
        return (v or {}).get("value") if isinstance(v, dict) else v

    from .compliance_gate import sanitize_material
    from .distiller import load_section
    sp = book.source_path or ""

    def _san(text: str) -> str:
        nonlocal hits
        safe, hh = sanitize_material(text, book.book_title, source_path=sp)
        hits += hh
        return safe

    def _sec(name: str, cap: int = 0) -> str:
        t = load_section(book.book_title, name)
        return _san(t[:cap]) if (t and cap) else _san(t)

    parts = [f"书名：《{book.book_title}》", f"作者：{book.author or '未知'}"]
    one_page = _sec("一页核", 1500)
    meta_q = _sec("元问题", 200)
    if meta_q or one_page:
        parts.append("【元问题与一页核 (全书最浓缩料)】"
                     + (f"{meta_q}\n{one_page}" if meta_q and one_page else (meta_q or one_page)))
    elif _val("全书核心主张"):
        parts.append(f"核心主张：{_san(str(_val('全书核心主张')))}")
    for sec_name, cap, fb_key in (("概念与机制", 4000, "关键概念清单"),
                                  ("故事库", 4000, "核心案例"),
                                  ("方法与清单", 3000, None)):
        t = _sec(sec_name, cap)
        if t:
            parts.append(t)
        elif fb_key and _val(fb_key):
            fb = _val(fb_key)
            fb_s = " / ".join(str(x) for x in fb) if isinstance(fb, list) else str(fb)
            parts.append(f"{fb_key}：{_san(fb_s)}")
    t_path = _sec("成功路径", 2000)
    if t_path:
        parts.append(t_path)
    # 0914: 有受众地图 (新链路) 时不再喂旧迁移视角清单 — 双清单=给导演双重人群信号;
    # 旧书无地图时保留 (legacy 兜底)
    _am = _val("受众地图") or ij.get("受众地图") or {}
    _has_map = isinstance(_am, dict) and (_am.get("L1") or _am.get("上下游") or _am.get("上下层"))
    if not _has_map:
        views = (_val("本书迁移视角") or ij.get("本书迁移视角") or [])
        if isinstance(views, dict):
            views = views.get("value") or []
        parts.append("迁移视角：" + " / ".join(str(v) for v in views))
    # 0914 受众地图 (两层受众): L1 主线 + 两轴闲话池 — 闲话层调度从池中选
    if _has_map:
        _l1 = (_am.get("L1") or {})
        _pool_txt = lambda arr: " / ".join(
            f"{p.get('group', '')}(桥:{p.get('bridge', '')})" for p in (arr or [])) or "(书撑不起此轴)"
        parts.append(
            f"【受众地图 (书定, L1=全系列主线读者)】\n"
            f"L1 主线读者: {_l1.get('reader', '')}\n"
            f"上下游池: {_pool_txt(_am.get('上下游'))}\n"
            f"上下层池: {_pool_txt(_am.get('上下层'))}")
    return "\n".join(parts), hits


def _norm_steps(raw: Any) -> list[str]:
    """拆解三步归一 (0913b): list | 分隔字符串 → 3 条短语; 不成 3 条返回 [] ."""
    if isinstance(raw, str):
        raw = re.split(r"[;；|\n]", raw)
    if not isinstance(raw, list):
        return []
    steps = [str(s).strip(" 　①②③④⑤:.：，,。") for s in raw if str(s).strip()]
    return [s for s in steps if s]


_CHAT_AXES = ("上下游", "上下层")


def _norm_chatter(raw: Any) -> list[dict]:
    """闲话层归一 (0914): [{轴,group,bridge}] | 简写字符串 → ≤2 条规范 dict.

    简写形如 "上下层·带团队的部门负责人；上下游·靠客户预算的乙方" (bridge 缺省空,
    生成层有池时按池补/按轴公式现造)。
    """
    items: list[Any]
    if isinstance(raw, str):
        items = re.split(r"[;；\n]", raw)
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[dict] = []
    for it in items[:2]:
        if isinstance(it, dict):
            axis = str(it.get("轴") or "").strip()
            group = str(it.get("group") or it.get("组") or "").strip()
            bridge = str(it.get("bridge") or it.get("桥") or "").strip()
        else:
            s = str(it).strip()
            parts = [p for p in re.split(r"[·:：,，/]", s) if p.strip()]
            axis = parts[0].strip() if parts else ""
            group = parts[1].strip() if len(parts) > 1 else ""
            bridge = parts[2].strip() if len(parts) > 2 else ""
        if axis not in _CHAT_AXES:
            # 轴名失格 → 试着从原文里认轴 (导演常见漂移: 只写人群/写"上下游轴")
            hit = next((a for a in _CHAT_AXES if a in str(it)), None)
            if not hit:
                continue
            axis = hit
            if isinstance(it, dict):
                group = group or str(it.get("group") or it.get("组") or "").strip()
            else:
                group = group or str(it).replace(hit, "", 1).strip(" 　轴·:：,，/的")
        if group:
            out.append({"轴": axis, "group": group[:30], "bridge": bridge[:60]})
    return out


def _validate(data: dict) -> list[str]:
    """确定性校验 (设计令: 不靠 LLM 自觉)。返回问题清单, 空=过。

    0914 结构参数化 (用户令: 集数由书定, 系统只守不变量):
    总集数 = 工具数 K+2 ∈ [5,10]; 第1集=大钩子集, 末集=行动册集,
    中间=工具集 (一集一秘籍, 拆解三步/闲话层必填, 头尾豁免)。
    0922 带放宽上沿 9→10 (用户令: 单集 300→240s, 集数多切 1-2 集)。
    """
    issues: list[str] = []
    eps = data.get("episodes") or []
    soul = data.get("soul") or {}
    if not 5 <= len(eps) <= 10:
        issues.append(f"集数 {len(eps)} 不在结构带 [5,10] (应为 工具数K+2: 大钩子1+工具K+行动册1, K∈[3,8])")
    if not all(soul.get(k) for k in ("core", "who", "gain")):
        issues.append("灵魂三问不完整")
    if eps:
        e1 = eps[0]
        t1 = str(e1.get("核心任务") or "") + str(e1.get("主题") or "")
        # 0914: ep1=大钩子集 (收益承诺+书三件套+工具导览, 不教工具)
        if not any(k in t1 for k in ("自测", "钩", "说的就是我", "撞墙", "生死", "承诺", "导览", "大钩子")):
            issues.append("ep1 核心任务未见'钩住人/承诺/导览'职能 (大钩子集)")
        # 首尾承上启下不空白 (0909 DS: 表格完整, 空位写固定句)
        if not str(eps[0].get("承上") or "").strip():
            issues.append("ep1 承上空白 (写'无(系列首集)—…'式固定句)")
        if not str(eps[-1].get("启下") or "").strip():
            issues.append("末集 启下空白 (写'无(系列收束)—…'式固定句)")
        # 末集=行动册集 (纯干货装订)
        tlast = str(eps[-1].get("核心任务") or "") + str(eps[-1].get("主题") or "") + str(eps[-1].get("本集秘籍") or "")
        if not any(k in tlast for k in ("行动册", "合订", "装订", "干货", "工具册", "收官", "交棒")):
            issues.append("末集未见'行动册/合订/装订'职能 (行动册集=纯干货收束)")
        # 概念正交: 六集概念两两交集
        csets = [set(str(c) for c in (e.get("概念") or [])) for e in eps]
        for i in range(len(csets)):
            for j in range(i + 1, len(csets)):
                inter = csets[i] & csets[j]
                if inter:
                    issues.append(f"ep{i+1}/ep{j+1} 概念重叠: {inter}")
        # 秘籍齐备 (0908 用户令): 每集一个可命名交付物, 禁空洞词
        _hollow = ("认知", "思维", "提升", "了解", "理解", "学会")
        for i, e in enumerate(eps):
            secret = str(e.get("本集秘籍") or "")
            if len(secret) < 12:
                issues.append(f"ep{i+1} 秘籍缺失或过短")
            elif any(h in secret[:6] for h in _hollow) and len(secret) < 30:
                issues.append(f"ep{i+1} 秘籍疑似空洞: {secret[:20]}")
        # 拆解三步 (0913b 治段落割裂): 工具集必填, 头 (大钩子集) 尾 (行动册集) 豁免
        _n = len(eps)
        for i, e in enumerate(eps):
            _epno = int(e.get("ep") or i + 1)
            if _epno == 1 or _epno == _n:
                continue
            steps = _norm_steps(e.get("拆解三步"))
            if len(steps) != 3 or any(len(s) < 4 for s in steps):
                issues.append(f"ep{_epno} 拆解三步缺失/不是3条短语 (一步=一拆解块, 步名+书证钉, 禁第一步总览完三步)")
        # 闲话层 (0914 内容×人群双面大纲): 大钩子集+工具集 1~2 组两轴人群;
        # 仅末集 (行动册纯干货) 豁免 — ep1 是系列受众最宽的一集, 池子要在这搭桥
        for i, e in enumerate(eps):
            _epno = int(e.get("ep") or i + 1)
            if _epno == _n:
                continue
            chatter = _norm_chatter(e.get("闲话层"))
            if not 1 <= len(chatter) <= 2:
                issues.append(f"ep{_epno} 闲话层缺失 (1~2组, 轴=上下游/上下层, 只从受众地图池选)")
            elif len({c['group'] for c in chatter}) != len(chatter):
                issues.append(f"ep{_epno} 闲话层组重复")
        secrets = [str(e.get("本集秘籍") or "")[:10] for e in eps]
        if len(set(secrets)) != len(secrets):
            issues.append("存在重复秘籍")
        # 视角非单一
        all_views = [str(v) for e in eps for v in (e.get("视角") or [])]
        if all_views and len(set(all_views)) < 2:
            issues.append("全系列视角单一 (一根筋)")
    return issues


def _derive_series_card(book_title: str, soul: dict) -> str:
    """书级固定打字卡 (用户令: 全系列每集 0-2s 同一句, 骨架冻结两槽随书).

    骨架: "如果你能坚持每晚睡前看一集，一个月后，{收益主体}，会超过90%的{对比人群}。"
    收益主体 = 这本书让观众最终变好的能力, 翻成零上下文大白话 (财商式);
    对比人群 = 和谁比/在哪里脱颖而出 (同行/同事/做内容的人…)。
    骨架不合格 → 返回 "" (episode_gen 走 ep1 自产兜底)。
    """
    sys_p = (
        "你是拆书系列的定场卡生成器。为一本书的系列生成一句固定打字卡 — 全系列每集"
        "开头 0-2 秒 (静音打字画面) 都用这一句, 重复即系列认别+行为训练。\n"
        "骨架冻结 (一字不改): '如果你能坚持每晚睡前看一集，一个月后，{A}，会超过90%的{B}。'\n"
        "A = 这本书让观众最终变好的那个能力, 翻成**零上下文大白话** (≤12字, '财商'式 — "
        "禁系列内部词汇: 工具/判定/墙/集数/行动册/秘籍, 冷观众必须秒懂);\n"
        "B = 和谁比/在哪里脱颖而出 (2~6字, 随书定: 同行/同事/做内容的人/带团队的人…)。\n"
        '输出严格 JSON: {"card": "完整一句"}'
    )
    user = (f"书名：《{book_title}》\n书的核心：{soul.get('core', '')}\n"
            f"听的收益：{str(soul.get('gain', ''))[:200]}")
    from .creation_common import _llm, _parse_json
    try:
        data = _parse_json(_llm().chat(sys_p, user, model="flash", temperature=0.3))
        card = str((data or {}).get("card") or "").strip()
    except Exception:
        return ""
    if not ("每晚睡前" in card and "超过90%" in card) or not 15 <= len(card) <= 60:
        return ""
    return card


def run_series_outline(db: Session, book: BookProject, *, replace: bool = False) -> dict:
    """跑系列总编剧 → 落库 (input_json.灵魂三问 + 六集 roadmap_json)。

    replace=False 且总纲已有灵魂三问 → 直接返回现稿 (幂等); True 强制重排。
    """
    ij = book.input_json if isinstance(book.input_json, dict) else {}
    if not replace and (ij.get("灵魂三问") or {}).get("core"):
        return {"status": "exists", "soul": ij.get("灵魂三问")}
    material, gate_hits = _material(book)
    # 0912 樊登前置架构: 总编剧同时吃樊登全书真稿 (编段落号) — 大纲锚定实有叙事,
    # 每集输出樊登切片 {start_p, end_p} (episode_gen 按此取拐棍); 无稿走旧路 (旧书兼容)
    _ff = ""
    try:
        from .fandeng_full import load_fandeng_full, numbered_paragraphs
        _ff = load_fandeng_full(book.book_title)
        if _ff:
            _np = numbered_paragraphs(_ff)
            _ff_block = "\n\n".join(f"[P{i}] {p}" for i, p in _np)
            material += (f"\n\n【樊登全书讲述稿 (P1-P{len(_np)} 真稿, 六集切片从它上面切, "
                         f"秘籍/概念必须在切片内有实料)】\n{_ff_block}")
            logger.info("[series-outline] 喂樊登全书稿: %d段 %d字", len(_np), len(_ff))
    except Exception as exc:
        logger.warning("[series-outline] 樊登稿加载跳过: %s", exc)
    from .creation_common import _llm, _parse_json

    def _check(d: dict) -> list[str]:
        """结构校验 + 樊登切片校验 (draft1/重修稿共用)."""
        iss = _validate(d)
        if _ff and d.get("episodes"):
            _n_paras = len(numbered_paragraphs(_ff))
            _slices = []
            for row in d["episodes"]:
                sl = row.get("樊登切片") or {}
                try:
                    s, e = int(sl.get("start_p")), int(sl.get("end_p"))
                    assert 1 <= s <= e <= _n_paras
                    _slices.append((s, e))
                except Exception:
                    iss.append(f"ep{row.get('ep')} 樊登切片缺失或越界 (稿共 P1-P{_n_paras})")
            if len(_slices) == len(d["episodes"]):
                flat = [p for s, e in _slices for p in range(s, e + 1)]
                if len(flat) != len(set(flat)):
                    iss.append("樊登切片段落重叠 (六集各拿各的, 不许同段两集)")
                miss = set(range(1, _n_paras + 1)) - set(flat)
                if miss:
                    logger.warning("[series-outline] 切片未覆盖段落: %s (不阻断)", sorted(miss)[:10])
        return iss

    raw = _llm().chat(_director_prompt(), material, model="pro", temperature=0.4)
    data = _parse_json(raw)
    if not isinstance(data, dict) or not data.get("episodes"):
        return {"status": "failed", "error": "总编剧输出不可解析"}
    issues = _check(data)
    if issues:
        # 0922 校验不过带问题重修一轮 (拆分约束加严后单发命中率降): 问题清单回喂复检
        logger.warning("[series-outline] 校验问题 (尝试重修): %s", issues)
        try:
            fix = _llm().chat(
                _director_prompt(),
                material + "\n\n【上一稿校验未过, 逐条修复后全量重出 (保持同结构同集数)】\n- "
                + "\n- ".join(issues)
                + "\n直接输出修正后的完整 JSON, 不要解释。",
                model="pro", temperature=0.4)
            data2 = _parse_json(fix)
            if isinstance(data2, dict) and data2.get("episodes"):
                issues2 = _check(data2)
                if not issues2:
                    data, issues = data2, []
                    logger.info("[series-outline] 重修稿过校验 ✓")
                else:
                    logger.warning("[series-outline] 重修稿仍不过: %s", issues2)
        except Exception as exc:
            logger.warning("[series-outline] 重修轮异常 (用原稿问题返回): %s", exc)
    if issues:
        logger.warning("[series-outline] 校验问题: %s", issues)
        return {"status": "rejected", "issues": issues, "draft": data}
    soul = data.get("soul") or {}
    # 落库: 灵魂三问 + 书级固定打字卡 + 六集 roadmap (全量重写, 用户可在讲书页继续手调)
    ij = dict(ij)
    ij["灵魂三问"] = soul
    _card = _derive_series_card(book.book_title, soul)
    if _card:
        ij["系列打字卡"] = {"card": _card, "tier": "L0"}
        logger.info("[series-outline] 书级固定打字卡: %s", _card)
    book.input_json = ij
    for row in data["episodes"]:
        ep_i = int(row.get("ep") or 0)
        ep = db.query(Episode).filter(Episode.book_id == book.id,
                                       Episode.ep_index == ep_i).first()
        if not ep:
            ep = Episode(book_id=book.id, ep_index=ep_i, status="pending",
                         target_duration_sec=_ep_target_duration(book))
            db.add(ep)
        ep.target_duration_sec = _ep_target_duration(book)  # 0922: 旧集行同步刷新 (老谭 300→240)
        ep.roadmap_json = {
            "主题": row.get("主题") or "",
            "黄金三秒钩子": row.get("黄金三秒钩子") or "",
            "赌注层级": row.get("赌注层级") or "",
            "数据锚点": row.get("数据锚点") or "",
            "贯穿人物": row.get("贯穿人物") or "",
            "对应书中内容": row.get("对应书中内容") or "",
            "核心任务": row.get("核心任务") or "", "卖点": row.get("卖点") or "",
            "承上": row.get("承上") or "", "启下": row.get("启下") or "",
            "概念": list(row.get("概念") or []), "视角": list(row.get("视角") or []),
            "本集秘籍": row.get("本集秘籍") or "",
            "三件套预留": row.get("三件套预留") or "",
            # 0910 构件调度 + 0913b 拆解三步: 导演层分配, 生成器只执行禁自选
            # (旧白名单漏掉构件 → 导演输出落库即丢, episode_gen 永远读空, 实锤修复)
            "构件": row.get("构件") or "",
            "拆解三步": _norm_steps(row.get("拆解三步")),
            "闲话层": _norm_chatter(row.get("闲话层")),
            # 0912 樊登前置: 本集拐棍=樊登全书稿切片 (episode_gen 按段落号确定性取)
            "樊登切片": dict(row.get("樊登切片") or {}),
        }
        ep.title = row.get("主题") or ep.title  # title 同步新总纲 (0908: 导览/列表都吃它)
    db.commit()
    logger.info("[series-outline] %s 六集总纲落库 (Gate0.5 命中 %d 处)", book.book_title, len(gate_hits))
    return {"status": "ok", "soul": soul, "gate_hits": gate_hits,
            "episodes": data["episodes"]}
