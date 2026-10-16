# -*- coding: utf-8 -*-
"""TTS 先行 (0912) — IndexTTS2.5 老谭读书通道: 逐镜单行合成 + 时间轴归真 + 漂移审计.

设计 (0911 待办①, 用户架构令): 先语音 → 实测真实时长 → 预留标题卡 → 动画按具体秒数。
- 单行合成天然无 bleed (批合成串句坑绕开, memory tts-batch-bleed-verify-loop)
- 音频 = 时间轴真理源: manifest.json + shots.json 的 t_start/t_end 按实测重写
  (旧轴存 t_start_orig/t_end_orig, 只在首次归真时落)
- **不动** anim.beats / status / 已生成视频 — 漂移三分类只审计报告:
  OK(≤0.25s 剪辑无感) / 吸收(≤0.5s 剪辑层定格吸收) / H3重跑(>0.5s 或音频>8s 段超)
- 文本预洗 = 产线同款纯函数平移 (零 app 依赖): 词表注音/年份/百分号/连字符守卫
  + 2.5 预洗 (H.B.O 加点/书名号→逗号/--→到/【】剥离, 注音标签保护)

引擎: POST {base}/v1/tts (IndexTTS2.5 api_server @7866, 产线 _specs.py 同款自拉起命令)。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

import requests

from . import shots as shots_mod
from .config import load

logger = logging.getLogger(__name__)

__all__ = ["run_tts", "tts_input"]

# ── 文本预洗 (产线 pinyin_fix.py + engines._wash_indextts25 同款平移) ──────────

_CN_DIGITS = {str(d): cn for d, cn in enumerate("零一二三四五六七八九")}

_YEAR_RE = re.compile(r"(?<![\d])(19|20)(\d{2})(?![\d])")
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
# 型号连字符守卫: GPT-6→GPT六 (只动字母↔数字接合处, 日期区间/英文短语不碰)
_HYPHEN_MODEL_RE = re.compile(r"([A-Za-z])-([0-9](?:[0-9.]*[0-9])?)")
_HYPHEN_DIGIT_LETTER_RE = re.compile(r"([0-9])-([A-Za-z])")
# 已标注形态 <字|PINYIN> (词表命中防重入 + 预洗保护)
_MARKED_RE = re.compile(r"<(\S{1,4})\|[A-Z]+[1-5]>")
# 2.5 预洗: 连续大写缩写加点 (HBO→H.B.O) / 注音标签整段保护
_ABBREV_RE = re.compile(r"(?<![A-Za-z.])([A-Z]{3,})(?![A-Za-z])")
_ANNOT_RE = re.compile(r"<[^<>|]*\|[^<>]*>")


def _int_to_cn(n: int) -> str:
    """整数千以内口播读法 (500→五百); 万亿级 narration 罕见, 简版够用."""
    if n < 10:
        return _CN_DIGITS[str(n)]
    if n < 20:
        return "十" + (_CN_DIGITS[str(n % 10)] if n % 10 else "")
    if n < 100:
        return _CN_DIGITS[str(n // 10)] + "十" + (_CN_DIGITS[str(n % 10)] if n % 10 else "")
    if n < 1000:
        tail = "" if n % 10 == 0 else (("" if n % 100 < 10 else "零") + _int_to_cn(n % 100))
        return _CN_DIGITS[str(n // 100)] + "百" + tail
    for hi, unit in ((10**8, "亿"), (10**4, "万")):
        if n >= hi:
            return _int_to_cn(n // hi) + unit + (_int_to_cn(n % hi) if n % hi else "")
    return str(n)


def _num_to_reading(tok: str) -> str:
    """数字串→口播读法: 1900-2099 四位按年逐位读; 其余按值 (3.5→三点五)."""
    if len(tok) == 4 and tok.isdigit() and 1900 <= int(tok) <= 2099:
        return "".join(_CN_DIGITS[c] for c in tok)
    if "." in tok:
        head, _, tail = tok.partition(".")
        return (_int_to_cn(int(head)) if head else "") + "点" + "".join(_CN_DIGITS[c] for c in tail)
    return _int_to_cn(int(tok))


def _years_to_cn(text: str) -> str:
    """4 位年份→逐位汉字 (1975→一九七五); 底稿真实形, 仅合成输入侧."""
    return _YEAR_RE.sub(lambda m: "".join(_CN_DIGITS[c] for c in m.group(0)), text)


def _pct_to_cn(text: str) -> str:
    """96% → 百分之九十六."""
    return _PCT_RE.sub(lambda m: "百分之" + _num_to_reading(m.group(1)), text)


def _hyphen_guard(text: str) -> str:
    if "-" not in text:
        return text
    text = _HYPHEN_MODEL_RE.sub(
        lambda m: m.group(1) + "".join(_CN_DIGITS.get(c, c) for c in m.group(2)), text)
    return _HYPHEN_DIGIT_LETTER_RE.sub(lambda m: _CN_DIGITS[m.group(1)] + m.group(2), text)


def _pinyin_rules(path: str) -> dict[str, str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rules = data.get("rules") or {}
        if isinstance(rules, dict):
            return rules
    except Exception:
        logger.warning("[tts] 拼音词表加载失败, 跳过纠音: %s", path)
    return {}


def _apply_pinyin_marks(text: str, rules: dict[str, str]) -> str:
    if not text or _MARKED_RE.search(text):
        return text
    for word, marked in rules.items():
        if word in text:
            text = text.replace(word, marked)
    return text


def _wash_25(text: str) -> str:
    """2.5 预洗: ｜【】剥离 / 缩写加点 / --→到 / 书名号→逗号 / 标点去重; 注音标签保护."""
    parts = _ANNOT_RE.split(text)
    keep = _ANNOT_RE.findall(text)
    washed: list[str] = []
    for i, seg in enumerate(parts):
        seg = re.sub(r"[｜【】]", " ", seg)
        seg = _ABBREV_RE.sub(lambda m: ".".join(m.group(1)), seg)  # HBO → H.B.O
        seg = seg.replace("--", "到")
        # 书名号 2.5 跳过致连读奇怪 (0910 实锤): 开书名号换气口, 闭书名号随原句
        seg = seg.replace("《", "，").replace("》", "，")
        seg = re.sub(r"，([，。！？；：、])", lambda m: m.group(1), seg)
        seg = seg.replace("，，", "，")
        washed.append(seg)
        if i < len(keep):
            washed.append(keep[i])
    return "".join(washed)


# 序数语境守卫 (1005 用户实锤: 第2集 → IndexTTS 读"第两集" — 模型把 2+名词当量词
# 语境读两): 序数标记"第"后的阿拉伯数字确定性转汉字 (第二集/第3步/第5件 同治)
_ORDINAL_RE = re.compile(r"第(\d{1,4})(?=[集步件章节条幕课期关卡年天月周])")


def _ordinals_to_cn(text: str) -> str:
    return _ORDINAL_RE.sub(lambda m: "第" + _int_to_cn(int(m.group(1))), text)


def tts_input(text: str, pinyin_map_path: str) -> str:
    """合成输入终态: 年份/百分号/连字符/序数守卫 → 词表注音 → 2.5 预洗."""
    t = _hyphen_guard(_pct_to_cn(_years_to_cn(_ordinals_to_cn(text))))
    t = _apply_pinyin_marks(t, _pinyin_rules(pinyin_map_path))
    return _wash_25(t)


# ── 合成 + 落盘 ───────────────────────────────────────────────

_LAUNCH_HINT = (
    "IndexTTS2.5 服务未起。产线同款拉起 (gpu_service_manager/_specs.py indextts25):\n"
    "  cd E:/AI/tts/index-tts2.5\n"
    "  .venv/Scripts/python.exe api_server.py --port 7866 --host 127.0.0.1\n"
    "  env: PYTHONPATH='' HF_ENDPOINT=https://hf-mirror.com HF_HOME=E:/AI/tts/index-tts2.5/.huggingface"
)


def _server_ok(base_url: str) -> bool:
    try:
        return requests.get(base_url.rstrip("/") + "/health", timeout=3).status_code == 200
    except Exception:
        return False


def _loudnorm(src: Path, dst: Path, lufs: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11", str(dst)],
        check=True, timeout=120)


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _synth_one(text: str, out_wav: Path, tcfg) -> None:
    """单镜合成: POST /v1/tts → 裸 wav → loudnorm 归一覆盖落盘 (重试 2 次)."""
    payload = {
        "text": text,
        "spk_audio_prompt": str(Path(tcfg.timbre_wav).resolve()),
        "master_text": tcfg.master_text,
        "master_style": "calm",
        "max_text_tokens_per_segment": 120,
        "do_sample": tcfg.do_sample,
        "top_p": tcfg.top_p,
        "top_k": tcfg.top_k,
        "temperature": tcfg.temperature,
        "seed": None,
        "use_emo_text": False,
        "emo_text": None,
        "emo_alpha": tcfg.emo_alpha,
        "emo_audio_prompt": str(Path(tcfg.emo_wav).resolve()),
        "duration_factor": tcfg.duration_factor,
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(tcfg.base_url.rstrip("/") + "/v1/tts",
                              json=payload, timeout=300)
            r.raise_for_status()
            raw = r.content
            if not raw or len(raw) < 44:
                raise RuntimeError("2.5 返回空/无效 wav")
            raw_tmp = out_wav.with_suffix(".raw.wav")
            raw_tmp.write_bytes(raw)
            _loudnorm(raw_tmp, out_wav, tcfg.lufs)
            raw_tmp.unlink(missing_ok=True)
            return
        except Exception as exc:  # noqa: BLE001 — 重试后仍失败则抛给上层断点续跑
            last_err = exc
            logger.warning("[tts] 合成失败 (第%d次): %s", attempt + 1, exc)
            time.sleep(2)
    raise RuntimeError(f"合成三连败: {last_err}")


# ── 主流程: 合成 → manifest → 时间轴归真 → 漂移审计 ─────────────────────────

def run_tts(doc: dict[str, Any], only: set[str] | None = None,
            force: bool = False, gap: float | None = None,
            title_card: float | None = None) -> dict[str, Any]:
    tcfg = load().tts
    if not _server_ok(tcfg.base_url):
        raise SystemExit(_LAUNCH_HINT)
    for p in (tcfg.timbre_wav, tcfg.emo_wav):
        if not Path(p).exists():
            raise SystemExit(f"参考音频缺失: {p}")
    gap = tcfg.gap_sec if gap is None else gap
    title_card = tcfg.title_card_sec if title_card is None else title_card

    audio_dir = shots_mod.ep_dir(doc["book_title"], doc["ep"]) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    shots = sorted(doc["shots"], key=lambda s: float(s.get("t_start", 0)))

    # ── ① 逐镜合成 (断点续跑: 已有 wav 跳过, --force 全重; 单镜失败不炸整批) ──
    rules_map = tcfg.pinyin_map
    synth_n, skip_n, fail_ids = 0, 0, []
    for s in shots:
        sid = s["shot_id"]
        if only and sid not in only:
            continue
        text = (s.get("narration") or "").strip()
        if not text:
            logger.warning("[tts] %s 无 narration, 跳过", sid)
            continue
        out_wav = audio_dir / f"{sid}.wav"
        if out_wav.exists() and not force:
            skip_n += 1
        else:
            try:
                _synth_one(tts_input(text, rules_map), out_wav, tcfg)
                synth_n += 1
                logger.info("[tts] %s ✓", sid)
            except Exception as exc:  # noqa: BLE001 — 记失败继续, 收尾统一上报
                fail_ids.append(sid)
                logger.error("[tts] %s 合成失败: %s", sid, exc)
                continue
        s["audio_file"] = str(out_wav.relative_to(shots_mod.ep_dir(doc["book_title"], doc["ep"])))
        s["audio_dur_s"] = round(_wav_duration(out_wav), 3)
    missing = fail_ids + [s["shot_id"] for s in shots
                          if (s.get("narration") or "").strip() and not (audio_dir / f"{s['shot_id']}.wav").exists()]
    if missing:
        logger.warning("[tts] %d 镜无音频 (失败/漏跑, 时间轴不含它们): %s",
                       len(missing), ",".join(missing[:8]))

    # ── ② manifest.json = 时间轴真理源 ──
    manifest = [
        {"shot_id": s["shot_id"], "file": s.get("audio_file", ""),
         "duration_s": s.get("audio_dur_s"), "text": (s.get("narration") or "").strip()}
        for s in shots if s.get("audio_dur_s")]
    (audio_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── ③ 时间轴归真: 标题卡预留 + 顺序累加 (旧轴仅首次备份) ──
    t = float(title_card)
    for s in shots:
        dur = s.get("audio_dur_s")
        if not dur:
            continue
        if "t_start_orig" not in s:
            s["t_start_orig"], s["t_end_orig"] = s.get("t_start"), s.get("t_end")
        s["t_start"] = round(t, 2)
        t += float(dur)
        s["t_end"] = round(t, 2)
        t += gap
    shots_mod.save(doc)

    # ── ④ 漂移审计 (只报告, 不动 beats/状态/视频) ──
    report_lines, stats = [], {"OK": [], "吸收": [], "H3重跑": [], "段超8s": []}
    for s in shots:
        dur = s.get("audio_dur_s")
        if not dur or "t_start_orig" not in s or s["t_start_orig"] is None:
            continue
        old_span = float(s["t_end_orig"]) - float(s["t_start_orig"])
        drift = float(dur) - old_span
        if dur > 8.0:
            cls = "段超8s"
        elif abs(drift) > 0.5:
            cls = "H3重跑"
        elif abs(drift) > 0.25:
            cls = "吸收"
        else:
            cls = "OK"
        stats[cls].append(f"{s['shot_id']}({drift:+.1f}s)")
        report_lines.append((s["shot_id"], cls, old_span, float(dur), drift))
    total = sum(m["duration_s"] or 0 for m in manifest)
    ep_len = title_card + total + gap * max(len(manifest) - 1, 0)
    flash = [s["shot_id"] for s in shots
             if any(("白闪" in str(b.get("motion", "")) or "急推" in str(b.get("motion", "")))
                    for b in (s.get("anim") or {}).get("beats") or [])]

    print(f"\n== TTS 完成: 新合成 {synth_n} / 续跑跳过 {skip_n} 镜 ==")
    print(f"口播总长 {total:.0f}s + 标题卡 {title_card}s + 镜间气口 → 全集成片 ≈ {ep_len:.0f}s")
    print(f"manifest: {audio_dir / 'manifest.json'}  (时间轴真理源)")
    print("-- 漂移审计 (vs 规划轴) --")
    for cls, ids in stats.items():
        print(f"  {cls}: {len(ids)}" + (f" — {','.join(ids[:12])}{'…' if len(ids) > 12 else ''}" if ids else ""))
    print(f"白闪/急推镜 {len(flash)} 个: {','.join(flash) if flash else '无'} (配额自查)")
    if stats["H3重跑"] or stats["段超8s"]:
        print("→ H3重跑: run.py h3 --reroll <ids>  |  段超8s 镜需 extend/重切 (音频超 H3 单镜上限)")
    return {"synth": synth_n, "skipped": skip_n, "total_s": round(total, 1),
            "ep_len_s": round(ep_len, 1), "drift": stats, "flash": flash}
