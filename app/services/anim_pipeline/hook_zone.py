"""60s 特区 (0919 用户令): 正文音频轴 [0, 60s) = 首分钟流量池特区。

口径 (定死): "1分钟" = 正文 60s (音频轴 0-60), 不含剪辑开头 opening_sec;
全集轴特区 = [tc, tc+60] (ep3 即 5.5-65.5s)。

- 所有视频的前 60s 都需要 — 平台推荐机制锁定, 冲平均在线时长。
- 镜槽系统预装 (DP): 目标带 3-6s (偏好 ≤5), 弹性带 [2.8, 6.3];
  >6.5s 长句句内逗号切 + 静音吸附。时间轴零 LLM — 导演只填画面。
- 特区跨场不打散场结构 (A1/A2 界不动); 场窗与特区相交的部分按窗裁块装包。
- 页面特区横幅 (anim.html) 与规划注入 (director2.plan_scene) 共用本模块。
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import constitution as CST

logger = logging.getLogger(__name__)

# 1013 P0 整合: 尺度与特区宪法迁唯一宪法 (config/anim_constitution.json) —
# 模块级同名常量保留 (消费方按属性导入), 数值源头收口一处 (冲突11 五尺归一)。
_DT = CST.duration_table()
ZONE_END = CST.zone_end()   # 特区右界 (音频轴, 正文秒)
SLOT_LO, SLOT_HI = _DT["slot_lo"], _DT["slot_hi"]        # 目标带 (0919: 3s 底线, 5s 可放宽到 6s)
ELASTIC_LO, ELASTIC_HI = _DT["elastic_lo"], _DT["elastic_hi"]  # 弹性带 (每集 ~1 镜落此)
SPLIT_SENT = _DT["split_sent"]  # 超此长的句 → 句内逗号切 (1005 句末吸附令; H3 帽留余量)

# 特区宪法 (注入 plan_scene user prompt; 覆盖宪法中"场内共享场景"等冲突条款)
# 1013 P0: 条款文本由 constitution.prompt_block("hook") 生成 (散文宪法唯一出口,
# 数值随宪法表联动); 本模块不再私设散文。
ZONE_RULES = CST.prompt_block("hook")



def zone_scene_issues(tags: list[str | None]) -> list[str]:
    """特区 scene_tag 审计 (确定性执法): 缺标/组内重复 → 问题列表 (硬校验拒稿).

    1008 语义升级: scene_tag = 微位名 (同大空间内), 禁重复逻辑不变 —
    换的是规则含义 (场景→微位), 执法口径不动。"""
    issues: list[str] = []
    seen: dict[str, int] = {}
    for i, tag in enumerate(tags):
        t = str(tag or "").strip()
        if not t:
            issues.append(f"特区镜{i + 1} 缺 scene_tag (2-6字场景名)")
            continue
        if t in seen:
            issues.append(f"特区镜{i + 1} scene_tag「{t}」与镜{seen[t] + 1}重复 — 组内场景禁一致")
        else:
            seen[t] = i
    return issues


_STRIP_RE = re.compile(
    r"1988赛璐璐风格|赛璐璐|卡通大头|固定机位|平视|俯瞰|特写|中景|全景|空拍|远景|近景"
    r"|镜头|极轻微推近|极轻微拉远|明亮|深夜|，|。|、|；|：|\s")


def zone_composition_issues(zone_shots: list[dict[str, Any]],
                            ratio_hi: float = 0.52) -> list[str]:
    """特区构图同源软审计 (0919 a1 实弹教训: scene_tag 各异但骨架全是"桌面+稿纸+红笔")。

    keyframe 剥风格/机位词后逐对相似度, ≥ratio_hi 点名 — 走批评轮 (软), 不硬拒。
    """
    import difflib
    txts = [_STRIP_RE.sub("", str(s.get("keyframe_zh") or ""))[:220]
            for s in zone_shots]
    issues: list[str] = []
    for i in range(len(txts)):
        for j in range(i + 1, len(txts)):
            if not txts[i] or not txts[j]:
                continue
            r = difflib.SequenceMatcher(None, txts[i], txts[j]).ratio()
            if r >= ratio_hi:
                issues.append(
                    f"{zone_shots[i].get('shot_id')}↔{zone_shots[j].get('shot_id')} "
                    f"构图相似度 {r:.0%} — 母题未轮换 (同一物件组合换房间)")
    return issues


def _seg_cost(d: float) -> float:
    """DP 段成本: 命中带 0 / 放宽带轻罚 / 弹性带中罚 / 越界重罚 (不可切时硬吃)。"""
    if d <= 0:
        return 1e9
    if SLOT_LO <= d <= 5.0:
        return 0.02            # 命中目标带 (含每槽基线 → 同分偏好少镜)
    if 5.0 < d <= SLOT_HI:
        return 0.6
    if ELASTIC_LO <= d < SLOT_LO:
        return 1.2
    if SLOT_HI < d <= ELASTIC_HI:
        return 1.5
    if 2.5 <= d < ELASTIC_LO:
        return 4.0
    if ELASTIC_HI < d <= 7.5:
        return 4.5
    if 1.5 <= d < 2.5:
        return 6.0
    # 0923 根修: >7.5s 改比例罚 — 旧平顶 12.0 导致 1槽60s(cost 12) 比 3槽20s(cost 36) "便宜",
    # DP 永远选巨槽; 比例罚 d*0.8 → 8s=6.4, 12s=9.6, 20s=16, 60s=48 (多小槽恒优于巨槽)
    # 1008 修 (ep2 A1 实锤: 8.3s+5.3s 两句被 DP 合并成 13.6s 巨槽 — d*0.8 线性成本
    # 对"拆不拆"无偏好: 8.3→12地板 + 5.3→0.6 = 12.6 > 合并13.6→12, 反而合并不拆!):
    # 超线性平方罚 — 时长翻倍代价翻四倍, 拆分恒占优; 4.5 地板衔接 7.5 段不倒挂
    return max(4.5, (d / SLOT_HI) ** 2 * 2.0)


def _slice_text_frac(text: str, f0: float, f1: float) -> str:
    """跨窗句的窗内份额文本: 字符比例切 (narration 用, 连续子串即可)。"""
    if not text:
        return ""
    n = len(text)
    return text[max(0, int(n * f0)):max(1, int(n * f1))]


@lru_cache(maxsize=128)
def _pack_cands(wav: str) -> tuple[float, ...]:
    """包级静音候选中点缓存 (1010: 同包多句/多窗共用一次 ffmpeg 检测)."""
    try:
        from scripts.tts_lib.silence_split import (
            _detect_silences,
            _silence_candidates,
            _wav_duration,
        )
        dur = _wav_duration(Path(wav))
        if dur <= 0:
            return ()
        return tuple(_silence_candidates(_detect_silences(Path(wav)), dur))
    except Exception:  # noqa: BLE001 — 检测失败 = 无候选, 调用方回退原值
        return ()


# 1010 s42/s43 实锤: 比例切点距停顿 1.202s, 超 1.2s 窗 0.002s 落空 → 切在语音
# 正中间 (音画错位体感来源)。吸到真实停顿永远优于比例点 → 窗放宽到 1.8s。
_SNAP_CUT = 1.8   # 逗号切点静音吸附窗
_SNAP_WIN = 0.6   # 硬窗边 (60s 特区/场窗) 静音吸附半径


@lru_cache(maxsize=128)
def _stops(wav: str) -> tuple[float, ...]:
    """包级停顿中点 (敏版 -35dB:d=0.12, ≥0.10s 微顿也算) — 硬窗边吸附用.

    1010 s11/s12 实锤: 60s 硬界切在"书/里"词中间, 该微顿 (0.15s) 在句级版
    -30dB:d=0.2 下检不出 — 窗边吸附需更敏的停顿表 (独立缓存, 不动句级表)。"""
    try:
        import subprocess
        r = subprocess.run(
            ["ffmpeg", "-i", wav, "-af", "silencedetect=noise=-35dB:d=0.12",
             "-f", "null", "-"],
            capture_output=True, text=True, errors="replace", timeout=30)
        ss = [float(m.group(1)) for m in re.finditer(r"silence_start: ([\d.]+)", r.stderr)]
        se = [float(m.group(1)) for m in re.finditer(r"silence_end: ([\d.]+)", r.stderr)]
        return tuple(round((a + b) / 2, 3) for a, b in zip(ss, se) if b - a >= 0.10)
    except Exception:  # noqa: BLE001 — 检测失败 = 无候选, 回退原值
        return ()


def _snap_to_silence(t: float, sent: dict[str, Any], lim: float) -> float:
    """硬边 t → ±lim 内最近停顿中点 (1010 s11/s12 实锤: 60s 硬界切在"书/里"
    词中间, 真实停顿就在 0.4s 后)。无候选/超窗原样返回; 相邻窗对同一句调用
    吸到同一停顿 (候选确定性) → 不产生跨窗重叠。"""
    wav = str(sent.get("file") or "")
    pack_t0 = float(sent.get("pack_t0") or -1e9)
    if not wav or pack_t0 < -1e8:
        return t
    cands = _stops(wav)
    if not cands:
        return t
    rel = t - pack_t0
    near = min(cands, key=lambda c: abs(c - rel))
    return round(pack_t0 + near, 2) if abs(near - rel) <= lim else t


def _comma_split(u: dict[str, Any]) -> list[dict[str, Any]]:
    """>SPLIT_SENT 句 → 句内子句切 (逗/分/冒), 时间 = 字数比例 + 静音吸附。

    静音候选取自所在包 wav (sent_table 需带 file/pack_t0); 无 wav/无候选回退纯比例。
    返回子句 pieces (递归无必要: 子句级再超限概率≈0)。
    """
    text = str(u.get("text") or "")
    t0, t1 = float(u["t_start"]), float(u["t_end"])
    dur = t1 - t0
    parts = [p for p in re.split(r"(?<=[，,；;：:])", text) if p.strip()]
    if len(parts) < 2 or dur <= SPLIT_SENT:
        return [u]
    weights = [len(p) + 0.6 for p in parts]  # 句读处小停顿加成
    total_w = sum(weights) or 1.0
    # 静音候选 (包内绝对秒, 单元范围内过滤; 1010 缓存 — 同包共用一次 ffmpeg)
    cands: list[float] = []
    wav = str(u.get("file") or "")
    pack_t0 = float(u.get("pack_t0") or -1e9)
    if wav and pack_t0 > -1e8:
        cands = [c for c in _pack_cands(wav)
                 if t0 - pack_t0 + 0.05 <= c <= t1 - pack_t0 - 0.05]
    pieces: list[dict[str, Any]] = []
    cum_t = t0
    cum_w = 0.0
    for p, w in zip(parts[:-1], weights[:-1]):
        cum_w += w
        exp_abs = t0 + dur * (cum_w / total_w)
        cut = exp_abs
        if cands:
            rel = exp_abs - pack_t0
            near = min(cands, key=lambda c: abs(c - rel)) if cands else None
            if near is not None and abs(near - rel) <= _SNAP_CUT:
                cut = pack_t0 + near
        cut = min(max(cut, cum_t + 0.3), t1 - 0.3)
        if cut <= cum_t + 0.05:
            continue
        pieces.append({"t_start": round(cum_t, 2), "t_end": round(cut, 2),
                       "text": p, "file": u.get("file"), "pack_t0": u.get("pack_t0")})
        cum_t = cut
    pieces.append({"t_start": round(cum_t, 2), "t_end": round(t1, 2),
                   "text": parts[-1] if cum_w + weights[-1] else text,
                   "file": u.get("file"), "pack_t0": u.get("pack_t0")})
    # 兜底: 切完仍有超限 piece 且子句≥2 → 不再递归 (概率≈0), 原样交付
    return pieces


def _absorb_short_units(units: list[dict[str, Any]],
                        min_u: float = 2.5) -> list[dict[str, Any]]:
    """短单元 (<min_u) 并邻, 但并后不超 7.5s (0923 修: 级联合并毁掉 DP 切点).

    旧版无条件并邻 → 级联后单元巨长 → DP 无切点 → 1 槽 60s (A1 实锤).
    修 = 并邻前检查合并后时长, 超过 CAP 则不并 (保留切点, 让 DP 弹性带处理)."""
    CAP = 7.5  # 并后上限 (超过则不并, 保留 DP 切点)
    out = [dict(u) for u in units]
    for _round in range(2):
        i = 0
        while i < len(out):
            d = float(out[i]["t_end"]) - float(out[i]["t_start"])
            if d >= min_u or len(out) == 1:
                i += 1
                continue
            if i > 0:
                prev_d = float(out[i - 1]["t_end"]) - float(out[i - 1]["t_start"])
                merged_d = float(out[i]["t_end"]) - float(out[i - 1]["t_start"])
                if merged_d <= CAP:  # 0923: 并后不超 CAP 才并
                    out[i - 1]["t_end"] = out[i]["t_end"]
                    out[i - 1]["text"] = str(out[i - 1]["text"]) + str(out[i]["text"])
                    out.pop(i)
                else:
                    i += 1  # 不并, 保留切点
            else:
                out[1]["t_start"] = out[0]["t_start"]
                out[1]["text"] = str(out[0]["text"]) + str(out[1]["text"])
                out.pop(0)
        # 终切: 并后超 SPLIT_SENT (1005 句末吸附令: >ELASTIC_HI 但 ≤9s 的整句
        # 不再为带宽劈开 — 越带槽 tolerated; 只劈 >9s 巨单元, 防 A2 全并 29.6s 巨槽)
        # 逗号切一遍, 切出的短片 (<min_u) 并回大侧
        final: list[dict[str, Any]] = []
        for u in out:
            if float(u["t_end"]) - float(u["t_start"]) > SPLIT_SENT:
                final.extend(_comma_split(u) or [u])
            else:
                final.append(u)
        k = 0
        while k < len(final):
            d = float(final[k]["t_end"]) - float(final[k]["t_start"])
            if d >= min_u or len(final) == 1:
                k += 1
                continue
            if k > 0 and (k + 1 >= len(final)
                          or float(final[k - 1]["t_end"]) - float(final[k - 1]["t_start"])
                          <= float(final[k + 1]["t_end"]) - float(final[k + 1]["t_start"])):
                final[k - 1]["t_end"] = final[k]["t_end"]
                final[k - 1]["text"] = str(final[k - 1]["text"]) + str(final[k]["text"])
                final.pop(k)
            elif k + 1 < len(final):
                final[k + 1]["t_start"] = final[k]["t_start"]
                final[k + 1]["text"] = str(final[k]["text"]) + str(final[k + 1]["text"])
                final.pop(k)
            else:
                k += 1
        if not any(float(f["t_end"]) - float(f["t_start"]) > SPLIT_SENT
                   for f in final) and \
           not any(float(f["t_end"]) - float(f["t_start"]) < min_u
                   for f in final):
            return final  # 收敛: 无超带无短片
        out = final
    return out


def pack_zone_slots(sent_table: list[dict[str, Any]] | None,
                    a0: float, a1: float,
                    zone_end: float = ZONE_END) -> list[dict[str, Any]]:
    """场窗 [a0, a1] ∩ 特区 [0, zone_end) → 镜槽列表 (音频轴)。

    单元 = 句级表条目按窗裁块 (跨窗句取窗内份额); 超长句逗号切; DP 装包
    目标带 [3,6] 弹性 [2.8,6.3]。首槽起 = a0, 末槽止 = min(a1, zone_end)。
    """
    w_end = min(a1, zone_end)
    # 1010: 碎窗闸 0.35s — 场尾零头窗 (<0.35s) 装不出有效槽, 只产 0.0x s 碎槽
    # (A1 尾 0.17s 实锤: 碎槽与邻场吸附后首槽重叠)。零头由邻场跨窗覆盖。
    if w_end - a0 < 0.35:
        return []
    # 1014 修 (ep3 s13/s14 词中裂实锤: 品牌句"多一个硬本事"被 60s 硬界劈成
    # "多一"+"个硬本事"): 品牌句跨特区界时, 窗界让位 — 整句不裁, 界界延伸
    # 到句末。品牌句 = 开场身份句 + 收官句, TTS 已是独立包, 句级表独立条目,
    # 唯一被劈的机会就是这里 (硬窗边裁块)。
    try:
        from .shots import is_brand_text as _ibt, norm_for_match as _nfm
        for s in sent_table or []:
            _t0, _t1 = float(s.get("t_start") or 0), float(s.get("t_end") or 0)
            if _t0 < w_end < _t1 and _ibt(_nfm(str(s.get("text") or ""))):
                w_end = _t1  # 界界让位: 整句归特区, 不裁
    except ImportError:
        pass
    units: list[dict[str, Any]] = []
    for s in sent_table or []:
        t0, t1 = float(s.get("t_start") or 0), float(s.get("t_end") or 0)
        if t1 <= a0 + 0.01 or t0 >= w_end - 0.01:
            continue
        p0, p1 = max(t0, a0), min(t1, w_end)
        if p1 - p0 < 0.05:
            continue
        text = str(s.get("text") or "")
        if t0 < p0 - 0.01 or t1 > p1 + 0.01:
            # 1010 s11/s12 实锤: 硬窗边 (60s 特区/场界) 可切在词中间 — 裁出的
            # 边 ±_SNAP_WIN 内吸附最近停顿 (相邻窗同句同候选 → 确定性收敛不重叠)
            if t0 < p0 - 0.01:
                p0 = min(_snap_to_silence(p0, s, _SNAP_WIN), p1 - 0.05)
            if t1 > p1 + 0.01:
                p1 = max(_snap_to_silence(p1, s, _SNAP_WIN), p0 + 0.05)
            span = max(t1 - t0, 1e-6)
            text = _slice_text_frac(text, (p0 - t0) / span, (p1 - t0) / span)
        u = {"t_start": round(p0, 2), "t_end": round(p1, 2), "text": text,
             "file": s.get("file"), "pack_t0": s.get("pack_t0")}
        units.extend(_comma_split(u) if p1 - p0 > SPLIT_SENT else [u])
    if not units:
        return []
    # 贴窗 (1010 改: 跨窗块已裁+吸附 → 吸附点 (距窗边 >0.05s) 优先保留不被钉回;
    # 仅对未吸附偏离的边界做 round 兜底钉齐)
    if abs(float(units[0]["t_start"]) - a0) < 0.05:
        units[0]["t_start"] = a0
    if abs(float(units[-1]["t_end"]) - w_end) < 0.05:
        units[-1]["t_end"] = w_end

    def _pack_dp(us: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """单元段 → 槽 (吸收短片 + 补切长句 + DP 装包) — 原管线, 段内独立执行."""
        us = _absorb_short_units(us)
        # 0923 修: 吸收后再切长单元 — 级联合并/强制补尾会造出 >SPLIT_SENT 的巨单元,
        # DP 丢失切点只能出 1 槽 (A1 前60s 一镜实锤); 逗号切恢复切点
        _re_split = []
        for u in us:
            d = float(u["t_end"]) - float(u["t_start"])
            _re_split.extend(_comma_split(u) if d > SPLIT_SENT else [u])
        us = _re_split
        n = len(us)
        t0s = [float(u["t_start"]) for u in us]
        t1s = [float(u["t_end"]) for u in us]
        INF = 1e18
        dp = [INF] * (n + 1)
        dp[0] = 0.0
        cut = [0] * (n + 1)
        for j in range(1, n + 1):
            for i in range(j - 1, -1, -1):
                if dp[i] >= INF:
                    continue
                d = t1s[j - 1] - t0s[i]
                c = dp[i] + _seg_cost(d)
                if c < dp[j] - 1e-9:
                    dp[j] = c
                    cut[j] = i
        out: list[dict[str, Any]] = []
        j = n
        while j > 0:
            i = cut[j]
            out.append({"t_start": t0s[i], "t_end": t1s[j - 1],
                        "text": "".join(str(u["text"]) for u in us[i:j])})
            j = i
        out.reverse()
        return out

    # 1004 修: 品牌句原子槽 — 身份句/收官句独占一镜 (槽文本=纯品牌句, splice 后
    # tag_brand_cards 确定性定卡)。DP 贪心会把品牌句并进邻槽, 并槽文本含品牌句
    # → 整槽被盖品牌卡资产, 邻句口播被定稿卡吃掉 (1001 ep4 89% 切走同病根)。
    # 1004-Ⅱ 修 (ep5 s65/s66 实锤): 原子=**整个 TTS 包**非单句 — 收官句两个
    # 句子, 规则只钉前半 ("咱们下一集，见。" 无"我是老谭"前缀永不命中) → 句级
    # 原子把仪式句劈成 品牌镜+孤儿镜; 系统注入句天然独占一包, 同包伙伴收进同槽。
    from app.services.anim_pipeline.shots import is_brand_text, norm_for_match

    def _is_brand(u: dict[str, Any]) -> bool:
        return is_brand_text(norm_for_match(str(u.get("text") or "")))

    def _pk(u: dict[str, Any]) -> tuple | None:
        f, p0 = str(u.get("file") or ""), u.get("pack_t0")
        return (f, round(float(p0), 2)) if (f and p0 is not None) else None

    if any(_is_brand(u) for u in units):
        slots: list[dict[str, Any]] = []
        run: list[dict[str, Any]] = []
        skip: set[int] = set()
        for idx, u in enumerate(units):
            if idx in skip:
                continue
            if _is_brand(u):
                if run:
                    slots.extend(_pack_dp(run))
                    run = []
                group = [u]  # 同包连续伙伴并入 (仪式句后半 "咱们下一集，见。")
                group_idx = [idx]
                key = _pk(u)
                if key is not None:
                    for j in range(idx + 1, len(units)):
                        m = units[j]
                        if _pk(m) != key or float(m["t_start"]) - float(group[-1]["t_end"]) > 0.35:
                            break
                        group.append(m)
                        group_idx.append(j)
                span = float(group[-1]["t_end"]) - float(group[0]["t_start"])
                if len(group) == 1 or span <= 10.0:
                    skip.update(group_idx[1:])
                    slots.append({"t_start": group[0]["t_start"], "t_end": group[-1]["t_end"],
                                  "text": "".join(str(g["text"]) for g in group)})
                else:
                    # 1006 修 (卓有ep5 实锤): 病态巨包回退句级原子 — 但伙伴**必须回到
                    # 正常 DP 流**; 旧版先标 skip 再回退 → 4 句正文被整体丢弃 → 13.9s
                    # 大洞 → "时间轴断裂"校验连环毙 (身份句未独包的旧音频必踩)
                    slots.append(u)
            else:
                run.append(u)
        if run:
            slots.extend(_pack_dp(run))
        slots.sort(key=lambda s: float(s["t_start"]))
    else:
        slots = _pack_dp(units)
    viol = [round(s["t_end"] - s["t_start"], 2) for s in slots
            if not ELASTIC_LO <= s["t_end"] - s["t_start"] <= ELASTIC_HI]
    logger.info("[hook-zone] 窗 [%.1f,%.1f] %d 单元 → %d 槽 (总成本 %.2f)%s",
                a0, w_end, len(units), len(slots),
                sum(_seg_cost(float(s["t_end"]) - float(s["t_start"])) for s in slots),
                f" 越带槽: {viol}" if viol else "")
    return slots


def enforce_zone_shots(groups: list[dict[str, Any]], slots: list[dict[str, Any]],
                       ) -> list[dict[str, Any]]:
    """LLM 输出执法: slot 标记镜 ↔ 槽位一一对应, 回填时间/口播/特区标。

    槽数不符 → ValueError (调用方重试网接住, 不落盘)。返回原 groups (原位改)。
    """
    got = sum(1 for g in groups for r in (g.get("shots") or [])
              if r.get("slot") is not None and int(r.get("slot") or 0) <= len(slots))
    if got != len(slots):
        raise ValueError(f"特区镜数 {got} ≠ 槽位数 {len(slots)} — slot 标记需逐槽一镜")
    cursor = 0
    for g in groups:
        inner = g.get("shots") or []
        ordered = sorted([r for r in inner
                          if r.get("slot") is not None
                          and int(r.get("slot") or 0) <= len(slots)],
                         key=lambda r: int(r.get("slot") or 0))
        for raw in ordered:
            sl = slots[cursor]
            cursor += 1
            raw.pop("slot", None)
            raw["t_start"] = sl["t_start"]
            raw["t_end"] = sl["t_end"]
            raw["narration"] = sl["text"][:300]
            raw["zone"] = "hook60"
    return groups


def enforce_scene_shots(groups: list[dict[str, Any]],
                        scene_slots: list[dict[str, Any]],
                        slot_offset: int = 0,
                        ) -> list[dict[str, Any]]:
    """普通镜槽执法 (0919 全片装包制): slot 标镜 (槽号 > offset) ↔ 普通槽一一对应.

    回填时间/口播, 打 packed 标 (后续对齐链路保护 — 槽位=句包边, 禁 needle 重切).
    槽数不符 → ValueError (调用方重试网接住)."""
    marked = sorted(
        (r for g in groups for r in (g.get("shots") or [])
         if r.get("slot") is not None and int(r.get("slot") or 0) > slot_offset),
        key=lambda r: int(r.get("slot") or 0))
    if len(marked) != len(scene_slots):
        raise ValueError(f"普通槽镜数 {len(marked)} ≠ 槽位数 {len(scene_slots)} — "
                         f"slot 标记需逐槽一镜")
    for raw, sl in zip(marked, scene_slots):
        raw.pop("slot", None)
        raw["t_start"] = sl["t_start"]
        raw["t_end"] = sl["t_end"]
        raw["narration"] = str(sl["text"])[:300]
        raw["packed"] = True
    return groups
