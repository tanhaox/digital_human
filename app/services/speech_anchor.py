"""语速锚定协调器 (0929 用户令).

病象: 重roll / 编辑改稿重生成后, 行语速落回 zone 默认 — 单条听正常,
连播时与邻行听感不协调 (手动 df 档不入库 + atempo 锚随新 take 清零 + take 间模型方差).

机制 (纯确定性, 不引入 ASR/LLM):
- 锚 = 用户标记的基准行实测含气口语速 (字/s = 汉字数 ÷ wav 时长, 气口不剥离).
  0929 实证: ep6 行019 (老谭·说书TTS2, 巡航 df1.16+情绪参考) = 1.86 字/s 用户裁定舒服.
- zone 分组 (hook <60字 / transition <150字 / cruise ≥150字, 与三区斜坡同偏移口径):
  锚按 zone 存, 校正只对同 zone 行 — 钩子行天然快 (ep6 行000 3.57 vs 019 1.86),
  不被巡航锚拖坏; 未标锚的 zone 不校正.
- 校正 = atempo 一次成型拉回锚带内 (变速不变调, 语音与气口等比缩放):
  复用 0921 accel_bak 模式 — 首次校正前存原始 take, 之后从原始按绝对倍率成型,
  小步多点与一步到位等价; factor 写 tempo.json 与页面 ⏩/⟲ 无缝接力.
- 带宽 ±10% (听感无差别) 不动; 超 ±15% (atempo 痕迹风险) 不硬拉, 报 far 建议重roll.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
# 与 scripts/tts_lib/lines.py 三区斜坡同口径 (行首字符偏移)
ZONE_HOOK, ZONE_TRANS = 60, 150
BAND, CAP = 0.10, 0.15


def han_count(text: str) -> int:
    return len(_HAN_RE.findall(text or ""))


def zone_of(offset: int) -> str:
    if offset < ZONE_HOOK:
        return "hook"
    if offset < ZONE_TRANS:
        return "transition"
    return "cruise"


def measure_rate(text: str, duration: float) -> float | None:
    """含气口实测语速 (字/s); 字太少或无时长返回 None (不参与协调)."""
    n, d = han_count(text), float(duration or 0)
    if n < 6 or d <= 0.3:
        return None
    return n / d


def _voice_cfg(voice) -> dict:
    """voice.config_json 兼容 dict (ORM JSON 列自动反序列化) / str (裸文本) 两种形态."""
    cfg = getattr(voice, "config_json", None)
    if isinstance(cfg, dict):
        return cfg
    if isinstance(cfg, str) and cfg.strip():
        try:
            parsed = json.loads(cfg)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def read_anchor(voice) -> dict:
    """voice → {zone: {"rate": float, "ref": str, "ts": str}} (config_json.params.speech_anchors)."""
    a = (_voice_cfg(voice).get("params") or {}).get("speech_anchors") or {}
    return a if isinstance(a, dict) else {}


def write_anchor(voice, zone: str, rate: float, ref: str) -> None:
    import copy
    cfg = copy.deepcopy(_voice_cfg(voice))  # 新引用 — JSON 列同对象赋值 SQLAlchemy 不标 dirty
    cfg.setdefault("params", {})
    from datetime import datetime
    cfg["params"]["speech_anchors"] = {
        **(cfg["params"].get("speech_anchors") or {}),
        zone: {"rate": round(rate, 3), "ref": ref,
               "ts": datetime.now().strftime("%Y-%m-%d %H:%M")},
    }
    voice.config_json = cfg


def _swap_in(src: Path, dst: Path) -> None:
    # 0921 Win 锁坑同款: <audio> 流式拉 wav 持读句柄, os.replace 短重试必过
    import os
    import time
    for i in range(12):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == 11:
                raise
            time.sleep(0.25)


def apply_atempo(wav: Path, factor: float) -> float:
    """从原始 take (accel_bak, 无则现场存) 按绝对倍率一次成型; 返回新时长."""
    bak = wav.with_suffix(".accel_bak.wav")
    if not bak.exists():
        shutil.copy2(wav, bak)
    tmp = wav.with_suffix(".anchor.tmp.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(bak),
                    "-af", f"atempo={factor:.4f}", str(tmp)], check=True)
    _swap_in(tmp, wav)
    wav.with_suffix(".tempo.json").write_text(
        json.dumps({"factor": round(factor, 4)}), encoding="utf-8")
    with wave.open(str(wav), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 3)


def harmonize(wav: Path, text: str, duration: float, anchor_rate: float) -> dict:
    """单行协调: 带内不动 / 带外帽内 atempo 拉回 / 超帽报 far.

    返回 {"changed", "rate"(校正后), "anchor", "flag": ok|done|far, "factor", "duration"}.
    """
    rate = measure_rate(text, duration)
    if rate is None:
        return {"changed": False, "flag": "skip", "anchor": anchor_rate}
    ratio = rate / anchor_rate
    dev = ratio - 1
    if abs(dev) <= BAND:
        return {"changed": False, "rate": round(rate, 2), "anchor": anchor_rate, "flag": "ok"}
    if abs(dev) > CAP:
        return {"changed": False, "rate": round(rate, 2), "anchor": anchor_rate,
                "flag": "far", "dev": f"{dev:+.0%}"}
    factor = 1 / ratio  # 慢行 factor>1 加速, 快行 factor<1 减速
    new_dur = apply_atempo(wav, factor)
    return {"changed": True, "rate": round(anchor_rate, 2), "anchor": anchor_rate,
            "flag": "done", "factor": round(factor, 3), "dev": f"{dev:+.0%}",
            "duration": new_dur}
