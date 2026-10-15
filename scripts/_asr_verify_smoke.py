# -*- coding: utf-8 -*-
"""一次性冒烟 (2026-09-03): 真实音频 + 真 whisper 回听, dry-run (resynth 抛异常
→ 回滚 → 全部疑似进 unresolved), 验证 ASR 回听校验在真数据上的噪声水平。
用法: python scripts/_asr_verify_smoke.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config import load_config, set_config

set_config(load_config())

from app.services.tts_verify import verify_pronunciation, _whisper_transcribe

AUDIO_DIR = Path("E:/数字人计划/ppt/cb3373ba-c8fd-4f7b-bd38-7cc77f16952e/audio")


def _dry_resynth(idx: int, clean: str, marked: str) -> None:
    raise RuntimeError("dry-run 不重合成")


report = verify_pronunciation(
    output_dir=AUDIO_DIR,
    manifest=json.loads((AUDIO_DIR / "manifest.json").read_text(encoding="utf-8")),
    resynth_line=_dry_resynth,
    on_event=lambda m, lv: print(f"[{lv}] {m}"),
    transcriber=_whisper_transcribe,
)

print("\n==== 汇总 ====")
print(f"checked={report['checked']} suspect={len(report['suspect_lines'])} "
      f"fixed={len(report['fixed'])} unresolved={len(report['unresolved'])}")
for u in report["unresolved"]:
    ds = ", ".join(f"{d['char']}({d['expect']}→{d['heard'] or '吞'})" for d in u["diffs"])
    print(f"  行{u['index']:>3} [{u['reason'][:24]}] {u['text'][:30]} | {ds}")
