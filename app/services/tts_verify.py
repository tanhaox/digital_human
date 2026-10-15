# -*- coding: utf-8 -*-
"""TTS 错音回听闭环 (2026-09-03) — 合成后 ASR 校验 + 拼音对比 + 自动重合成修复.

问题: 多音字偶发错读 (说/行/重) 只能生成后靠人耳听成片才能发现。本模块把
发现时机提前到合成后秒级, 且大部分错音自动修复:

  1. 每行 wav 过 faster-whisper (复用 alignment_service 进程级单例, CUDA)
  2. ASR 文本 vs 稿件 双方转无声调拼音序列对齐 (difflib) — 同音字替换
     (的地得/他她它) 拼音相同不误报; 声/韵母不同 = 疑似错读
  3. 疑似错读 → 稿件侧按 pypinyin 语境读音生成 ``<字|PINYIN>`` 强制标注
     (pinyin_fix 协议, IndexTTS2 前端解析), 单行手术式重合成 (不动邻行)
  4. 复检一次: 修好 → fixed; 仍错 → 回滚原 wav 标红请人工听 (宁可不修不可修错)

注入点: tts_service.generate 在拼接 full_paragraph 之前调用 (拆书/新闻产线共用)。
失败静默降级 (whisper/pypinyin 缺失、显存不足、行过短) — 校验是增值不是依赖。
"""
from __future__ import annotations

import difflib
import functools
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = ["verify_pronunciation", "_diff_readings", "_pinyin_pairs", "_apply_markup"]

# 行内汉字 < 此数不校验 (过短 ASR 不可靠, 误报率高)
_MIN_HAN_CHARS = 4
# 疑似错读字占比 > 此值视为 ASR 整段噪声, 放弃该行 (不可信)
_MAX_FLAG_RATIO = 0.30
# 单次合成最多自动修复行数 (防 ASR 噪声风暴拖垮产线)
_MAX_FIXES = 6
# 0913 TTS2 吃字率升高: 静态 6 一轮就烧光 (job 628cef1b 10行 fix_budget 饿死实锤);
# 跟 bleed 同律改动态 — max(12, 行数//8)。取用处在 verify_pronunciation 内重算。
_DYNAMIC_FIX_FLOOR = 12
# 结构助词/轻声多读字 (的地得着了) — ASR/TTS 两读常态摇摆, 且拼音标注无从判定
# 哪个才是本句正确读音 (需深层语境, pypinyin 也只给词典默认如 地=di4)。
# 自动修反而会把正确的轻声改错 → 一律豁免不报 (漏检 恰如"目的dì"认了)。
# 儿/语气词 (吗呢吧啊呀…): 儿化音 whisper 常吞"儿"、语气词常同音变换, 语义无重
# 量, 豁免降噪。
_PARTICLE_CHARS = {"的", "地", "得", "着", "了", "儿",
                   "吗", "呢", "吧", "啊", "呀", "哦", "嘛", "啦", "嗯"}


_DIGITS_CN = "零一二三四五六七八九"
_UNITS = ["", "十", "百", "千"]
_MARK_SPLIT = re.compile(r"(<\S{1,4}\|[A-Z]+[1-5]>)")
# 1001 修: 停顿标记 -Xs- (拍点层) 不是语音 — 期望文本先剥, 否则 ASR 永远对不上
# (行20/30/31/46/53 "重合成后仍不一致"误报回滚实锤); 与 tts_lib.text._PAUSE_MARK_ANY_RE 同式
_PAUSE_RE = re.compile(r"(?<![A-Za-z0-9])-\d+(?:\.\d+)?s-")
_ARABIC_NUM = re.compile(r"(\d{4}(?![\d.])|\d+(?:\.\d+)?)")  # 4位整数优先单捕(年份)

# 0913 语速地板 (用户令, 晚间定谳): 5.3字/s **只在首分钟窗口生效** — 前20秒快节奏
# 是硬标准; 正文自然说书方差 (拖腔/气口 4.3-5.2字/s) 是特性不是病, 全篇强压地板
# 实测"气口没了说的硬"已弃。窗口=三区末边界 (150字≈前25s)。度量=去空白字符数
# (含标点)/wav实测时长, 与 0913 全篇排查口径一致。
SPEED_FLOOR = 5.3
SPEED_FLOOR_WINDOW_CHARS = 150
# 语速自动⏩预算 (窗口内最多自动加速重合成的行数; 超出留人工)
_SLOW_AUTO_BUDGET = 12


def _int_to_cn(num: int) -> str:
    """0~99999999 整数 → 中文读法 (值形式): 10→十, 500→五百, 470000→四十七万。"""
    if num == 0:
        return "零"
    groups: list[str] = []
    for scale, unit in (("亿", 100000000), ("万", 10000)):
        if num >= unit:
            g, num = divmod(num, unit)
            groups.append(_int_to_cn(g) + scale)
    if num:
        out: list[str] = []
        zero_pending = False
        for i, ch in enumerate(str(num).zfill(4)):
            d = int(ch)
            pos = 3 - i
            if d == 0:
                zero_pending = True
                continue
            if zero_pending and out:
                out.append("零")
            zero_pending = False
            if d == 1 and pos == 1 and not out:
                out.append("十")  # 10~19 → 十X
            else:
                out.append(_DIGITS_CN[d] + _UNITS[pos])
        groups.append("".join(out))
    return "".join(groups)


def _num_to_reading(tok: str) -> str:
    """阿拉伯数字串 → 口播读法: 1900-2099 四位数按年读 (2024→二零二四, 口播稿年份
    全为逐位读法); 其余按值 (500→五百, 3.5→三点五, 0.045→零点零四五)。"""
    if len(tok) == 4 and tok.isdigit() and 1900 <= int(tok) <= 2099:
        return "".join(_DIGITS_CN[int(c)] for c in tok)
    if "." in tok:
        head, _, tail = tok.partition(".")
        head_cn = _int_to_cn(int(head)) if head else ""
        return head_cn + "点" + "".join(_DIGITS_CN[int(c)] for c in tail)
    return _int_to_cn(int(tok))


_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def _normalize_asr(text: str) -> str:
    """ASR 输出规整: 阿拉伯数字 → 中文读法 (whisper 实测按数值归一: 十块钱→"10块钱",
    五百万→"500万", 全库回听 2026-09-03)。拼音标注段 <字|PINYIN1> 原样保留 —
    其中的数字是声调, 展开会毁标注。
    0913 补: "90%"→"百分之九十" — whisper 把百分之X写成百分号 (job 628cef1b
    行3/22/61 三行假吃字实锤, 财经稿满地百分比必复发)。"""
    def _pct(m: re.Match) -> str:
        return "百分之" + _num_to_reading(m.group(1))
    parts = _MARK_SPLIT.split(_PCT_RE.sub(_pct, text))
    return "".join(
        p if p.startswith("<") else _ARABIC_NUM.sub(lambda m: _num_to_reading(m.group(0)), p)
        for p in parts
    )


_INITIALS = ("zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g",
             "k", "h", "j", "q", "x", "r", "z", "c", "s", "y", "w")
# 声母混淆对 (全库回听 2026-09-03 上下文抽查全为 whisper 听岔): 达→塔(d/t)、
# 灌→欢(g/h)、就→有(j/y)、期→记(j/q)。韵母相同 + 声母属同组 → 容差。
_INITIAL_CONFUSION = tuple(map(frozenset, ("dt", "gh", "jy", "jq")))


def _init_final(syl: str) -> tuple[str, str]:
    for ini in _INITIALS:
        if syl.startswith(ini):
            return ini, syl[len(ini):]
    return "", syl


def _syl_similar(x: str, y: str) -> bool:
    """音节相似判定 — ASR 系统性混淆容差:
    ① 前后鼻音 (jin/jing); ② 卷舌/平舌 (zhai/zai, chun/cun, shan/san);
    ③ 同韵母 + 声母混淆对 (da/ta, guan/huan, jiu/you — iu/ou 视为同韵)。
    全库回听实测 (2026-09-03, 2948 段): 债→zai×10/纯→cun×5/就→you×10/
    达→ta×7(全是英伟达)/灌→huan×4/期→ji×3 全是 whisper 听岔, 非 TTS 错读 —
    不容差会把重合成预算烧在伪影上。"""
    if x == y:
        return True
    nx = x[:-1] if x.endswith("ng") else x
    ny = y[:-1] if y.endswith("ng") else y
    if nx == ny and x.endswith(("n", "ng")) and y.endswith(("n", "ng")):
        return True
    rx = nx.replace("zh", "z").replace("ch", "c").replace("sh", "s")
    ry = ny.replace("zh", "z").replace("ch", "c").replace("sh", "s")
    if rx == ry:
        return True
    ix, fx = _init_final(rx)
    iy, fy = _init_final(ry)
    if fx == fy or {fx, fy} == {"iu", "ou"}:
        if ix and iy and any({ix, iy} == g for g in _INITIAL_CONFUSION):
            return True
    return False

_Transcriber = Callable[[Path], str]
_Event = Callable[[str, str], None]
# (行号, 干净行文本, 标注后行文本) -> 合成成功 (wav 原位覆盖) — 失败抛异常由调用方回滚
_Resynth = Callable[..., None]  # (idx, 干净行, 标注行, *, duration_factor=None)


def _is_han(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _pinyin_pairs(text: str) -> list[tuple[str, str, str]]:
    """文本 → [(汉字, 无声调拼音, 带调拼音hang2)] — pypinyin 词组感知 (银行→hang)。

    非汉字 (标点/数字/字母/拼音标注标记) 全部过滤, 与 ASR 输出的标点差异天然免疫。
    拼音数与汉字数对不齐 (罕见) 返回 [] — fail-safe 不比对。
    """
    from pypinyin import Style, pinyin

    text = _normalize_asr(text)
    han = [c for c in text if _is_han(c)]
    if not han:
        return []
    try:
        norm = [x[0] for x in pinyin(text, style=Style.NORMAL, errors="ignore") if x]
        t3 = [
            x[0] for x in
            pinyin(text, style=Style.TONE3, neutral_tone_with_five=True, errors="ignore")
            if x
        ]
    except Exception:
        return []
    if len(norm) != len(han) or len(t3) != len(han):
        return []
    return list(zip(han, norm, t3))


def _bleed_hit(probe: str, heard_han: str) -> bool:
    """bleed 子串容差匹配: 精确命中, 或逐字无声调拼音滑窗相似命中。

    0913 实锤 (job 39ee68a8 行17/18): 稿「头一档」whisper 听写「投一档」— 一个
    同音字让精确子串匹配整条失明, 正向 bleed 漏检 → 成片"说两遍"靠人耳才发现。
    容差复用错音层的 _syl_similar (声母混淆组+韵母同), 不放宽到全同音 (防误报)。"""
    if not probe:
        return False
    if probe in heard_han:
        return True
    pp = _pinyin_pairs(probe)
    hh = _pinyin_pairs(heard_han)
    n = len(pp)
    # 模糊路径仅 ≥3 字探针 (生产判据恒 3 字), 且为**纯同音匹配** (无声调拼音逐字相等,
    # 如 头/投 复/付 代/带 — whisper 听写同音换字)。不用 _syl_similar 的 n/ng 等混淆
    # 容差: bleed=音频里真念了下句首, ASR 只会换同音字; 混淆容差会把 撞墙/赚钱 类
    # 邻韵词误判成串句 (0913 回归实锤)。
    if n < 3 or len(hh) < n:
        return False
    probe_syl = [p[1] for p in pp]
    for i in range(len(hh) - n + 1):
        if all(hh[i + k][1] == probe_syl[k] for k in range(n)):
            return True
    return False


def _diff_readings(script: str, heard: str) -> list[dict[str, Any]]:
    """拼音级对比 → 疑似错读清单 [{char, expect, heard, idx, t3}]。

    同音字替换 (的地得) 无声调拼音相同 → 不报; 声/韵母不同才报。
    ASR 吞字 (delete 区间) 也报 (heard="") — 重合成常能修复, 复检失败会回滚。
    ASR 多听出的字 (insert) 忽略 — 稿件侧无对应字, 无从标注。
    """
    a = _pinyin_pairs(script)
    b = _pinyin_pairs(heard)
    if not a or not b:
        return []
    sa = [s for _, s, _ in a]
    sb = [s for _, s, _ in b]
    sm = difflib.SequenceMatcher(None, sa, sb, autojunk=False)
    out: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            heard_region = sb[j1:j2]
            # 两/二 同值异读 (两百 vs whisper 按值写的 200→二百): 非错读 (×31 全库实测)
            if a[i1][0] == "两" and set(heard_region) <= {"er"}:
                continue
            for si in range(i1, i2):
                if a[si][0] in _PARTICLE_CHARS:
                    continue
                if not any(_syl_similar(a[si][1], h) for h in heard_region):
                    out.append({"char": a[si][0], "expect": a[si][1],
                                "heard": "/".join(heard_region), "idx": si, "t3": a[si][2]})
        elif tag == "delete":
            for si in range(i1, i2):
                if a[si][0] in _PARTICLE_CHARS:
                    continue
                out.append({"char": a[si][0], "expect": a[si][1],
                            "heard": "", "idx": si, "t3": a[si][2]})
    return out


@functools.lru_cache(maxsize=512)
def _valid_readings(ch: str) -> frozenset[str]:
    """单字全部合法读音 (heteronym)。缓存: 高频字反复查。"""
    from pypinyin import Style, pinyin

    try:
        return frozenset(
            x for grp in pinyin(ch, style=Style.NORMAL, heteronym=True, errors="ignore")
            for x in grp
        )
    except Exception:
        return frozenset()


def _polyphone_ambiguous(diffs: list[dict[str, Any]]) -> bool:
    """听到的音是错读字的**另一合法读音** → 多音字歧义, 禁自动修。

    全库回听实测 (2026-09-03): pypinyin 语境猜错「长文档/长上下文/长鑫」期望
    zhang (实读 chang 正确) — 若强标 <长|ZHANG3> 重合成, 会把正确音频改坏。
    铟类错读 (听成 ling/yun, 非法读音) 不受影响照常自动修; 宁可不修不可修错。"""
    for d in diffs:
        if not d["heard"]:
            continue
        valid = _valid_readings(d["char"])
        if any(h != d["expect"] and h in valid for h in d["heard"].split("/")):
            return True
    return False


def _apply_markup(line: str, flagged: dict[int, str]) -> str:
    """按汉字序号给行文本加 ``<字|PINYIN>`` 标注 (idx → 带调拼音 t3, 如 hang2)。"""
    out: list[str] = []
    hi = -1
    for ch in line:
        if _is_han(ch):
            hi += 1
            t3 = flagged.get(hi)
            if t3:
                letters = "".join(c for c in t3 if c.isalpha())
                digit = "".join(c for c in t3 if c.isdigit()) or "5"
                out.append(f"<{ch}|{letters.upper()}{digit}>")
                continue
        out.append(ch)
    return "".join(out)


def _add_nvidia_dll_dirs() -> None:
    """pip nvidia 轮子 (cublas/cudnn…) 的 DLL 不在系统 PATH — 应用内靠 torch
    import 副作用注册; 独立进程先加载 whisper 会找不到 cuBLAS。

    ctranslate2 用传统 LoadLibraryW (不吃 os.add_dll_directory), 必须把
    nvidia/*/bin 前置进 PATH (putenv 同步进程环境, 对后续 LoadLibrary 生效)。"""
    import os
    import site

    dirs: list[str] = []
    for sp in site.getsitepackages():
        nv = Path(sp) / "nvidia"
        if nv.is_dir():
            dirs.extend(str(b) for b in nv.glob("*/bin"))
    if not dirs:
        return
    path = os.environ.get("PATH", "")
    new_dirs = [d for d in dirs if d not in path.split(os.pathsep)]
    if new_dirs:
        os.environ["PATH"] = os.pathsep.join(new_dirs) + os.pathsep + path


def _whisper_transcribe(wav: Path) -> str:
    """单行 wav → 文本 (faster-whisper large-v3, 进程级单例复用, CUDA)。

    initial_prompt (0912): 诱导逐字转写保留语气词 — whisper 默认吞 嗯/呃,
    会令语气词污染检失聪; 逐字口径对错音比对同样更准 (只会更忠实, 无副作用)。
    """
    _add_nvidia_dll_dirs()
    from app.services.alignment_service._models import _ensure_model

    model = _ensure_model()
    segments, _info = model.transcribe(
        str(wav), language="zh", word_timestamps=False, vad_filter=False,
        initial_prompt="以下是逐字转写,一字不漏,保留全部语气词:嗯、呃。",
    )
    return "".join(getattr(s, "text", "") for s in segments)


# 语气词污染字符 (0912 用户令: 稿里有的豁免, 稿里没有的=污染)
_FILLER_CHARS = ("嗯", "呃")
# 插字检测 (0912 行24 实锤: 稿中无"第一"语音念"第一" — 行尾破折号续写幻觉等)
# 稿里没有的多字连接词出现在 ASR = 插字嫌疑 → 重roll
_INSERT_WORDS = ("第一", "第二", "第三", "然后", "接着", "那么", "其次")


def verify_pronunciation(
    *,
    output_dir: Path,
    manifest: dict[str, Any],
    resynth_line: _Resynth,
    on_event: _Event | None = None,
    transcriber: _Transcriber | None = None,
    max_fixes: int = 0,
    speed_zones: list[dict] | None = None,
    global_df: float = 1.00,
) -> dict[str, Any]:
    """逐行回听校验 + 错音自动修复。

    Args:
        output_dir: manifest 所在目录 (逐行 wav {idx:03d}.wav 同目录)。
        manifest: synthesize_lines 产出的 manifest (segments[].text/file/index)。
        resynth_line: (idx, 干净行, 标注行) → 原位覆盖该行 wav; 失败抛异常。
        on_event: (消息, 级别 info/ok/warn) 产线事件流上报。
        transcriber: 注入式转录器 (单测用), 缺省 whisper。

    Returns:
        {checked, suspect_lines, fixed, unresolved} — fixed/unresolved 每条含
        {index, file, text, diffs, duration}(fixed 才有 duration)。
    """
    def _emit(msg: str, level: str = "info") -> None:
        if on_event:
            try:
                on_event(msg, level)
            except Exception:
                pass

    _asr = transcriber or _whisper_transcribe
    # 0913 动态修复预算: max(12, 行数//8) — TTS2 吃字率高, 静态 6 一轮饿死
    if max_fixes <= 0:
        max_fixes = max(_DYNAMIC_FIX_FLOOR, (len(manifest.get("segments") or []) or 8) // 8)
    report: dict[str, Any] = {
        "checked": 0, "suspect_lines": [], "fixed": [], "unresolved": [],
    }
    _heard_cache: dict[int, str] = {}  # idx → ASR 文本 (bleed 全量扫描复用)
    segments = manifest.get("segments") or []
    if not segments:
        return report
    _emit(f"ASR 回听校验 {len(segments)} 段…")
    attempts = 0  # 修复尝试计次 (成功失败都算 — 防失败风暴突破预算)

    for seg in segments:
        idx = int(seg.get("index", -1))
        text = _PAUSE_RE.sub("", str(seg.get("text") or ""))
        wav = output_dir / str(seg.get("file") or "")
        if idx < 0 or not wav.exists():
            continue
        han_count = sum(1 for c in text if _is_han(c))
        if han_count < _MIN_HAN_CHARS:
            continue  # 行过短, ASR 不可靠
        report["checked"] += 1

        try:
            heard = _asr(wav)
        except Exception as exc:
            # 模型缺失/显存不足 → 首败即中止 (否则逐行重复告警静默空转)
            logger.warning("[tts_verify] 转录不可用, 中止校验: %s", exc)
            _emit(f"ASR 回听不可用, 本次跳过校验: {exc}", "warn")
            break
        if not heard or not _pinyin_pairs(heard):
            continue
        _heard_cache[idx] = heard  # bleed 扫描复用 (不再重复转录)

        # ── 语气词污染检 (0912 用户令: 对齐稿件 — 稿里有的豁免, 没有的是污染 → 重roll) ──
        _fillers = [ch for ch in _FILLER_CHARS if ch in heard and ch not in text]
        _inserts = [w for w in _INSERT_WORDS if w in heard and w not in text]
        if _fillers or _inserts:
            entry_f = {"index": idx, "file": wav.name, "text": text[:40],
                       "diffs": [{"char": ch, "expect": "无(稿中无此语气词)",
                                  "heard": ch} for ch in _fillers]
                                 + [{"char": w, "expect": "无(稿中无此词)",
                                     "heard": w} for w in _inserts]}
            if attempts >= max_fixes:
                report["unresolved"].append({**entry_f, "reason": "filler_budget"})
                _emit(f"行{idx} 语气词污染「{''.join(_fillers)}」预算尽 — 请人工听", "warn")
            else:
                attempts += 1
                _emit(f"行{idx} 语气词污染「{''.join(_fillers)}」(稿中无) — 重合成", "warn")
                bak = wav.with_suffix(".wav.bak")
                try:
                    shutil.copy2(wav, bak)
                    resynth_line(idx, text, text)  # 干净稿重roll (采样重roll大概率丢弃语气词)
                    heard2 = _asr(wav)
                    if heard2 and not any((ch in heard2 and ch not in text) for ch in _FILLER_CHARS) \
                            and not any((w in heard2 and w not in text) for w in _INSERT_WORDS):
                        import soundfile as sf
                        try:
                            dur = round(sf.info(str(wav)).duration, 3)
                        except Exception:
                            dur = None
                        report["fixed"].append({**entry_f, "reason": "filler", "duration": dur})
                        _heard_cache[idx] = heard2
                        _emit(f"行{idx} 语气词已清除 (重roll)", "ok")
                        heard = heard2  # 用干净版继续走下方发音校验
                    else:
                        raise RuntimeError("重roll后语气词仍在")
                except Exception as exc:
                    logger.warning("[tts_verify] 行%s 语气词修复失败回滚: %s", idx, exc)
                    try:
                        shutil.copy2(bak, wav)
                        bak.unlink(missing_ok=True)
                    except Exception:
                        pass
                    report["unresolved"].append({**entry_f, "reason": "filler"})
                    _emit(f"行{idx} 语气词两roll未除 — 请人工听 {wav.name}", "warn")

        diffs = _diff_readings(text, heard)
        if not diffs:
            continue

        entry = {"index": idx, "file": wav.name, "text": text[:40],
                 "diffs": [{"char": d["char"], "expect": d["expect"],
                            "heard": d["heard"]} for d in diffs[:4]]}
        report["suspect_lines"].append(entry)
        if len(diffs) > max(1, int(han_count * _MAX_FLAG_RATIO)):
            # 整段大面积对不上 = ASR 噪声 (口音/背景音), 不可信不修
            report["unresolved"].append({**entry, "reason": "asr_noise"})
            continue
        if _polyphone_ambiguous(diffs):
            # 听到的是另一合法读音 → pypinyin 语境期望可能本身错, 强标会把对的改错
            report["unresolved"].append({**entry, "reason": "polyphone_ambiguous"})
            _emit(f"行{idx} 多音字歧义「{diffs[0]['char']}」期望 {diffs[0]['expect']} "
                  f"听到 {diffs[0]['heard']} — 不自动修, 请人工听", "warn")
            continue
        if attempts >= max_fixes:
            report["unresolved"].append({**entry, "reason": "fix_budget"})
            continue
        attempts += 1

        _emit(f"疑似错读 行{idx}「{diffs[0]['char']}」期望 {diffs[0]['expect']} "
              f"听到 {diffs[0]['heard'] or '(吞字)'} — 拼音标注重合成", "warn")
        bak = wav.with_suffix(".wav.bak")
        try:
            shutil.copy2(wav, bak)
            marked = _apply_markup(text, {d["idx"]: d["t3"] for d in diffs})
            resynth_line(idx, text, marked)
            # 复检: 标注字符在拼音对里天然过滤, 与干净文本同口径
            # 0912 补: 发音修复的 reroll 也可能带入语气词 (028 实锤: 亲字修复带进嗯)
            # → 复检同时查语气词, 带入即视为修复失败回滚
            heard2 = _asr(wav)
            _new_fillers = [ch for ch in _FILLER_CHARS if ch in heard2 and ch not in text]
            if heard2 and not _diff_readings(marked, heard2) and not _new_fillers:
                import soundfile as sf
                try:
                    dur = round(sf.info(str(wav)).duration, 3)
                except Exception:
                    dur = None
                report["fixed"].append({**entry, "duration": dur})
                _emit(f"行{idx} 错音已修复 ({len(diffs)} 字)", "ok")
            else:
                raise RuntimeError("重合成后仍不一致")
        except Exception as exc:
            # 宁可不修不可修错: 回滚原音频, 留给人工听
            logger.warning("[tts_verify] 行%s 修复失败回滚: %s", idx, exc)
            try:
                if bak.exists():
                    shutil.copy2(bak, wav)
            except Exception:
                logger.error("[tts_verify] 行%s 回滚失败, 原音频在 %s", idx, bak)
            report["unresolved"].append({**entry, "reason": f"resynth_failed:{exc}"})
            _emit(f"行{idx} 错音自动修复失败, 请人工听 ({diffs[0]['char']})", "warn")
        finally:
            bak.unlink(missing_ok=True)

    # ── bleed 双向独立全量扫描 (2026-09-07 三次实锤改版) ──
    # 批合成 IndexTTS 串句是双向的: ① 上句尾串下句首 (0905 已修); ② 本行头部
    # 带上句尾 (job 9193c63b 实锤: 135 行念了 134 尾 + 135 全文)。两向都致成片
    # "说两遍"。bleed 与错音分开预算: 旧实现共享 _MAX_FIXES=6, bleed 爆发时
    # 预算瞬烧光、其余行静默带病放行 (同 job 158 段染 20 行, 仅 6 行获修机会,
    # 成片"多次重复"的直接根因)。受染行分布与批首/批尾无关 (批内随机, 生成端
    # 重复), 逐行检测是产线侧唯一可靠抓手。
    _by_index = {int(s.get("index", -1)): s for s in segments}

    def _han_only(s: str) -> str:
        return "".join(c for c in (s or "") if _is_han(c))

    bleed_budget = max(16, len(segments) // 4)
    bleed_attempts = 0

    def _fix_bleed_line(idx: int, label: str, verify) -> None:
        """重合成受染行 + 复检 + 回写 ASR 缓存 (正反向共用)。verify(heard2)->bool。"""
        nonlocal bleed_attempts
        seg = _by_index.get(idx)
        if not seg:
            return
        wav = output_dir / str(seg.get("file") or "")
        text = _PAUSE_RE.sub("", str(seg.get("text") or ""))
        if not wav.exists():
            return
        bleed_attempts += 1
        bak = wav.with_suffix(".wav.bak")
        try:
            shutil.copy2(wav, bak)
            resynth_line(idx, text, text)
            heard2 = _asr(wav)
            if heard2 and verify(heard2):
                import soundfile as sf
                try:
                    dur = round(sf.info(str(wav)).duration, 3)
                except Exception:
                    dur = None
                report["fixed"].append({
                    "index": idx, "file": wav.name, "text": text[:40],
                    "diffs": [{"char": label[0], "expect": "", "heard": label}],
                    "duration": dur,
                })
                _heard_cache[idx] = heard2  # 后续相邻行判定用修复后文本
                _emit(f"行{idx} bleed 已修复 ({label})", "ok")
            else:
                raise RuntimeError("bleed 重合成后仍异常")
        except Exception as exc:
            # 宁可不修不可修错: 回滚原音频, 留给人工听
            logger.warning("[tts_verify] 行%s bleed 修复失败回滚: %s", idx, exc)
            try:
                if bak.exists():
                    shutil.copy2(bak, wav)
            except Exception:
                logger.error("[tts_verify] 行%s 回滚失败, 原音频在 %s", idx, bak)
            report["unresolved"].append({
                "index": idx, "file": wav.name, "text": text[:40],
                "diffs": [{"char": label[0], "expect": "", "heard": label}],
                "reason": "bleed_resynth_failed",
            })
        finally:
            bak.unlink(missing_ok=True)

    def _bleed_entry(idx: int, label: str) -> dict[str, Any]:
        seg = _by_index.get(idx) or {}
        return {"index": idx, "file": str(seg.get("file") or ""),
                "text": str(seg.get("text") or "")[:40],
                "diffs": [{"char": label[0], "expect": "", "heard": label}]}

    for seg in segments:
        idx = int(seg.get("index", -1))
        cur = _by_index.get(idx)
        prev = _by_index.get(idx - 1)
        if not cur:
            continue
        cur_text = str(cur.get("text") or "")
        cur_han = _han_only(cur_text)

        # ── 正向: 上行 (idx-1) 尾部串入本句句首 → 修 idx-1 ──
        if prev and (idx - 1) in _heard_cache:
            prev_text = str(prev.get("text") or "")
            prev_han = _han_only(prev_text)
            if sum(1 for c in prev_text if _is_han(c)) >= _MIN_HAN_CHARS:
                # 下句句首取 3~5 汉字窗口 (太短误报, 太长 ASR 转写漂移匹配不上)
                nxt_head = cur_han[:5]
                hp = _han_only(_heard_cache[idx - 1])
                # bleed 判据: 上行听到了下句句首, 且该片段不在上行自己文本里
                # (全文任意位置查 — 口癖/排比重复不算 bleed, 宁可漏检不误重合成)
                # 0913: 匹配改 _bleed_hit 容差 (同音漂移防漏检, 见其 docstring)
                if (len(nxt_head) >= 3 and _bleed_hit(nxt_head[:3], hp)
                        and nxt_head[:3] not in prev_han):
                    if bleed_attempts >= bleed_budget:
                        report["unresolved"].append(
                            {**_bleed_entry(idx - 1, "串句bleed"),
                             "reason": f"bleed_budget:{bleed_budget}"})
                        _emit(f"行{idx - 1} bleed 超修复预算未修, 请人工听", "warn")
                        continue
                    _emit(f"行{idx - 1} 尾部串入下句开头「{nxt_head[:3]}…」(批切分 bleed) — 重合成去重", "warn")
                    _fix_bleed_line(
                        idx - 1, "串句bleed",
                        lambda h2, nh=nxt_head[:3], pt=prev_text:
                            nh not in _han_only(h2) and not _diff_readings(pt, h2))

        # ── 反向: 本行 (idx) 头部带上句尾 → 修 idx (2026-09-07 新增) ──
        if prev and idx in _heard_cache:
            prev_text = str(prev.get("text") or "")
            prev_han = _han_only(prev_text)
            hc = _han_only(_heard_cache[idx])
            if sum(1 for c in prev_text if _is_han(c)) >= _MIN_HAN_CHARS:
                prev_tail = prev_han[-5:]
                if (len(prev_tail) >= 3 and _bleed_hit(prev_tail[-3:], hc)
                        and prev_tail[-3:] not in cur_han):
                    if bleed_attempts >= bleed_budget:
                        report["unresolved"].append(
                            {**_bleed_entry(idx, "头串bleed"),
                             "reason": f"bleed_budget:{bleed_budget}"})
                        _emit(f"行{idx} bleed 超修复预算未修, 请人工听", "warn")
                        continue
                    _emit(f"行{idx} 头部带上句结尾「{prev_tail[-3:]}…」(批切分 bleed) — 重合成去重", "warn")
                    _fix_bleed_line(
                        idx, "头串bleed",
                        lambda h2, pt=prev_tail[-3:], ct=cur_text:
                            pt not in _han_only(h2) and not _diff_readings(ct, h2))

    # ── 0913 语速地板: 首分钟窗口确定性体检 + 自动⏩加速闭环 ──
    # 只查窗口内 (前 SPEED_FLOOR_WINDOW_CHARS 字) — 正文自然方差不进地板。
    # 慢行不再人工挨个 ⏩: 在本行落区 df 基础上按实测速率折算目标 df (目标 5.6 留余量,
    # 下限 0.5), 重合成复测; 两次仍不过留 slow_lines 给人工。预算 _SLOW_AUTO_BUDGET
    # 防拖腔风暴烧 GPU。修复行入 fixed[] (duration 随既有回写链同步 manifest/DB)。
    import wave as _wave_mod
    try:
        from scripts.tts_lib.lines import zone_duration_factor as _zdf
    except Exception:
        _zdf = None
    report["slow_lines"] = []
    _off = 0
    _auto_left = _SLOW_AUTO_BUDGET
    for seg in segments:
        wav = output_dir / str(seg.get("file") or "")
        idx = int(seg.get("index", -1))
        text = str(seg.get("text") or "")
        n_chars = len(re.sub(r"\s", "", text))
        dur = 0.0
        if idx >= 0 and wav.exists() and n_chars >= 4:
            try:
                with _wave_mod.open(str(wav), "rb") as w:
                    dur = w.getnframes() / w.getframerate()
            except Exception:
                dur = 0.0
        if dur > 0.3 and _off < SPEED_FLOOR_WINDOW_CHARS and n_chars / dur < SPEED_FLOOR:
            _slow_entry = {"index": idx, "file": seg.get("file"), "text": text[:20],
                           "rate": round(n_chars / dur, 2), "duration": round(dur, 2)}
            _df_used = (global_df if _zdf is None or not speed_zones
                        else _zdf(speed_zones, _off, global_df))
            _fixed = False
            _att = 0
            # 自动⏩仅在引擎支持语速控制 (duration_factor) 时可行 — speed_zones 缺省
            # (=backend 非 2.5, TTS2 忽略 df) 时只上报不烧 GPU
            while speed_zones and _auto_left > 0 and _att < 2:
                _att += 1
                _auto_left -= 1
                _df_new = max(round(_df_used * (n_chars / dur) / 5.6, 3), 0.5)
                _emit(f"行{idx} 语速{_slow_entry['rate']}字/s <{SPEED_FLOOR} — 自动⏩ df={_df_new}", "warn")
                try:
                    resynth_line(idx, text, text, duration_factor=_df_new)
                except TypeError:
                    # 回调不支持 df 覆盖 (旧签名) — 退回纯上报
                    _auto_left = 0
                    break
                except Exception as exc:
                    logger.warning("[tts_verify] 行%s 自动加速失败: %s", idx, exc)
                    break
                try:
                    with _wave_mod.open(str(wav), "rb") as w:
                        dur = w.getnframes() / w.getframerate()
                except Exception:
                    break
                if dur > 0.3 and n_chars / dur >= SPEED_FLOOR:
                    report["fixed"].append({**_slow_entry, "duration": round(dur, 3),
                                            "reason": "slow", "diffs": []})
                    _emit(f"行{idx} 自动⏩过地板 ({round(n_chars / dur, 2)}字/s)", "ok")
                    _fixed = True
                    break
                _df_used = _df_new  # 仍慢 → 在新 df 上再折一档
            if not _fixed:
                report["slow_lines"].append(_slow_entry)
        _off += len(text)
    if report["slow_lines"]:
        _emit(f"语速地板 <{SPEED_FLOOR}字/s: {len(report['slow_lines'])} 行自动加速未过, 请 ⏩/人工",
              "warn")

    n_fix, n_bad = len(report["fixed"]), len(report["unresolved"])
    if n_fix or n_bad:
        _emit(f"ASR 回听完成: {report['checked']} 段, 修复 {n_fix}, 待人工 {n_bad}",
              "warn" if n_bad else "ok")
    else:
        _emit(f"ASR 回听完成: {report['checked']} 段读音全部吻合", "ok")
    return report
