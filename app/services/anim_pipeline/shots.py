# -*- coding: utf-8 -*-
"""shots.json — 动画管线 v1 单一事实源 (schema + IO + 状态机).

设计: docs/improvements/进行中-20260911-动画管线v1-K2生图-H3图生.md 架构定稿.
每镜状态: planned → img_done → approved → anim_done
          (人检打回: img_done→planned 换seed; H3翻车: →anim_fail 降级用静态图)
A 层(工坊页脊柱)不进 GPU, 不出现在 shots 数组, 只存在于 a_layer 摘要.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any

from .config import load

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# 状态机合法状态
STATUSES = ("planned", "img_done", "approved", "anim_done", "anim_fail")
PAGE_TYPES = ("B", "C")  # A 层不进 shots


def _safe(name: str) -> str:
    """文件名清洗 (对齐 book_service/l0.py 的做法)."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "unnamed"


def new_seed() -> int:
    """15 位随机 seed (对齐用户最爱图 492564323177030 量级)."""
    return random.randint(10**14, 10**15 - 1)


def anim_output_root() -> Path:
    return Path(load().output_root)


def ep_dir(book_title: str, ep: int) -> Path:
    return anim_output_root() / _safe(book_title) / f"ep{ep}"


def shots_path(book_title: str, ep: int) -> Path:
    return ep_dir(book_title, ep) / "shots.json"


# 品牌卡匹配规范化: 剥标点/空白 (句式微变不影响命中)
_MATCH_STRIP_RE = re.compile(
    r"[\s，。！？；：、,.!?:;\"'“”‘’（）《》〈〉【】\[\](){}<>—…·]+")
# 0918 比对口径同步: 句级表已洗涤 TTS 指令 (剥 -Xs- / 拼音 <字|PINYIN>→字),
# 镜 narration 存的可能是洗涤前旧形态 — 比对前同款剥离, 形变不再误判"文案变"
# (align 8 镜误打回实锤: 视频内容等价, 只是标记/拼音的书写形差异)
_MATCH_TTS_MARK_PAUSE = re.compile(r"-\d+(?:\.\d+)?s-")
_MATCH_TTS_MARK_PINYIN = re.compile(r"<([^|>]+)\|[^>]+>")


def norm_for_match(text: str) -> str:
    t = _MATCH_TTS_MARK_PAUSE.sub("", text or "")
    t = _MATCH_TTS_MARK_PINYIN.sub(r"\1", t)
    return _MATCH_STRIP_RE.sub("", t)


def tag_brand_cards(doc: dict[str, Any]) -> list[str]:
    """品牌卡补标 (幂等, 兼容旧 doc): narration 规范化后全含某规则子串组 → brand_card=True.

    落款仪式句/收官品牌句是冻结文案 (config/laotan-book.txt), 由此确定性定位品牌卡镜 —
    命中镜 k2/h3 走零 GPU 直通, 草稿装配插定稿卡资产 (品牌卡_配方.txt 复用令).
    """
    tagged: list[str] = []
    for s in doc.get("shots") or []:
        if not s.get("brand_card") and is_brand_text(norm_for_match(s.get("narration", ""))):
            s["brand_card"] = True
            tagged.append(s["shot_id"])
        if s.get("brand_card") and (s.get("h3_text_event") or {}).get("text"):
            # 1017 修 (ep5 s13 ×2 同错实锤): 品牌镜直通无渲染通道, writer 偶带
            # h3_text_event 必踩 R2 硬闸 → 定卡处确定性剥除, 不赌 LLM 记得规则
            s.pop("h3_text_event", None)
    return tagged


def is_brand_text(norm: str) -> bool:
    """规范化文本是否命中品牌句规则 (tag_brand_cards 同一套 match_rules)."""
    if not norm:
        return False
    return any(all(p in norm for p in rule) for rule in load().brand_card.match_rules)


def brand_pack_spans(sent_table: list[dict[str, Any]] | None) -> list[tuple[float, float]]:
    """句级表中品牌句 (落款身份句/收官句, 可能多处) 的 (起点, 终点) 列表 (音频轴, 升序).

    单场重规划的窗界守卫用: 窗界切进品牌句包内部 → 钳回包界 (1001 ep4 实锤:
    旧账场界把仪式句包切走 89% 给 s99, 与 A8 签名镜成双品牌卡); 完整落在窗内
    → 本场接手 (hook_zone 原子槽 + tag_brand_cards 定卡, 1004 定位 ep5 教训:
    一律钳回会把中段身份句挖成 46.7s 骨架洞 → 分镜层死锁)。
    **1004-Ⅱ 修 (ep5 s65/s66 实锤): span=品牌句所在 TTS 包的包络, 非单句** —
    收官句是两个句子 ("我是老谭…翻一座山。"/"咱们下一集，见。"), 规则只钉
    前半 (后半无"我是老谭"前缀永不命中) → 句级 span/原子槽把仪式句劈两镜;
    系统注入句天然独占一包 (包界即仪式句边界)。老表缺 file/pack_t0 退化为句。
    注: 句级表 t_start 即绝对轴 (_sentence_segs 出口), 勿加 pack_t0。
    """
    entries = [s for s in (sent_table or []) if str(s.get("text") or "").strip()]

    def _pk(s: dict[str, Any]) -> tuple | None:
        f, p0 = str(s.get("file") or ""), s.get("pack_t0")
        return (f, round(float(p0), 2)) if (f and p0 is not None) else None

    spans: list[tuple[float, float]] = []
    for s in entries:
        if not is_brand_text(norm_for_match(str(s.get("text") or ""))):
            continue
        key = _pk(s)
        mates = [m for m in entries if key is not None and _pk(m) == key] or [s]
        t0 = round(min(float(m.get("t_start") or 0) for m in mates), 2)
        t1 = round(max(float(m.get("t_end") or 0) for m in mates), 2)
        # 1006 修 (卓有ep5 实锤): 身份句未独包的旧音频 — 品牌句与正文焊在一个
        # >10s 巨包, 整包包络会把守卫错钳到正文上; 包络超 10s 退化为品牌句本身
        if t1 - t0 > 10.0:
            t0 = round(float(s.get("t_start") or 0), 2)
            t1 = round(float(s.get("t_end") or 0), 2)
        spans.append((t0, t1))
    return sorted(set(spans))


def brand_asset_rel(book_title: str, ep: int, kind: str) -> str:
    """品牌资产相对 ep 目录的路径 (kind: video|keyframe|title_bg), 统一正斜杠."""
    cfg = load()
    abs_path = {
        "video": cfg.brand_card.video,
        "keyframe": cfg.brand_card.keyframe,
        "title_bg": cfg.brand_card.title_bg,
    }[kind]
    return Path(os.path.relpath(abs_path, ep_dir(book_title, ep))).as_posix()


def load_doc(book_title: str, ep: int) -> dict[str, Any]:
    p = shots_path(book_title, ep)
    if not p.exists():
        raise FileNotFoundError(f"shots.json 不存在: {p} (先跑 plan)")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("version") != SCHEMA_VERSION:
        logger.warning("shots.json schema 版本不匹配: %s (当前 %s)", doc.get("version"), SCHEMA_VERSION)
    tagged = tag_brand_cards(doc)  # 内存补标 (旧 doc 兼容), 随任一写操作持久化
    if tagged:
        logger.info("[shots] 品牌卡补标: %s", ",".join(tagged))
    return doc


def save(doc: dict[str, Any]) -> Path:
    p = shots_path(doc["book_title"], doc["ep"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def find_shot(doc: dict[str, Any], shot_id: str) -> dict[str, Any]:
    for s in doc["shots"]:
        if s["shot_id"] == shot_id:
            return s
    raise KeyError(f"shot_id 不存在: {shot_id}")


def transition(shot: dict[str, Any], new_status: str) -> None:
    if new_status not in STATUSES:
        raise ValueError(f"非法状态: {new_status}")
    shot["status"] = new_status
    shot["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")


# 1017 W4: 单拍兜底 motion 唯一常量 (旧三版漂移: 'Gentle ambient motion, very
# slight push-in.' ×5 / 'the scene evolves with subtle ambient motion' /
# 'The scene holds calm and still...' — canonical=director2 主路径版逐字;
# 本拍本就违反反静止律, 存在意义=beats_enrich 失败时的最后兜底)
SINGLE_BEAT_FALLBACK = "Gentle ambient motion, very slight push-in."


def stretch_beats(shot: dict[str, Any], seg_len: float) -> None:
    """anim.duration_s 与 beats 伸缩对齐 seg_len (拍 motion 不动, 时间轴对齐).

    planner auto_fix 与音频归真 (align) 共用: 音频先行令下, 未生成视频的镜
    beats 一律按真实音频秒数铺 — H3 首次生成的时长即正确时长.
    """
    anim = shot["anim"]
    beats = anim.get("beats") or []
    if not beats:
        anim["beats"] = [{"t_start": 0, "t_end": seg_len,
                          "motion": SINGLE_BEAT_FALLBACK}]
    else:
        total = sum(float(b["t_end"]) - float(b["t_start"]) for b in beats)
        if total > 0 and abs(total - seg_len) > 0.01:
            scale = seg_len / total
            t = 0.0
            for b in beats:
                dur = round((float(b["t_end"]) - float(b["t_start"])) * scale, 2)
                b["t_start"], b["t_end"] = t, round(t + dur, 2)
                t = b["t_end"]
            beats[-1]["t_end"] = seg_len  # 抹平累计误差
    anim["duration_s"] = seg_len


def strip_media_on_narr_change(old_doc: dict[str, Any] | None, doc: dict[str, Any]) -> list[str]:
    """文案变/槽漂移 → 媒体失效 (0916 错配根治, 两轮强化).

    ① narration 变 (s53 实锤): 清视频+图回 planned (内容变了全重来);
    ② span 变 (粉卡差额实锤: 归真改槽 8.31s 而 beats 还停旧槽 5.69 → H3 按
       beats 出片必然短缺): beats 重拉到新槽; 槽变长>0.3s 且有视频 → 视频保
       不住 (清 video_file 回 approved, 图保留 — 画面内容没变, 只需 H3 重出);
       槽变短 → 保留 (装配端截取)."""
    if not old_doc:
        return []
    old = {s["shot_id"]: s for s in old_doc.get("shots") or []}
    stripped = []
    for s in doc.get("shots") or []:
        sid = s["shot_id"]
        o = old.get(sid)
        if o is None:
            continue
        cur_n = norm_for_match(s.get("narration") or "")
        prev_n = norm_for_match(o.get("narration") or "")
        cur_span = float(s.get("t_end", 0)) - float(s.get("t_start", 0))
        prev_span = float(o.get("t_end", 0)) - float(o.get("t_start", 0))
        if prev_n != cur_n:
            # 0919 相似度容忍 (align 平移误杀实锤: 音频编辑/对齐后句界挪位重切 —
            # 句子在镜间挪边 ≠ 内容变, s68/s18/s33 三枚成片视频被剥链接盘上却完
            # 好): 归一文本相似度 ≥0.55 判"边界重切"(保媒体, 时长规则②接管);
            # <0.55 = 真文案变 (reroll/重规划), 照剥不误
            import difflib
            if difflib.SequenceMatcher(None, prev_n, cur_n).ratio() < 0.55:
                if s.pop("video_file", None) or s.pop("image_file", None):
                    s["status"] = "planned"
                    s.pop("gen_narr_sha", None)
                    stripped.append(f"{sid}(文案变)")
        elif abs(cur_span - prev_span) > 0.05 and isinstance(s.get("anim"), dict):
            from .director2 import H3_MAX_SPAN as _CAP  # 函数级导入防环
            # 0918 根因修正: old_dur 必须在 stretch 前读 — 旧序 (stretch→读) 恒读到
            # 新值, "槽变长>0.3s 清视频"保护从未生效 (s17/s23 短片粉卡实锤, 死分支多日)
            old_dur = float(s["anim"].get("duration_s") or 0)
            stretch_beats(s, min(round(cur_span, 2), _CAP))
            if s.get("video_file") and cur_span > old_dur + 0.3:
                s.pop("video_file", None)   # 图保留: 内容没变, 只需 H3 按新 beats 重出
                s["status"] = "approved"
                s.pop("gen_narr_sha", None)
                stripped.append(f"{sid}(槽{prev_span:.1f}→{cur_span:.1f}, 旧片{old_dur:.1f}s)")
    return stripped


def reject_for_reroll(shot: dict[str, Any], note: str = "") -> None:
    """人检打回: 换 seed (节点缓存坑: 同 prompt+seed 直接回旧图), 回 planned."""
    shot["seed"] = new_seed()
    shot["attempts"]["k2"] += 1
    shot["reject_note"] = note
    transition(shot, "planned")


def retry_anim(shot: dict[str, Any]) -> None:
    """H3 重试: 换 h3_seed, attempts+1."""
    shot["h3_seed"] = new_seed()
    shot["attempts"]["h3"] += 1


def summary(doc: dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    for s in doc["shots"]:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    return f"[{doc['book_title']} ep{doc['ep']}] {len(doc['shots'])} 镜: {parts}"


def validate(doc: dict[str, Any]) -> list[str]:
    """结构校验, 返回问题列表 (空=通过)."""
    problems: list[str] = []
    shots = doc.get("shots") or []
    if not shots:
        problems.append("shots 为空")
        return problems
    c_count = sum(1 for s in shots if s.get("page_type") == "C")
    if c_count < 1 or c_count > 4:
        problems.append(f"C 层钩子页 {c_count} 个, 预期每集 2-3")
    for s in shots:
        sid = s.get("shot_id", "?")
        if s.get("page_type") not in PAGE_TYPES:
            problems.append(f"{sid}: page_type 必须是 B/C")
        try:
            seg_len = float(s.get("t_end", 0)) - float(s.get("t_start", 0))
        except (TypeError, ValueError):
            seg_len = -1
        if seg_len <= 0 or seg_len > 8.5:
            problems.append(f"{sid}: 段长 {seg_len:.1f}s 超出 (0, 8s] (语义分段上限 8s)")
        if not (s.get("image_prompt_zh") or "").strip():
            problems.append(f"{sid}: image_prompt_zh 为空")
        anim = s.get("anim") or {}
        beats = anim.get("beats") or []
        if not beats:
            problems.append(f"{sid}: anim.beats 为空")
            continue
        t_prev = 0.0
        for b in beats:
            try:
                ts, te = float(b.get("t_start", -1)), float(b.get("t_end", -1))
            except (TypeError, ValueError):
                problems.append(f"{sid}: 拍时间非数字 {b.get('t_start')},{b.get('t_end')}")
                continue
            if ts < 0 or te <= ts:
                problems.append(f"{sid}: 拍时间非法 [{ts},{te})")
            if abs(ts - t_prev) > 0.01:
                problems.append(f"{sid}: 拍不连续 t={ts} (上一拍尾 {t_prev})")
            if not (b.get("motion") or "").strip():
                problems.append(f"{sid}: 拍 motion 为空")
            t_prev = te
        try:
            dur = float(anim.get("duration_s", 0))
        except (TypeError, ValueError):
            dur = -1
        if abs(t_prev - dur) > 0.01:
            problems.append(f"{sid}: beats 总长 {t_prev}s ≠ duration_s {dur}s")
        if dur < 2 or dur > 10:
            problems.append(f"{sid}: duration_s={dur}s 超出 [2,10]")
    return problems


def audit_reroll(doc: dict[str, Any], shot: dict[str, Any], reason: str) -> None:
    """重roll 账本 (0918 用户令: 文字扭曲先统计后治理 — 几集后字频规律自浮).

    每次人检打回/败镜重试, 追加一条到 outputs/动画/_资产/reroll_ledger.jsonl:
    该镜全部文字内容 (text_layer + beats 文字事件) + 原因 + 上下文。
    统计: scripts/reroll_stats.py。只记不判 — 数据说话。"""
    import json as _json
    import re as _re
    texts: list[str] = [str(t.get("text") or "") for t in (shot.get("text_layer") or [])]
    for b in ((shot.get("anim") or {}).get("beats") or []):
        m = str(b.get("motion") or "")
        for seg in _re.findall(r"render[^,.;]*?\"([^\"]{1,40})\"", m):
            texts.append(seg)
        for zh in _re.findall(r"[「\"']([^「」\"']{1,20}[字词卡])[」\"']", m):
            texts.append(zh)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "book": str(doc.get("book_title") or ""), "ep": doc.get("ep"),
        "shot_id": str(shot.get("shot_id")), "reason": reason[:120],
        "arc": shot.get("arc_id"),
        "texts": [t for t in texts if t.strip()],
        "attempts": dict(shot.get("attempts") or {}),
        "slot_s": round(float(shot.get("t_end") or 0) - float(shot.get("t_start") or 0), 2),
    }
    try:
        p = anim_output_root() / "_资产" / "reroll_ledger.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 记账失败绝不挡产线
        pass
