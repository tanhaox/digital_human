"""TTS 网络传输工具: msgpack 打包 + urllib POST.

保留 requests 可选依赖的语义 (存在时优先用, 否则回退 urllib),
与旧 tts_client 行为完全一致。
"""
from __future__ import annotations

import base64
import json
import urllib.request
from typing import Any

# Fish Speech uses msgpack; try the fast implementation first, fall back to msgpack.
try:
    import ormsgpack as _packer
except Exception:  # pragma: no cover
    import msgpack as _packer  # type: ignore[no-redef]

# requests is optional; urllib is the fallback.
try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

__all__ = ["_pack_msgpack", "_http_post_bytes", "_http_post_json", "requests"]


def _pack_msgpack(payload: dict[str, Any]) -> bytes:
    if hasattr(_packer, "packb"):
        return _packer.packb(payload)  # type: ignore[union-attr]
    return _packer.pack(payload)


def _http_post_bytes(url: str, data: bytes, headers: dict, timeout: int = 300) -> bytes:
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_post_json(url: str, payload: dict, timeout: int = 300) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
