"""逐行合成: 批量 TTS + 静音切分回逐行 WAV + manifest 输出.

- synthesize_lines(): 每行一个 WAV, 按 ~300 字合并为 batch 一次 TTS, 再按
  静音切分回逐行文件, 写 manifest.json。
- 断点续传: 已存在非空 *_wav 跳过整批; 失败保留已有 WAV + 部分 manifest,
  仅清理进行中的 _batch_*.wav。

行为与旧 tts_client.py 逐字一致 (连接符/命名/manifest/异常消息全不变)。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf

from .audio import (
    _concat_wavs_with_pauses,
    _ensure_dir,
    _split_wav_by_silence,
    _write_wav,
)
from .constants import DEFAULT_F5_URL, DEFAULT_FISH_URL, DEFAULT_INDEXTTS_URL
from .orchestrator import Backend, _synthesize_single
from .text import (
    _group_lines_semantic,
    _merge_lines_for_batch,
    _sanitize_for_fish,
    _split_by_pause_marks,
    _tts_text,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str, dict[str, Any] | None], None]

__all__ = ["synthesize_lines", "zone_duration_factor"]


def zone_duration_factor(
    speed_zones: list[dict] | None, offset: int, global_df: float = 1.16,
) -> float:
    """三区斜坡 (0913): 按字符偏移插值 duration_factor — 区内线性过渡到下一区值。

    锚点 (0, df1) → (upto1, df2) → (upto2, df3=global) → 区外恒 global。
    边界连续无缝 (旧台阶实现每边界跳 0.10-0.11, 人耳可辨 "突然慢一档")。
    reroll (audio.py) 与批合成共用本函数保证同参。
    global 缺省 1.16 (0913 晚回摆 — 1.00+emo0.6 实测气口全无语速7+已弃, 1.16+0.8=自然说书速)。
    """
    if not speed_zones:
        return global_df
    zs = sorted(speed_zones, key=lambda z: float(z.get("upto_chars", 0)))
    if offset >= float(zs[-1].get("upto_chars", 0)):
        return global_df
    anchors: list[tuple[float, float]] = [(0.0, float(zs[0].get("duration_factor", global_df)))]
    for i, z in enumerate(zs):
        nxt = float(zs[i + 1]["duration_factor"]) if i + 1 < len(zs) else float(global_df)
        anchors.append((float(z.get("upto_chars", 0)), nxt))
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= offset < x1:
            if x1 <= x0:
                return y1
            return y0 + (y1 - y0) * (offset - x0) / (x1 - x0)
    return anchors[-1][1]


@dataclass(frozen=True)
class _SynthesisParams:
    """Immutable bundle of TTS params forwarded to _synthesize_single."""
    backend: Backend
    voice_id: str
    reference_audio: Path | None
    reference_text: str
    base_url_fish: str
    base_url_f5: str
    base_url_indextts: str
    master_audio: Path | None
    master_text: str
    master_style: str
    params: dict[str, Any] | None


# 互动尾句气口 (1005 用户令·方案A: 站队钩/评论区号召句后必停 0.5s):
# "你站哪边？评论区说说。" 句号结尾不进设问停顿判定 (只认行尾?), 合成连读实锤,
# 用户此前每集手动加 -0.5s- 重roll。合成输入侧确定性垫口 (幂等: 已带标记不重复加;
# 必须吃到句末标点才垫 — "评论区立个flag，填完回来打卡。"式中腰不插)。
_HOOK_TAIL_RE = re.compile(r"(评论区(?:说说|聊聊|立个flag|报个到|打卡)[。！？])(?!-[\d.]+s-)")


def _hook_tail_pause(text: str) -> str:
    return _HOOK_TAIL_RE.sub(lambda m: m.group(1) + "-0.5s-", text or "")


def synthesize_lines(
    text: str,
    output_dir: Path,
    backend: Backend = "auto",
    voice_id: str = "default",
    reference_audio: Path | None = None,
    reference_text: str = "",
    base_url_fish: str = DEFAULT_FISH_URL,
    base_url_f5: str = DEFAULT_F5_URL,
    base_url_indextts: str = DEFAULT_INDEXTTS_URL,
    master_audio: Path | None = None,
    master_text: str = "",
    master_style: str = "calm",
    progress_callback: ProgressCallback | None = None,
    params: dict[str, Any] | None = None,
    batch_max_chars: int = 300,
    emotion_segments: list[dict[str, Any]] | None = None,
    hook_duration_factor: float | None = None,
    hook_chars: int = 60,
    speed_zones: list[dict] | None = None,
    synth_unit: str = "line",
    # speed_zones (0930 隔离注记): 三区斜坡 df 是 2.5 引擎专属合成期参数,
    # TTS2 上哑参数 (0921 定稿) — 语速控制在 audio_post (atempo 后置栈), 勿在此扩展。
    # 气口档位 (1010 用户四层定标): 正常接缝 0.3 (地板) / 段落 0.7 / 问句 0.8 /
    # 模块界 (钩子/解读大段落) 1.0 = 绝对上限; -Xs- 标记 max 融入 (废止加法 —
    # ep2 实锤超长静音主凶)。
    question_breath_sec: float = 0.8,
    para_breath_sec: float = 0.7,
    module_walls: set[int] | list[int] | None = None,
    module_breath_sec: float = 1.0,
    zone_breath: dict[int, dict] | None = None,
    para_walls: set[int] | list[int] | None = None,
) -> dict[str, Any]:
    """Generate one WAV per non-empty line; writes manifest.json into output_dir.

    emotion_segments (2026-08-13, P5): 已解析的段落情绪参数
        [{"text": 段文本, "vector": 8维列表, "alpha": float}]. 提供时按段分组合成,
        段内行合并为 batch, 每批带该段情绪参数; 缺省走现状 (整篇 master_style=calm).
    hook_duration_factor (0912): 开场 hook_chars 字提速 (断崖式, 旧参数保留兼容)。
    speed_zones (0912 晚 · 三区渐降, 用户令"提前布局"): [{"upto_chars": 60, "duration_factor": 0.95},
        {"upto_chars": 150, "duration_factor": 1.05}] — 按行首字符偏移落区, 区外用全局;
        首分钟节奏梯度: 锤区快 → 过渡区中 → 正文巡航, 消除语速断崖。
    synth_unit (0913 包化, 用户令根治批切分漂移): "line"=旧行为 (批合成→静音切回行,
        bleed/丢头/串尾的病灶); "chunk"=**一包一次完整解码一个 wav, 永不切分** —
        结构性消灭制造出来的边界。包=句边界聚组, 上限 min(batch_max_chars, 140)
        (模型单段解码 ~30s≈150-160字静默截断悬崖, 留余量); manifest 条目带
        line_indices, wav 以首行行号命名 (续传/排序/拼接全兼容)。
    module_walls (0917 模块总线): 六拍模块末行号集合 (清洗后语音行序列, 0 基) —
        模块边界强制断包 (与情绪墙同机制), 模块尾垫 module_breath_sec 大气口;
        manifest 每包带 module_id (包末行落在第几个模块内, 0 基)。缺省 None =
        无模块结构 (旧稿/裸稿), 行为不变。
    para_walls (1004 用户令: 写稿结构沿用为音频结构): 稿件段落/节标段首行号集 —
        强制断包 (身份句与下节首句焊一包 = ep6 008 实锤) + 段前垫 para_breath_sec
        (合成文本无空行, _paragraph_final_indices 空转 — 段落气口由此接通)。
        缺省 None = 沿用空行检测旧行为。
    """
    text = _hook_tail_pause(text)  # 1005 互动尾句 0.5s 气口 (幂等先行, 全路径生效)
    _ensure_dir(output_dir)
    if synth_unit == "chunk":
        batch_max_chars = min(batch_max_chars, 140)
    syn = _SynthesisParams(
        backend=backend, voice_id=voice_id, reference_audio=reference_audio,
        reference_text=reference_text, base_url_fish=base_url_fish,
        base_url_f5=base_url_f5, base_url_indextts=base_url_indextts,
        master_audio=master_audio, master_text=master_text,
        master_style=master_style, params=params,
    )
    lines = _split_line_indices(text)
    # 0917 模块总线: 模块墙并入断包墙集合 — 语义修正 (0918 实锤): 分组器的墙
    # 语义 = "墙行开新包", 而模块墙 = 模块末行 (应收包) → 传 +1 (下模块首行开新包),
    # 否则模块末句泄进下一模块的包 + 气口判定 miss。气口/bisect 用原末行号。
    _mod_walls: set[int] = {int(w) for w in (module_walls or []) if 0 <= int(w) < len(lines)}
    _mod_starts: set[int] = {w + 1 for w in _mod_walls if w + 1 < len(lines)}
    # 1004 段落墙: 稿件段首 (与模块墙/情绪墙同机制, 细粒度) — 未过界校验的脏值剔除
    _para_wall_set: set[int] = {int(w) for w in (para_walls or []) if 0 < int(w) < len(lines)}
    if synth_unit == "chunk":
        # 0913 深夜 (用户令"包缝必须语义切"): 贪心装包只在句末收包, 超限回退包内
        # 最后一个句末行 — 旧行为边界=字数满即切, 包缝落行边界, 而行≠句 (033 行
        # 以逗号开头实锤), 半句包缝=下包语气重置。情绪墙 (P5 段变化) + 模块墙
        # (六拍边界) + 段落墙 (1004 稿件段首) 保持强制断包。
        if emotion_segments:
            line_emos = _map_lines_to_segments(lines, emotion_segments)
            _walls = frozenset({0} | {i for i in range(1, len(lines))
                                       if line_emos[i] != line_emos[i - 1]} | _mod_starts | _para_wall_set)
            batch_groups = _group_lines_semantic(lines, batch_max_chars, walls=_walls)
            batch_emos = [line_emos[g[0]] for g in batch_groups]
        else:
            _walls = frozenset({0} | _mod_starts | _para_wall_set)
            batch_groups = _group_lines_semantic(lines, batch_max_chars, walls=_walls)
            batch_emos = None
    elif emotion_segments:
        batch_groups, batch_emos = _group_by_emotion_segments(
            lines, emotion_segments, max_chars=batch_max_chars,
        )
    else:
        batch_groups = _merge_lines_for_batch(lines, max_chars=batch_max_chars)
        batch_emos = None
    # 0917 模块总线: manifest 每包标 module_id — 切场/分镜/核验按模块分组消费。
    segment_paths, manifest_segments, _ = _process_batches(
        output_dir, lines, batch_groups, syn, progress_callback, batch_emos=batch_emos,
        hook_df=hook_duration_factor, hook_chars=hook_chars, speed_zones=speed_zones,
        synth_unit=synth_unit,
    )
    if _mod_walls:
        _stamp_module_ids(manifest_segments, _mod_walls)
    # 2026-08-21: 响度归一 (TTS 源 mean≈-39dB 偏轻) → loudnorm, 覆盖所有调用路径
    # (tts_service / 错别字替换 / CLI / e2e). app 不可用(独立 CLI)时跳过.
    try:
        from app.infrastructure.ffmpeg import normalize_audio
    except Exception:
        normalize_audio = None
    if normalize_audio is not None:
        for p in segment_paths:
            try:
                w = Path(p)
                tmp = w.with_suffix(".norm.wav")
                normalize_audio(w, tmp)
                tmp.replace(w)
            except Exception:
                pass  # 单段归一失败不阻断产线
    # 包内死静音守卫 (0919 两连发实锤: 001 包 2.6s/3.98s, IndexTTS 单次解码随机
    # 吐长静音 — 页面回听全绿也拦不住新 take 再抽中)。垫在此刻 = 有意静音
    # (气口引擎尾垫/-Xs- 标记片间垫) 都还没进, 包内所有静音皆是模型产物, 零误伤;
    # 只动包内中段 (头尾静音归归一/尾虚胖审计管)。
    _guarded = _trim_long_inner_pauses(output_dir, manifest_segments)
    if _guarded:
        logger.info("[synth] 死静音守卫: %d 包中段长静音已压到 %.1fs",
                    _guarded, _GUARD_KEEP)
    # 气口引擎 (0914 用户令: 气口=给观众留反应时间): 问句 1.2s (系统停顿 0.7
    # + 再加 0.5) / 大段落边界 1s / -Xs- 行尾标记一律叠加 (歧义句上下句手控);
    # 静音垫在响度归一之后 (不碰静音地板), manifest duration 同步供下游对齐
    if question_breath_sec > 0 or para_breath_sec > 0:
        # 1004 段落墙接通气口: 合成文本无空行 → 空行检测常年空转 (0930 自证);
        # 有段首墙时段末行 = 墙-1, 段前垫 para_breath_sec (与模块气口同栈)
        if _para_wall_set:
            _para_final = {w - 1 for w in _para_wall_set} - {len(lines) - 1}
        else:
            _para_final = _paragraph_final_indices(text)
        # 0918: 末行=文件尾不垫模块气口 (音频结束无尾气口; 且尾部静音会让最后镜
        # 槽虚胖 — 尾静音虚胖问题换马甲重演, 监控 v2 实锤末包垫了 1.5)
        _mod_final = _mod_walls - {len(lines) - 1}
        _apply_breath_pauses(output_dir, lines, _para_final, manifest_segments,
                             question_breath_sec, para_breath_sec,
                             module_final=_mod_final, module_sec=module_breath_sec,
                             zone_breath=zone_breath)
    else:
        # 1010: 档位全关也切头尾 (治理栈①无条件 — 幂等锚不受档位开关控制)
        _apply_breath_pauses(output_dir, None, set(), manifest_segments,
                             0.0, 0.0)
    return _finalize_manifest(output_dir, voice_id, backend, manifest_segments)


# 死静音守卫参数: 中段静音 >1.5s 判病 (本产线健康句间气口 0.35-0.7s, 模块尾
# 气口 1.5s 只在包尾=不在中段), 压到 0.6s
_GUARD_MAX_GAP = 1.5
_GUARD_KEEP = 0.6


def _trim_long_inner_pauses(output_dir: Path,
                            manifest_segments: list[dict[str, Any]]) -> int:
    """包内死静音守卫 (0919 用户令, 两连发 2.6s/3.98s 实锤): 每包 wav 中段静音
    > _GUARD_MAX_GAP → 样本级压到 _GUARD_KEEP (切口 5ms 淡入淡出防咔哒)。

    判定: 50ms 帧 max 振幅 < 全文件 max×2% 为静音帧; 连续静音段须整体落在
    [0.3s, dur-0.3s] 内 (头尾静音不动 — 头=进带, 尾=气口引擎待垫区)。
    失败不阻断产线 (原样交付, 只记日志)。返回修正包数。
    """
    import wave

    fixed = 0
    for seg in manifest_segments:
        f = seg.get("file")
        if not f:
            continue
        wav = output_dir / str(f)
        if not wav.exists():
            continue
        try:
            with wave.open(str(wav), "rb") as w:
                sr, n, ch, sw = (w.getframerate(), w.getnframes(),
                                 w.getnchannels(), w.getsampwidth())
                raw = w.readframes(n)
        except Exception:
            continue
        if n < sr or sw != 2:
            continue
        x = np.frombuffer(raw, dtype=np.int16)
        peak = float(np.abs(x).max()) if x.size else 0.0
        if peak < 100:
            continue
        thr = peak * 0.02
        fr = max(int(sr * 0.05), 1)
        nfr = x.size // fr
        if nfr < 4:
            continue
        quiet = np.abs(x[: nfr * fr].reshape(nfr, fr)).max(axis=1) < thr
        dur = n / sr
        # 连续静音段 → [起帧, 止帧)
        cuts: list[tuple[int, int]] = []
        i = 0
        while i < nfr:
            if quiet[i]:
                j = i
                while j < nfr and quiet[j]:
                    j += 1
                t0, t1 = i * 0.05, j * 0.05
                if t1 - t0 > _GUARD_MAX_GAP and t0 > 0.3 and t1 < dur - 0.3:
                    cuts.append((i, j))
                i = j
            else:
                i += 1
        if not cuts:
            continue
        keep_fr = max(int(_GUARD_KEEP / 0.05), 1)
        fade = max(int(sr * 0.005), 1)
        out = x.astype(np.float64)
        # 从后往前切 (帧索引不受影响)
        for fi, fj in reversed(cuts):
            cut_a = (fi + keep_fr) * fr          # 保留段首 keep 秒
            cut_b = fj * fr                      # 静音段尾
            if cut_b - cut_a < fr:
                continue
            out = np.concatenate([out[:cut_a], out[cut_b:]])
            out[cut_a - fade:cut_a] *= np.linspace(1, 0.3, fade)
            if cut_a + fade < out.size:
                out[cut_a:cut_a + fade] *= np.linspace(0.3, 1, fade)
        try:
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(sw)
                w.setframerate(sr)
                w.writeframes(out.astype(np.int16).tobytes())
            seg["duration"] = round(float(seg.get("duration") or 0.0)
                                    - (x.size - out.size) / sr, 3)
            fixed += 1
            logger.warning("[synth] 死静音守卫: %s 中段 %d 处长静音压到 %.1fs "
                           "(%d→%d帧)", f, len(cuts), _GUARD_KEEP, x.size, out.size)
        except Exception:
            continue  # 写回失败 → 原样交付
    return fixed


def _paragraph_final_indices(text: str) -> set[int]:
    """段落末行号集合 (大段落边界垫 1s): 原文按空行分段, 每段最后一个非空行;
    整篇末行除外 (音频结束不需要尾气口)。"""
    paras = [[l for l in p.splitlines() if l.strip()]
             for p in re.split(r"\n\s*\n", text or "")]
    paras = [p for p in paras if p]
    out: set[int] = set()
    idx = -1
    for pi, para in enumerate(paras):
        idx += len(para)
        if pi < len(paras) - 1:
            out.add(idx)
    return out



def _stamp_module_ids(manifest_segments: list[dict[str, Any]], walls: set[int]) -> None:
    """manifest 每包标 module_id = 包末行落在第几个模块内 (0 基, 墙=模块末行)."""
    import bisect
    sw = sorted(walls)
    for seg in manifest_segments:
        li = seg.get("line_indices") or [seg.get("index", 0)]
        seg["module_id"] = bisect.bisect_left(sw, li[-1])


def _trim_edges(wav: Path, keep: float = 0.03, threshold_db: float = -45.0) -> float | None:
    """切头尾静音各留 keep 秒, 返回处理后时长 (1010 治理栈①).

    幂等前提: apad 垫的纯零静音 (-inf) 必然被任何阈值切掉 → 重跑 N 次结果恒定,
    从机制上消灭"批 resume 再垫一轮"的双垫累积 (ep2 实锤: 008 尾 1.63 ≈ 0.8×2).
    -45dB 只切真静音, 不吃弱语音尾音; 失败返回 None 不动文件."""
    import subprocess as _sp
    tmp = wav.with_suffix(".trim.wav")
    try:
        r = _sp.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af",
             f"silenceremove=start_periods=1:start_threshold={threshold_db}dB:"
             f"start_silence={keep},areverse,"
             f"silenceremove=start_periods=1:start_threshold={threshold_db}dB:"
             f"start_silence={keep},areverse",
             str(tmp)], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            tmp.unlink(missing_ok=True)
            return None
        dur = _wav_dur(tmp)
        if dur <= 0:
            tmp.unlink(missing_ok=True)
            return None
        tmp.replace(wav)
        return dur
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def _wav_dur(p: Path) -> float:
    import wave as _w
    with _w.open(str(p), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 3)


# 气口档位 (1010 用户定标·四层语义): 正常接缝 0.3 / 段落 0.7 / 问句 0.8 /
# 模块界 (钩子/解读1·2·3 大段落) 1.0 = 绝对上限; -Xs- 标记取 max 融入 (废止加法).
_BREATH_SEAM_FLOOR = 0.3


def _apply_breath_pauses(
    output_dir: Path, lines: list[str], para_final: set[int],
    manifest_segments: list[dict[str, Any]],
    question_sec: float, para_sec: float,
    module_final: set[int] | None = None, module_sec: float = 1.0,
    zone_breath: dict[int, dict] | None = None,
) -> None:
    """气口治理栈 (1010 用户定标, 幂等 — 批合成/reroll/重拼三入口同一份规则):

    ① 全包切头尾静音, 各留 0.03 (幂等前提: 上次垫的纯零必然被切掉)
    ② 非末包垫 max(接缝地板 0.3, 问句 0.8, 段落 0.7, 模块 1.0, -Xs- 标记)
       — max 关系, 废止旧版"档位 += 标记"加法 (超长静音主凶之一)
    ③ manifest duration 回写 (处理后真长, 下游单真相源)

    lines=None (reroll/重拼场景): 档位取已存 breath_pause 与文本标记的 max —
    批合成时判定的档位经 manifest 透传, 无需全稿上下文."""
    import subprocess
    from .text import _is_question, _pause_after
    module_final = module_final or set()
    for i, seg in enumerate(manifest_segments):
        wav = output_dir / str(seg.get("file") or "")
        if not wav.exists():
            continue
        # ① 切头尾 (幂等锚)
        trimmed = _trim_edges(wav)
        if trimmed is None:
            continue
        seg["duration"] = trimmed
        # ② 档位判定 → 垫尾 (末包不垫: 音频结束无尾气口, 尾垫让末镜槽虚胖)
        if i >= len(manifest_segments) - 1:
            seg["breath_pause"] = 0.0
            continue
        if lines is not None:
            li = seg.get("line_indices") or [seg.get("index", 0)]
            last_idx = li[-1]
            last = lines[last_idx] if last_idx < len(lines) else str(seg.get("text") or "")
            # 0930 五分区气口档: module_id → question/para 档覆盖全局值
            _zb = (zone_breath or {}).get(seg.get("module_id")) or {}
            _q_sec = float(_zb.get("question", question_sec))
            _p_sec = float(_zb.get("para", para_sec))
            pause = _BREATH_SEAM_FLOOR
            if _q_sec > 0 and _is_question(last):
                pause = max(pause, _q_sec)
            if _p_sec > 0 and last_idx in para_final:
                pause = max(pause, _p_sec)
            if module_final and last_idx in module_final:
                pause = max(pause, module_sec)
            pause = max(pause, _pause_after(last) or 0.0)  # -Xs-: max 融入 (1010 废止加法)
        else:
            _mark = _pause_after(str(seg.get("text") or "")) or 0.0
            pause = max(_BREATH_SEAM_FLOOR, float(seg.get("breath_pause") or 0.0), _mark)
        if not 0 < pause <= 3:
            pause = _BREATH_SEAM_FLOOR
        tmp = wav.with_suffix(".pad.wav")
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav), "-af", f"apad=pad_dur={pause}",
                 str(tmp)], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                tmp.replace(wav)
                seg["duration"] = round(trimmed + pause, 3)
                seg["breath_pause"] = pause
            else:
                tmp.unlink(missing_ok=True)
        except Exception:
            tmp.unlink(missing_ok=True)  # 垫不上不阻断产线 (音频照常, 只是没这口气)


def _apply_question_pauses(
    output_dir: Path, lines: list[str],
    manifest_segments: list[dict[str, Any]], default_sec: float,
) -> None:
    from .text import _is_question, _pause_after
    import subprocess
    for seg in manifest_segments:
        li = seg.get("line_indices") or [seg.get("index", 0)]
        last = lines[li[-1]] if li[-1] < len(lines) else str(seg.get("text") or "")
        pause = _pause_after(last)
        if pause is None and _is_question(last):
            pause = default_sec
        if not pause or pause <= 0 or pause > 3:
            continue
        wav = output_dir / str(seg.get("file") or "")
        if not wav.exists():
            continue
        tmp = wav.with_suffix(".pad.wav")
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav), "-af", f"apad=pad_dur={pause}",
                 str(tmp)], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                tmp.replace(wav)
                seg["duration"] = round(float(seg.get("duration") or 0.0) + pause, 3)
                seg["question_pause"] = pause
        except Exception:
            tmp.unlink(missing_ok=True)  # 垫不上不阻断产线 (音频照常, 只是没这口气)


def _group_by_emotion_segments(
    lines: list[str],
    emotion_segments: list[dict[str, Any]],
    max_chars: int = 300,
) -> tuple[list[list[int]], list[dict[str, Any] | None]]:
    """按 P5 段落分组行: 段内行合并为 batch (≤max_chars), 每批带该段情绪参数.

    行→段顺序匹配 (_map_lines_to_segments); 匹配失败的行落 calm (vector=None).
    Returns: (batch_groups, batch_emos) — batch_emos 与 batch_groups 等长.
    """
    line_emos = _map_lines_to_segments(lines, emotion_segments)
    batch_groups: list[list[int]] = []
    batch_emos: list[dict[str, Any] | None] = []
    current: list[int] = []
    current_emo: dict[str, Any] | None = None
    current_chars = 0
    for idx, emo in enumerate(line_emos):
        line = lines[idx]
        if current and (emo != current_emo or current_chars + len(line) > max_chars):
            batch_groups.append(current)
            batch_emos.append(current_emo)
            current = []
            current_chars = 0
        current.append(idx)
        current_chars += len(line)
        current_emo = emo
    if current:
        batch_groups.append(current)
        batch_emos.append(current_emo)
    return batch_groups, batch_emos


def _map_lines_to_segments(
    lines: list[str],
    emotion_segments: list[dict[str, Any]],
) -> list[dict[str, Any] | None]:
    """顺序匹配行到 P5 段: 累积行文本, 段文本被消费后前进到下一段.

    容错: 段文本匹配不上 → 该行落 calm (None). 纯字符串逻辑, 不感知段边界.
    """
    import re

    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    anno = [dict(s, _n=_norm(s.get("text"))) for s in emotion_segments]
    result: list[dict[str, Any] | None] = []
    acc = ""
    ai = 0
    for line in lines:
        acc += _norm(line)
        cur = ai  # 匹配前段号: 本行归属匹配发生时所在段
        # 当前段文本已被累积文本消费 → 前进到下一段 (重置累积, 简单容错)
        while ai < len(anno) and anno[ai]["_n"] and anno[ai]["_n"] in acc:
            ai += 1
            acc = ""
        if cur < len(anno):
            result.append({"vector": anno[cur].get("vector"), "alpha": anno[cur].get("alpha", 1.0)})
        else:
            result.append(None)
    return result


def _process_batches(
    output_dir: Path, lines: list[str], batch_groups: list[list[int]],
    syn: _SynthesisParams, progress_callback: ProgressCallback | None,
    batch_emos: list[dict[str, Any] | None] | None = None,
    hook_df: float | None = None, hook_chars: int = 0,
    speed_zones: list[dict] | None = None,
    synth_unit: str = "line",
) -> tuple[list[Path], list[dict[str, Any]], int]:
    """Loop batches, skipping on-disk ones; returns paths, manifest, completed.

    hook_df (0912): 开场前 hook_chars 字的批换语速 (断崖式旧参数)。
    speed_zones (0912 晚 · 三区渐降, 用户令"提前布局"): [{"upto_chars": 60, "duration_factor": 0.95}, ...]
    按批首行字符偏移落区, 区外用全局; 提供时优先于 hook_df — 锤区快→过渡中→正文巡航, 消除断崖。
    """
    segment_paths: list[Path] = []
    manifest_segments: list[dict[str, Any]] = []
    completed = 0
    # 续传指纹的引擎/音色维度 (0910): backend+voice+参考音内容哈希 — 换引擎/换音色/
    # 参考文件换内容(同名替换)后旧 wav 必失效; 情感参考路径一并入签 (0910 bug#4)
    _vsig = "|".join(str(x) for x in (
        getattr(syn, "backend", ""), getattr(syn, "voice_id", ""),
        _asset_sig(getattr(syn, "master_audio", "") or ""),
        _asset_sig(str(((syn.params or {}).get("emo_audio_prompt") or "")))))
    try:
        # 钩子提速 (0912): 行首字符偏移表 → 判定各批是否落在开场 hook 窗口内
        _line_offsets: list[int] = []
        _acc = 0
        for _l in lines:
            _line_offsets.append(_acc)
            _acc += len(_l)
        for batch_idx, line_indices in enumerate(batch_groups):
            batch_lines = [lines[i] for i in line_indices]
            emo = batch_emos[batch_idx] if batch_emos else None
            _df_batch = None
            if speed_zones:
                # 三区斜坡 (0913): 批首行偏移插值取 df — 区内线性过渡到下一区值,
                # 消除边界台阶 (job 2adcf9b7 行9→10 "突然慢一档" 实锤; 旧实现每边界
                # 跳 0.10-0.11, 人耳可辨)
                _df_batch = zone_duration_factor(
                    speed_zones, _line_offsets[line_indices[0]],
                    float((syn.params or {}).get("duration_factor", 1.16)))
            else:
                _df_batch = hook_df if (hook_df is not None and hook_chars > 0
                                        and _line_offsets[line_indices[0]] < hook_chars) else None
            # Resume: only skip a whole batch — split_by_silence maps batch audio
            # → lines by position, so a batch must be rebuilt entirely.
            # 情绪指纹 (0909): wav 在盘还不够, 必须与本批情绪参数一致 — P5 重标
            # (坡度平滑/波浪铁律) 后旧 wav 情绪作废, 只看文件存在会整篇复用旧音频,
            # 情绪修复零听感 (008/009 台阶实锤)。旧产物无指纹文件 → 全量重合成。
            # 包模式 (0913): 一包=首行行号一个 wav, 只查首行文件。
            if synth_unit == "chunk":
                existing_paths = [output_dir / f"{line_indices[0]:03d}.wav"]
            else:
                existing_paths = [output_dir / f"{i:03d}.wav" for i in line_indices]
            if all(p.is_file() and p.stat().st_size > 0 for p in existing_paths):
                if _emo_fingerprint_matches(output_dir, batch_idx, emo, _vsig,
                                           text_sig=_text_sig(batch_lines)):
                    completed = _skip_batch(
                        output_dir, lines, line_indices, existing_paths, batch_idx,
                        completed, progress_callback, segment_paths, manifest_segments,
                        synth_unit=synth_unit,
                    )
                    continue
            completed = _run_batch(
                output_dir, batch_idx, batch_lines, lines, line_indices, completed,
                syn, progress_callback, segment_paths, manifest_segments,
                emo_vector=emo["vector"] if emo else None,
                emo_alpha=emo["alpha"] if emo else 1.0,
                duration_factor=_df_batch,
                synth_unit=synth_unit,
            )
    except Exception:
        _cleanup_failed_batches(output_dir, completed, len(lines), len(segment_paths))
        raise
    return segment_paths, manifest_segments, completed


def _split_line_indices(text: str) -> list[str]:
    """Normalize input text into cleaned, non-empty lines."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [_sanitize_for_fish(line) for line in lines if line.strip()]
    if not lines:
        raise ValueError("No non-empty lines to synthesize")
    return lines


def _text_sig(lines: list[str]) -> str:
    """批文本哈希 — 改词/改标点后旧 wav 失效 (0910 bug#7: 改稿必重合成)。"""
    import hashlib as _hl
    return _hl.sha1("".join(lines).encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _asset_sig(path: str) -> str:
    """参考音频内容签名 (路径+大小+mtime) — 同名换内容 (预设覆盖拷贝) 也失效。"""
    try:
        from pathlib import Path as _P
        p = _P(path)
        if not path or not p.is_file():
            return path or ""
        st = p.stat()
        return f"{path}:{st.st_size}:{int(st.st_mtime)}"
    except Exception:
        return path or ""


def _emo_fingerprint_matches(output_dir: Path, batch_idx: int, emo: dict | None,
                             voice_sig: str = "", text_sig: str = "") -> bool:
    """本批情绪+引擎+音色与上次合成是否一致 (断点续传闸门, 0909; 0910 补引擎维度).

    指纹 = vector+alpha+voice_sig (backend+voice_id+master_audio — 换引擎/换音色
    后旧 wav 必失效, 0910 实锤: line-2 与 2.5 同 confident/3 指纹撞车全量复用)。
    旧指纹无 voice_sig 键 → 结构不等 → False (强制重合成一次)。
    """
    import json as _json

    cur = ({"vector": emo.get("vector"), "alpha": emo.get("alpha"),
            "voice_sig": voice_sig, "text_sig": text_sig}
           if emo else {"voice_sig": voice_sig, "text_sig": text_sig})
    fp = output_dir / f".emo_{batch_idx:04d}.json"
    try:
        old = _json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else None
    except Exception:
        return False
    return old == cur


def _record_emo_fingerprint(output_dir: Path, batch_idx: int, emo: dict | None,
                             voice_sig: str = "", text_sig: str = "") -> None:
    """批合成成功后写指纹 (vector+alpha+voice_sig; 失败静默 — 只影响下次多合成)."""
    import json as _json

    try:
        payload = ({"vector": emo.get("vector"), "alpha": emo.get("alpha"),
                    "voice_sig": voice_sig, "text_sig": text_sig}
                   if emo else {"voice_sig": voice_sig, "text_sig": text_sig})
        (output_dir / f".emo_{batch_idx:04d}.json").write_text(
            _json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _skip_batch(
    output_dir: Path, lines: list[str], line_indices: list[int],
    existing_paths: list[Path], batch_idx: int, completed: int,
    progress_callback: ProgressCallback | None,
    segment_paths: list[Path], manifest_segments: list[dict[str, Any]],
    synth_unit: str = "line",
) -> int:
    """Resume path: reuse on-disk WAVs and record manifest entries."""
    if synth_unit == "chunk":
        final_path = existing_paths[0]
        seg_entry = _build_segment_entry(final_path, line_indices[0],
                                         "\n".join(lines[i] for i in line_indices))
        seg_entry["line_indices"] = list(line_indices)
        seg_entry["chunk"] = True
        manifest_segments.append(seg_entry)
        segment_paths.append(final_path)
        completed += 1
        if progress_callback:
            progress_callback(completed, len(lines), seg_entry["text"], {**seg_entry})
        logger.info("synthesize_lines: skipped chunk batch %d (wav on disk)", batch_idx)
        return completed
    for offset, line_idx in enumerate(line_indices):
        final_path = existing_paths[offset]
        seg_entry = _build_segment_entry(final_path, line_idx, lines[line_idx])
        manifest_segments.append(seg_entry)
        segment_paths.append(final_path)
        completed += 1
        if progress_callback:
            progress_callback(completed, len(lines), lines[line_idx], {**seg_entry})
    logger.info(
        "synthesize_lines: skipped batch %d (%d line%s already on disk)",
        batch_idx, len(line_indices), "" if len(line_indices) == 1 else "s",
    )
    return completed


def _run_batch(
    output_dir: Path, batch_idx: int, batch_lines: list[str],
    lines: list[str], line_indices: list[int], completed: int,
    syn: _SynthesisParams, progress_callback: ProgressCallback | None,
    segment_paths: list[Path], manifest_segments: list[dict[str, Any]],
    emo_vector: list[float] | None = None, emo_alpha: float = 1.0,
    duration_factor: float | None = None,
    synth_unit: str = "line",
) -> int:
    """Synthesize one batch; line 模式切回逐行, chunk 模式整包即成品."""
    # "||" gets replaced with "，" in _tts_text(), creating a natural pause.
    batch_text = "||".join(batch_lines)
    inference_text = _tts_text(batch_text, keep_breaks=(syn.backend == "indextts"))
    batch_path = output_dir / f"_batch_{batch_idx:03d}.wav"
    # 0917 根治: -Xs- 标记 = 合成硬边界 (旧约定只认行尾 — 编辑按钮光标处插入的
    # 句中标记既不剥也不垫, 直念"负一S", 033 包实锤)。chunk 模式每片独立解码后
    # 片间垫静音; 末片停顿清零归 _apply_breath_pauses (wav 尾垫), 防双垫。
    # line 模式不拆片 — 垫入的静音会被 _split_wav_by_silence 误判为行边界,
    # 标记由 _tts_text 无条件剥 (行尾垫仍走 _apply_breath_pauses)。
    _pieces = _split_by_pause_marks(batch_text) if synth_unit == "chunk" else []
    if len(_pieces) > 1:
        _piece_wavs: list[tuple[Path, float]] = []
        for _j, (_ptext, _ppause) in enumerate(_pieces):
            _pwav = output_dir / f"_batch_{batch_idx:03d}_p{_j:02d}.wav"
            _synth_single(syn, _tts_text(_ptext, keep_breaks=(syn.backend == "indextts")),
                          _pwav, emo_vector=emo_vector, emo_alpha=emo_alpha,
                          duration_factor=duration_factor)
            _piece_wavs.append(
                (_pwav, _ppause if _j < len(_pieces) - 1 else 0.0))
        _concat_wavs_with_pauses(_piece_wavs, batch_path)
        for _pwav, _ in _piece_wavs:
            _pwav.unlink(missing_ok=True)
    else:
        _synth_single(syn, inference_text, batch_path,
                      emo_vector=emo_vector, emo_alpha=emo_alpha,
                      duration_factor=duration_factor)

    if synth_unit == "chunk":
        # 0913 包化: 一包一次完整解码 = 一个 wav, 永不静音切分 — bleed/丢头/串尾
        # 结构性消灭 (批切分漂移是当日全部案情根因, 用户令根治)。
        final_path = output_dir / f"{line_indices[0]:03d}.wav"
        if final_path.exists():
            final_path.unlink(missing_ok=True)
        batch_path.rename(final_path)
        seg_entry = _build_segment_entry(final_path, line_indices[0],
                                         "\n".join(batch_lines))
        seg_entry["line_indices"] = list(line_indices)
        seg_entry["chunk"] = True
        segment_paths.append(final_path)
        manifest_segments.append(seg_entry)
        completed += 1
        if progress_callback:
            progress_callback(completed, len(lines), "\n".join(batch_lines), {**seg_entry})
        _vs = "|".join(str(x) for x in (
            getattr(syn, "backend", ""), getattr(syn, "voice_id", ""),
            getattr(syn, "master_audio", "") or ""))
        _record_emo_fingerprint(output_dir, batch_idx,
                                {"vector": emo_vector, "alpha": emo_alpha} if emo_vector else None, _vs,
                                text_sig=_text_sig(batch_lines))
        return completed

    split_paths = _split_wav_by_silence(
        wav_path=batch_path, expected_count=len(batch_lines),
        output_dir=output_dir, stem=f"{batch_idx:03d}", line_texts=batch_lines,
    )
    for offset, line_idx in enumerate(line_indices):
        final_path = _rename_to_final(split_paths, offset, line_idx, output_dir)
        segment_paths.append(final_path)
        seg_entry = _build_segment_entry(final_path, line_idx, lines[line_idx])
        manifest_segments.append(seg_entry)
        completed += 1
        if progress_callback:
            progress_callback(completed, len(lines), lines[line_idx], {**seg_entry})

    batch_path.unlink(missing_ok=True)
    # voice_sig 就地自算 (syn 是本函数参数, 勿引用外层 _process_batches 作用域)
    _vs = "|".join(str(x) for x in (
        getattr(syn, "backend", ""), getattr(syn, "voice_id", ""),
        getattr(syn, "master_audio", "") or ""))
    _record_emo_fingerprint(output_dir, batch_idx,
                            {"vector": emo_vector, "alpha": emo_alpha} if emo_vector else None, _vs,
                            text_sig=_text_sig(batch_lines))
    return completed


def _synth_single(syn: _SynthesisParams, text: str, output_path: Path,
                  emo_vector: list[float] | None = None, emo_alpha: float = 1.0,
                  duration_factor: float | None = None) -> Path:
    """Forward one synthesis call with the bundled parameters.

    duration_factor (0912): 批级语速覆盖 (钩子提速); None=用 syn.params 原值。
    """
    params = syn.params
    if duration_factor is not None:
        params = {**(params or {}), "duration_factor": duration_factor}
    return _synthesize_single(
        text=text, output_path=output_path, backend=syn.backend,
        voice_id=syn.voice_id, reference_audio=syn.reference_audio,
        reference_text=syn.reference_text, base_url_fish=syn.base_url_fish,
        base_url_f5=syn.base_url_f5, base_url_indextts=syn.base_url_indextts,
        master_audio=syn.master_audio, master_text=syn.master_text,
        master_style=syn.master_style, params=params,
        emo_vector=emo_vector, emo_alpha=emo_alpha,
    )


def _rename_to_final(
    split_paths: list[Path], offset: int, line_idx: int, output_dir: Path,
) -> Path:
    """Rename a split file to {{line_idx:03d}}.wav; pad with silence when missing."""
    final_path = output_dir / f"{line_idx:03d}.wav"
    # 目标已存在时先删 (重跑生成音频时旧 wav 残留, Windows rename 目标存在抛 WinError 183)
    if final_path.exists():
        final_path.unlink(missing_ok=True)
    if offset < len(split_paths):
        split_paths[offset].rename(final_path)
    else:
        _write_wav(final_path, np.zeros((1,), dtype=np.float32), 24000)
    return final_path


def _build_segment_entry(final_path: Path, line_idx: int, text: str) -> dict[str, Any]:
    """Build one manifest segment entry from a per-line WAV file."""
    try:
        info = sf.info(str(final_path))
        duration = round(info.duration, 3)
        sample_rate = info.samplerate
    except Exception:
        duration = 0.0
        sample_rate = 0
    return {
        "index": line_idx,
        "text": text,
        "inference_text": _tts_text(text),
        "file": final_path.name,
        "duration": duration,
        "sample_rate": sample_rate,
    }


def _finalize_manifest(
    output_dir: Path, voice_id: str, backend: str,
    manifest_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write manifest.json into output_dir and return the manifest dict."""
    manifest: dict[str, Any] = {
        "voice_id": voice_id,
        "backend": backend,
        "sample_rate": manifest_segments[0].get("sample_rate", 24000) if manifest_segments else 24000,
        "segment_count": len(manifest_segments),
        "segments": manifest_segments,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _cleanup_failed_batches(
    output_dir: Path, completed: int, total_lines: int, kept_segments: int,
) -> None:
    """Failure path: keep on-disk WAVs + partial manifest; remove in-progress batches."""
    logger.warning(
        "synthesize_lines failed after %d/%d segments; keeping %d wav file(s) "
        "and partial manifest for resumable retry",
        completed, total_lines, kept_segments,
    )
    for batch_path in output_dir.glob("_batch_*.wav"):
        try:
            batch_path.unlink(missing_ok=True)
        except OSError:
            pass
