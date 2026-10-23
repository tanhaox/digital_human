# -*- coding: utf-8 -*-
"""whisper worker 客户端 — 常驻子进程单例 + fail-open 回退 (1023).

后端侧唯一入口: _transcribe_via_worker()。worker (scripts/whisper_worker.py)
常驻复用省 70s 加载费, 空闲自杀释放显存; 任何失败返回 None, 调用方
(_transcribe_words / tts_verify) 回退进程内旧路径 — 产线永不被此层卡死。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["_transcribe_via_worker"]

_ROOT = Path(__file__).resolve().parents[3]
_WORKER = _ROOT / "scripts" / "whisper_worker.py"


class _WorkerProc:
    """单例常驻 worker; 挂了下次调用自动重拉. 进程级锁串行化请求."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> subprocess.Popen | None:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            self._proc = subprocess.Popen(
                [sys.executable, str(_WORKER)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # worker 自志走 stderr, 别灌爆父进程
                text=True, encoding="utf-8", bufsize=1,
                cwd=str(_ROOT), env=None,
            )
            logger.info("[whisper-worker] spawned pid=%s", self._proc.pid)
            return self._proc
        except Exception as exc:  # noqa: BLE001
            logger.warning("[whisper-worker] spawn 失败 (回退进程内): %s", exc)
            self._proc = None
            return None

    def transcribe(self, req: dict) -> list[dict] | None:
        with self._lock:
            proc = self._ensure()
            if proc is None or proc.stdin is None or proc.stdout is None:
                return None
            try:
                proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[whisper-worker] 通信失败 (回退进程内): %s", exc)
                self._kill()
                return None
            if not line:  # EOF = worker 已退/已死
                logger.warning("[whisper-worker] EOF (worker 已退出, 回退进程内)")
                self._kill()
                return None
            try:
                resp = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[whisper-worker] 响应坏行 (回退进程内): %s", exc)
                self._kill()
                return None
            if not resp.get("ok"):
                logger.warning("[whisper-worker] 请求失败 (回退进程内): %s",
                               resp.get("error"))
                return None
            return resp.get("segments")

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None


_SINGLETON = _WorkerProc()


def _transcribe_via_worker(
    audio_path: Path,
    *,
    language: str = "zh",
    vad_filter: bool = True,
    initial_prompt: str | None = None,
    size: str | None = None,
    device: str | None = None,
) -> list[dict] | None:
    """worker 转录; 失败 None (调用方回退进程内 _ensure_model 旧路径)."""
    return _SINGLETON.transcribe({
        "audio": str(audio_path),
        "language": language,
        "vad_filter": vad_filter,
        "initial_prompt": initial_prompt,
        "size": size,
        "device": device,
    })
