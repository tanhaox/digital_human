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
- 1118 用户令: 带宽设计废除 (带内放走大量拖行 / 帽拒修最慢行), 锚=地板, 见 harmonize.
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
# 0929 带宽常量 (BAND/CAP) 随 1118 带宽废除留名防外部 import 炸 (不再被读)
BAND, CAP = 0.0, 1.5
FMAX = 1.5   # atempo 单级上限, 与 /tempo 端点 FMAX 对齐
_TOL = 0.02  # 慢侧容差: 距锚 2% 内不动 (防 1-2% 增益的无效重采样)


def han_count(text: str) -> int:
    return len(_HAN_RE.findall(text or ""))


def zone_of(offset: int) -> str:
    if offset < ZONE_HOOK:
        return "hook"
    if offset < ZONE_TRANS:
        return "transition"
    return "cruise"


def _spoken_count(text: str) -> int:
    """1118 口径: 与 /tempo 端点读数一致 — 剥停顿标记与空白, 标点/数字计入。

    旧 han_count 纯汉字口径把数字行 (48小时/76台/PPT) 读慢 ~5-8%, 会顶帽误拉。
    """
    try:
        from scripts.tts_lib.text import _PAUSE_MARK_ANY_RE as _PMARK
        t = _PMARK.sub("", text or "")
    except Exception:  # noqa: BLE001 — scripts 包不可用时退化为内联同款模式
        t = re.sub(r"(?<![A-Za-z0-9])-(\d+(?:\.\d+)?)s-", "", text or "")
    return len(re.sub(r"\s", "", t))


def measure_rate(text: str, duration: float) -> float | None:
    """含气口实测语速 (字/s); 字太少或无时长返回 None (不参与协调)."""
    n, d = _spoken_count(text), float(duration or 0)
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
    """单行统一加速 (1118 用户令: 锚=地板, 非 0929 带宽).

    - 快行 (≥锚 98%): 绝不减速 — 护栏非总督 (1109/1112 实锚语义);
    - 慢行 (<锚 98%): atempo 一次成型拉回锚 (变速不变调, 语音与气口等比缩放);
      绝对档 = sidecar 已存档 × 增量 (1118 修: 直接用增量会覆盖手调档改慢行);
    - 总档超 FMAX(1.5): 尽力拉到帽, 标 capped 提示回听/重roll。

    返回 {"changed", "rate"(校正后), "anchor", "flag": ok|done|far, "factor", "duration"}.
    """
    rate = measure_rate(text, duration)
    if rate is None:
        return {"changed": False, "flag": "skip", "anchor": anchor_rate}
    dev = rate / anchor_rate - 1
    if dev >= -_TOL:
        return {"changed": False, "rate": round(rate, 2), "anchor": anchor_rate, "flag": "ok"}
    factor = anchor_rate / rate  # 慢行增量倍率 (当前速率 → 锚)
    try:
        # apply_atempo 是从原始 take (accel_bak) 按**绝对倍率**成型 —
        # 手点/前次机制写过的行 sidecar 已存绝对档, 必须接力累乘,
        # 否则用增量档当绝对档会把已有档位改小 = 手调被改慢 (1118 实测 000 行 286→280)。
        _f_old = float(json.loads(
            wav.with_suffix(".tempo.json").read_text(encoding="utf-8"))["factor"])
    except Exception:  # noqa: BLE001 — 无 sidecar = 原始 take
        _f_old = 1.0
    needed = _f_old * factor
    if needed > FMAX:
        # 顶帽尽力拉 (与手点 ⏩ 的 FMAX 封顶同域), 标 capped 提示重roll/回听
        factor, capped = FMAX, True
    else:
        factor, capped = round(needed, 4), False
    new_dur = apply_atempo(wav, factor)
    new_rate = measure_rate(text, new_dur) or rate
    return {"changed": True, "rate": round(new_rate, 2), "anchor": anchor_rate,
            "flag": "done", "factor": round(factor, 3), "dev": f"{dev:+.0%}",
            "duration": new_dur, "capped": capped}
