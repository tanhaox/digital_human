# -*- coding: utf-8 -*-
"""爆品改造流水线 — run_boost 主编排 + P7 流量评审.

现行流水线 (2026-08-25): P-L(geo 反问目录注入, 独立层) → P4(逐句表达精修,
1:1锁定结构) → P6拼音审计 → P7评审. P1/P2/P3 已于 2026-08-14 砍除 (实证零
增益纯破坏), P5 已拆离至 audio.py 生成时刻.
拆包自 boost_service.py (2026-09-01), 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.services.boost_service.deconstruct import _format_deconstruct_for_prompt
from app.services.boost_service.llm import _call, _extract_json
from app.services.boost_service.prompts import (
    P1_PROMPT,
    P2_PROMPT,
    P3_PROMPT,
    P4_PROMPT,
    P7_PROMPT,
    P_LOOP_PROMPT,
)
from app.services.boost_service.splice import (
    _find_end,
    _parse_opening,
    _strip_p3_head,
    clean_boosted_text,
)

logger = logging.getLogger(__name__)

__all__ = ["run_boost", "audit_flow_metrics"]

# P7 评审缓存 (2026-08-25): {script_id: (final_text_sha, audit)} — 同稿重跑复用。
_FLOW_AUDIT_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}


def run_boost(db: Session, script_id: str, *, title: str | None = None,
              emit=None) -> dict[str, Any]:
    """执行完整爆品改造 (2026-08-14, 新流水线: P2→P3→P4→P1→P5).

    P1 后移至精修之后, 避免被 P3/P4 覆盖.
    失败容错: 单个 Pass 失败跳过, 用已成功部分的拼接; 全部失败回退原稿 (不卡死配音).
    """
    from app.models import Script

    script = db.get(Script, script_id)
    if script is None:
        raise ValueError(f"Script {script_id} not found")
    original = script.script_text
    original_title = title or (script.article.title if script.article else None)
    persona_name = "老谭"
    if script.host:
        persona_name = (script.host.stamp_name if getattr(script.host, "stamp_name", None) else script.host.name) or "老谭"

    decon_ctx = _format_deconstruct_for_prompt(script.deconstruct_json)

    def _emit(evt: str, msg: str) -> None:
        if emit:
            try: emit(evt, msg)
            except Exception: pass

    p1_result: dict[str, Any] | None = None
    p2_text: str | None = None
    p3_text: str | None = None
    p4_text: str | None = None
    loop_block: str | None = None  # P-L 反问目录文本 (geo; 定稿后代码插入)

    # ⚠️ ARCHIVED: _run_p2/_run_p3/_run_p1 (下方三个函数) 已于 2026-08-14 弃用,
    # run_boost 现行流水线不调用 — 勿删勿优化 (详见 P1_PROMPT 处标注)。
    # ── P2: 预埋争议+关注 ──
    def _run_p2() -> None:
        nonlocal p2_text
        _emit("boost_p2_start", "预埋专家：埋争议 + 关注钩子")
        try:
            body = _find_end(original)
            p2_prompt = P2_PROMPT.replace('{persona}', persona_name) + "\n\n【输入口播稿（无开头钩子）】\n" + body + decon_ctx
            p2_text = _call(
                p2_prompt,
                max_tokens=3000,
            )
            _emit("boost_p2_done", "预埋专家：预埋完成")
        except Exception as exc:
            logger.warning("[boost] P2 failed: %s", exc)
            _emit("boost_p2_error", f"预埋专家失败：{str(exc)[:100]}")

    # ── P3: 呼吸点 ──
    def _run_p3() -> None:
        nonlocal p3_text
        _emit("boost_p3_start", "节奏专家：插入呼吸点")
        try:
            p3_prompt = P3_PROMPT.replace('{persona}', persona_name) + "\n\n【输入口播稿全文】\n" + (p2_text or original)
            raw_p3 = _call(
                p3_prompt,
                max_tokens=3500,
            )
            p3_text = _strip_p3_head(raw_p3)
            _emit("boost_p3_done", "节奏专家：呼吸点完成")
        except Exception as exc:
            logger.warning("[boost] P3 failed: %s", exc)
            _emit("boost_p3_error", f"节奏专家失败：{str(exc)[:100]}")

    # ── P4: 精修正文 ──
    def _run_p4() -> None:
        nonlocal p4_text
        _emit("boost_p4_start", "全篇精修：正文顺滑化")
        try:
            input_text = p3_text or p2_text or original
            p4_prompt = P4_PROMPT.replace('{persona}', persona_name) + "\n\n【输入已完成全文】\n" + input_text
            raw_p4 = _call(
                p4_prompt,
                # 8000 (2026-08-25): P4 输出=全文复写 ~3000字≈逼近 4000 上限, 截断/
                # 大输出空响应风险 (同 P5 前科); thinking 兜底路径 max(8000,·) 不受影响。
                max_tokens=8000,
            )
            p4_text = _strip_p3_head(raw_p4)
            if not p4_text or not p4_text.strip():
                p4_text = input_text
            # 字数下限兜底 (2026-08-15): P4 偶发压缩 13~16% (合并短句/删句),
            # 精修定位是 1:1 打磨, 输出 <88% 原文长度视为越权 → 回退原稿。
            # 88% 而非 95%: 容忍旧稿 || 剥离等合法格式差。
            if len(p4_text) < len(input_text) * 0.88:
                logger.warning(
                    "[boost] P4 shrunk %d→%d chars (<88%%), fallback to original",
                    len(input_text), len(p4_text),
                )
                _emit("boost_p4_error", f"精修输出过短({len(p4_text)}字<{int(len(input_text)*0.88)}字)，已回退原稿防压缩")
                p4_text = input_text
            _emit("boost_p4_done", "全篇精修完成")
            # 首句保序护栏 (2026-08-25): P4 偶发把身份段挪到第一句 → 首屏 opening 卡
            # 变成"大家好我是老谭"自我介绍, 标题感全失 (实测)。输出稿开头必须仍是
            # 原稿开头(钩子), 否则视为越权重组 → 回退原稿。
            import re as _re
            _orig_head = _re.sub(r"^\[[^\]]+\]\s*", "", original.strip())[:12]
            _p4_head = _re.sub(r"^\[[^\]]+\]\s*", "", (p4_text or "").strip())[:12]
            if _orig_head and _p4_head != _orig_head:
                logger.warning(
                    "[boost] P4 首句移位 (原稿开头 %r, 精修稿开头 %r), 回退原稿",
                    _orig_head, _p4_head,
                )
                _emit("boost_p4_error", "精修把开头钩子移位（首屏标题感会丢），已回退原稿")
                p4_text = input_text
        except Exception as exc:
            logger.warning("[boost] P4 failed: %s", exc)
            _emit("boost_p4_error", f"全篇精修失败：{str(exc)[:100]}")

    # ── P1: 电击开场（后置，用 P4 后的正文） ──
    def _run_p1() -> None:
        nonlocal p1_result
        _emit("boost_p1_start", "开场专家：重写前 30 秒 + 标题")
        try:
            input_text = p4_text or p3_text or p2_text or original
            p1_prompt = P1_PROMPT.replace('{persona}', persona_name) + "\n\n【原标题】\n" + (original_title or '') + "\n\n【口播稿全文】\n" + input_text + decon_ctx
            raw = _call(
                p1_prompt,
                json_mode=True,
                max_tokens=1500,
            )
            parsed = _parse_opening(raw)
            if not parsed:
                logger.warning("[boost] P1 parse failed: %s", raw[:100])
                _emit("boost_p1_error", "开场专家：输出格式错误，跳过")
                return
            p1_result = parsed
            _emit("boost_p1_done", "开场专家：前 30 秒重写完成")
        except Exception as exc:
            logger.warning("[boost] P1 failed: %s", exc)
            _emit("boost_p1_error", f"开场专家失败：{str(exc)[:100]}")

    # (P5 情绪标注已于 2026-08-25 拆离 run_boost — 移至 audio.py 生成音频时刻,
    #  与 TTS 同源文本现场标注。独立函数 annotate_emotions 保留供其调用。)

    # ── P-L: 反问目录 (2026-08-25, geo 专属独立层) ──
    # 两段式: LLM 只产 3~5 行反问存 _loop_block, 插入在 final_text 定稿后由代码执行
    # (P4 精修碰不到反问层, 免疫"精修时被当冗余删除")。tech 线跳过 (对照组)。
    def _run_loop() -> None:
        nonlocal loop_block
        _emit("boost_loop_start", "反问目录：通读全文，设计连环反问")
        try:
            loop_prompt = P_LOOP_PROMPT.replace('{persona}', persona_name) + "\n\n【口播稿全文】\n" + original
            raw_loop = _call(loop_prompt, max_tokens=600)
            import re as _re2
            qlines = []
            for ln in (raw_loop or "").splitlines():
                ln = _re2.sub(r"^[\s\d\.、·\-\*\"'“”]+", "", ln.strip())
                ln = _re2.sub(r"[\"'“”\s]+$", "", ln)
                if ln.endswith("？") or ln.endswith("?"):
                    qlines.append(ln)
            if not (3 <= len(qlines) <= 6):
                logger.warning("[boost] P-L bad question count %d: %s", len(qlines), (raw_loop or "")[:80])
                _emit("boost_loop_error", f"反问目录产出异常（{len(qlines)} 问, 需 3~6）, 跳过注入")
                loop_block = None
                return
            loop_block = "\n".join(qlines)
            _emit("boost_loop_done", f"反问目录完成（{len(qlines)} 问, 定稿后注入）")
        except Exception as exc:
            logger.warning("[boost] P-L failed: %s", exc)
            _emit("boost_loop_error", f"反问目录失败：{str(exc)[:100]}")
            loop_block = None

    def _insert_loop_block(text: str) -> str:
        """确定性插入反问目录。

        锚点优先级 (2026-08-25 v2): 认知校准句组之后 > '剥开看'引导词之后。
        校准句("千万别当成简单的XX" + 定调句)必须先于反问目录 — 先立视角再抛
       目录, 目录紧跟定调承接正文; 插在校准前会被校准块截断连贯性 (实测)。
        """
        import re as _re3
        if not loop_block or loop_block in text:
            return text
        anchor = None
        # 优先: 认知校准句组之后 (校准句 + 支撑/定调句, 最多再 3 行, 吃到空行前)
        m_cal = _re3.search(r"千万别当成[^\n]*(?:\n[^\n]*){0,3}", text)
        if m_cal:
            anchor = m_cal.end()
        if anchor is None:
            for key in ("这事儿咱们得剥开看", "剥开看", "确定的逻辑"):
                i = text.find(key)
                if i != -1:
                    end = text.find("。", i)
                    anchor = end + 1 if end != -1 else i + len(key)
                    break
        if anchor is None:
            m = _re3.search(r"大家好[^。]*。", text)
            anchor = m.end() if m else 0
        return text[:anchor] + "\n" + loop_block + "\n" + text[anchor:]

    # ═════ 执行流水线 (2026-08-25: P-L 反问注入 → P4 精修; P5 已拆离) ═════
    # 7层洗稿已是完整爆款结构; P1(电击开场)/P2(预埋)/P3(呼吸点) 是旧六模块逻辑,
    # 跑7层稿只会覆盖钩子/身份段/结尾. 实证(_test_7layer_ab A/B)确认 P1-P3 零增益纯破坏.
    # 现行: P-L(geo: 反问目录注入, 独立层) → P4(逐句表达精修, 1:1锁定结构)。
    # P5 拆离 (2026-08-25): 移到 audio.py _do_tts 入口, 与 TTS 输入同源文本现场标注 —
    # 消灭"改稿后旧标注错配"窗口, boost 回归纯内容职责。annotate_emotions 保留供调用。
    _emit("boost_start", "爆品改造开始（P-L反问目录→P4精修）")

    # P-L 反问目录 (2026-09-07 全赛道: 原仅 geo — tech 缺承诺结构/平均时长抓手,
    # 用户拍板放开; "问序=回收序"本身即前 60 秒留存设计。失败不阻断, P4 走原稿)
    _run_loop()

    # P4 精修 (输入 = P-L 产物或原稿; P4 失败回退原稿, 不卡死)
    _run_p4()

    # final = P4 精修稿 (P4 失败回退原稿, 不卡死)
    final_text = p4_text or original
    if not final_text.strip():
        final_text = original

    # 剥离内部标注 (层标题/锚点/清单残留)
    final_text = clean_boosted_text(final_text)

    # P-L 反问目录注入 (2026-08-25): 定稿后确定性插入 — P4 精修碰不到反问层,
    # 免疫被当冗余删除; _insert_loop_block 内含幂等保护 (已存在则不重复插)。
    final_text = _insert_loop_block(final_text)

    # ── P6: 拼音纠音审计 (2026-08-25) ──
    # 生僻字/多音字由 config/tts_pinyin_map.json 词表在 TTS 入口自动标 <字|PINYIN>
    # (替代旧的"昇腾→生疼"同音换字)。此处只扫描提示, 让用户知道哪些词被照顾到;
    # 踩到新坑直接往词表加一行, 无需改代码。
    pinyin_fixes: list[str] = []
    try:
        from app.services.pinyin_fix import scan_pinyin_hits
        pinyin_fixes = scan_pinyin_hits(final_text)
        if pinyin_fixes:
            _emit("boost_pinyin_done", f"拼音纠音 {len(pinyin_fixes)} 处: {'、'.join(pinyin_fixes)}")
        else:
            _emit("boost_pinyin_done", "拼音纠音: 本稿无已知易错词")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[boost] pinyin scan failed: %s", exc)

    # ── P7: 流量评审 (2026-08-25 复刻豆包五维框架) ── 只评审不改稿, 报告供人决策
    flow_audit: dict[str, Any] | None = None
    try:
        # 重复跳过 (2026-08-25): 同稿(sha 相同)重跑 boost 时复用上次评审 —
        # 评审是只读仪表盘, 稿没变结论不变; LRU 重启丢失可接受 (单次评审 ~2K token)。
        import hashlib as _hl3
        _sha = _hl3.sha1(final_text.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]  # noqa: S324 — LRU 缓存指纹非密码学
        cached_audit = _FLOW_AUDIT_CACHE.get(script_id)
        if cached_audit and cached_audit[0] == _sha:
            flow_audit = cached_audit[1]
            _emit("boost_p7_done", "流量评审: 稿件未变，复用上次评审")
        else:
            flow_audit = audit_flow_metrics(final_text)
            if flow_audit:
                _FLOW_AUDIT_CACHE[script_id] = (_sha, flow_audit)
        if flow_audit:
            _emit("boost_p7_done",
                  f"流量评审 {flow_audit.get('overall', '?')}: "
                  f"评论率{flow_audit.get('comment', {}).get('estimate', '?')} · "
                  f"短板: {'; '.join(flow_audit.get('weaknesses', [])[:2])}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[boost] P7 audit failed: %s", exc)

    return {
        "boosted_text": final_text,
        "boost_titles": [],  # P1 砍掉, 不再产标题候选
        "p5_annotated": None,  # P5 已拆离 (2026-08-25 → audio.py 生成时现场标), 字段留兼容
        "p4_ok": p4_text is not None,
        "p5_ok": False,
        "loop_ok": loop_block is not None,  # P-L 反问目录 (geo) 是否注入成功
        "pinyin_fixes": pinyin_fixes,
        "flow_audit": flow_audit,
    }


def audit_flow_metrics(text: str) -> dict[str, Any] | None:
    """P7 独立入口: 五维流量评审 (停留/完播/点赞/评论/收藏 + 优势/短板/微调建议)。

    框架复刻豆包测评标准 (2026-08-25): 逐维给"预估值+档位+50字理由", 输出结构化
    JSON。失败返回 None (评审是仪表盘, 不许阻断产线)。
    """
    try:
        raw = _call(P7_PROMPT + "\n\n【待评审最终稿】\n" + text, json_mode=True, max_tokens=2500)
        data = _extract_json(raw)
        if isinstance(data, dict) and data.get("overall"):
            return data
        logger.warning("[boost] P7 parse failed: %s", str(raw)[:100])
        return None
    except Exception as exc:
        logger.warning("[boost] P7 audit call failed: %s", exc)
        return None
