# -*- coding: utf-8 -*-
"""whisper_worker — faster-whisper 独立子进程 (1023 4090 双 AI 上卡事故根治).

背景: 对齐/TTS校验把 whisper large-v3 float32 (~6.2G) 钉死 4090 且 _MODEL_CACHE
永久常驻, H3 批生成 (ComfyUI 同卡) 被挤压降速 20-30% (1023 ep3 实锤)。
本 worker: 常驻 (突发连发省 70s 加载费) + **空闲 IDLE_SEC 自杀** (进程退出即释放
显存) — 后端经 alignment_service._worker 客户端调用, worker 挂了由客户端回退
进程内旧路径 (fail-open)。

协议: stdin/stdout 各一行 JSON。
  请求: {"id": int, "audio": str, "language": "zh", "vad_filter": true,
         "initial_prompt": str?, "size": str?, "device": str?}
  响应: {"id": int, "ok": true, "segments": [{"text","start","end"}, ...]}
        {"id": int, "ok": false, "error": str}
EOF 或空闲超时 → exit(0)。

用法 (由 _worker.py 拉起, 一般不手跑):
  .venv/Scripts/python scripts/whisper_worker.py [idle_sec]
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_config, set_config  # noqa: E402

IDLE_SEC = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0


def _log(msg: str) -> None:
    print(f"[whisper-worker] {msg}", file=sys.stderr, flush=True)


def _reader(stdin_lines: "queue.Queue[str | None]") -> None:
    """stdin 逐行搬运线程 — Windows 管道无 select, 主循环靠 queue 空闲超时."""
    for line in sys.stdin:
        stdin_lines.put(line)
    stdin_lines.put(None)  # EOF


def main() -> None:
    set_config(load_config())  # standalone 脚本无 lifespan
    from app.services.alignment_service._models import _ensure_model

    model = None  # 懒加载: 首请求才上卡
    lines: "queue.Queue[str | None]" = queue.Queue()
    threading.Thread(target=_reader, args=(lines,), daemon=True).start()
    _log(f"ready (idle_exit={IDLE_SEC:.0f}s, model=lazy)")

    req_id = 0
    while True:
        try:
            line = lines.get(timeout=IDLE_SEC)
        except queue.Empty:
            _log(f"idle {IDLE_SEC:.0f}s, exit (显存随进程释放)")
            return
        if line is None:
            _log("stdin EOF, exit")
            return
        req_id += 1
        try:
            req = json.loads(line)
            audio = str(req["audio"])
        except Exception as exc:  # noqa: BLE001 — 协议坏行不杀进程
            print(json.dumps({"id": req_id, "ok": False,
                              "error": f"bad request: {exc}"}), flush=True)
            continue
        t0 = time.time()
        try:
            if model is None:
                _log("loading whisper (首次约 70s)…")
            model = _ensure_model(
                req.get("size"), req.get("device"))
            segments, _info = model.transcribe(
                audio,
                language=req.get("language", "zh"),
                word_timestamps=False,        # CTranslate2 4.6.0 workaround (同 _transcribe)
                vad_filter=bool(req.get("vad_filter", True)),
                vad_parameters={"threshold": 0.5, "min_speech_duration_ms": 250,
                                "max_speech_duration_s": 30.0},
                initial_prompt=req.get("initial_prompt"),
            )
            out = [{"text": (s.text or "").strip(), "start": float(s.start),
                    "end": float(s.end)}
                   for s in segments if (s.text or "").strip()
                   and s.start is not None and s.end is not None]
            print(json.dumps({"id": req_id, "ok": True, "segments": out,
                              "secs": round(time.time() - t0, 1)}), flush=True)
            _log(f"req#{req_id} {Path(audio).name} → {len(out)} segs "
                 f"({time.time() - t0:.1f}s)")
        except Exception as exc:  # noqa: BLE001 — 单请求失败不杀进程
            print(json.dumps({"id": req_id, "ok": False,
                              "error": f"{type(exc).__name__}: {exc}"}), flush=True)
            _log(f"req#{req_id} failed: {exc}")


if __name__ == "__main__":
    main()
