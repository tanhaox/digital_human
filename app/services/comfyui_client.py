"""ComfyUI HTTP 客户端 — /prompt 提交 + /history 轮询 + 产物落盘.

从 `app/services/dhv_service.py` 与 `app/services/comfyui_service.py` 拆分
(为满足 ≤250 行). 错误消息逐字保留.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = ["submit_prompt", "poll_history", "submit_and_wait", "persist_outputs"]

_COMFY_OUTPUT = Path(r"E:/AI/ComfyUI_windows_portable/ComfyUI/output")
_COMFY_INPUT = Path(r"E:/AI/ComfyUI_windows_portable/ComfyUI/input")
_COMFY_TEMP = Path(r"E:/AI/ComfyUI_windows_portable/ComfyUI/temp")


def _output_base(t: str) -> Path:
    """ComfyUI 产物 type → 本地根目录."""
    if t == "input":
        return _COMFY_INPUT
    if t == "temp":
        return _COMFY_TEMP
    return _COMFY_OUTPUT


def persist_outputs(
    outputs: dict[str, Any],
    out_dir: Path,
    view_order: list[str],
) -> dict[str, str]:
    """把 ComfyUI /history outputs 落盘到 out_dir/<view>.png.

    outputs 结构: {node_id: {"images": [{"filename": "xxx.png", "subfolder": "...", "type": "output"}]}}
    按节点顺序 → view_order 顺序对应, 拷贝到 out_dir/<view>.png.
    返回 {view_name: 绝对路径}.
    """
    all_images: list[Path] = []
    for nid, payload in outputs.items():
        imgs = payload.get("images") or []
        for img in imgs:
            fn = img.get("filename")
            sub = img.get("subfolder", "")
            t = img.get("type", "output")
            if not fn:
                continue
            base = _output_base(t)
            src = base / sub / fn if sub else base / fn
            if src.exists():
                all_images.append(src)

    views: dict[str, str] = {}
    for i, src in enumerate(all_images[: len(view_order)]):
        view_name = view_order[i]
        ext = src.suffix or ".png"
        dest = out_dir / f"{view_name}{ext}"
        try:
            os.replace(str(src), str(dest))
        except OSError:
            shutil.copy2(str(src), str(dest))
            src.unlink(missing_ok=True)
        views[view_name] = str(dest)
    return views


async def submit_and_wait(
    workflow_payload: dict[str, Any],
    role_id: str | None,
    base_url: str,
    timeout_sec: int,
    output_view_order: list[str] | None = None,
) -> dict[str, Any]:
    """提交 → 轮询 /history/{prompt_id} → 落盘 views.

    返回:
        {"prompt_id": str, "status": "completed"|"failed"|"timeout",
         "views": {"front": "...", "side": "...", "full": "..."},
         "elapsed_sec": float, "error": str|None}
    """
    from app.config import get_config

    cfg = get_config()
    roles_root = Path(cfg.defaults.roles_output_root)
    out_dir = roles_root / role_id if role_id else roles_root / "ad-hoc"
    out_dir.mkdir(parents=True, exist_ok=True)

    view_order = output_view_order or ["front", "side", "full"]

    t0 = time.time()
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. POST /prompt — ComfyUI 协议要求 body = {"prompt": <wf>, "client_id": <uuid>}
        client_id = str(uuid.uuid4())
        try:
            resp = await client.post(
                f"{base_url.rstrip('/')}/prompt",
                json={"prompt": workflow_payload, "client_id": client_id},
            )
        except httpx.RequestError as exc:
            return {
                "prompt_id": None,
                "status": "failed",
                "views": {},
                "elapsed_sec": 0.0,
                "error": f"ComfyUI unreachable at {base_url}: {exc}",
            }
        if resp.status_code >= 400:
            return {
                "prompt_id": None,
                "status": "failed",
                "views": {},
                "elapsed_sec": 0.0,
                "error": f"ComfyUI /prompt HTTP {resp.status_code}: {resp.text[:300]}",
            }
        try:
            prompt_id = resp.json()["prompt_id"]
        except (KeyError, ValueError) as exc:
            return {
                "prompt_id": None,
                "status": "failed",
                "views": {},
                "elapsed_sec": 0.0,
                "error": f"ComfyUI /prompt malformed response: {exc}; body={resp.text[:200]}",
            }

        logger.info(
            "[comfyui] submitted prompt_id=%s role_id=%s client_id=%s",
            prompt_id, role_id, client_id,
        )

        # 2. 轮询 /history/{prompt_id}
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            await asyncio.sleep(2.0)
            try:
                h = await client.get(f"{base_url.rstrip('/')}/history/{prompt_id}")
            except httpx.RequestError:
                continue
            if h.status_code >= 400:
                continue
            history = h.json() or {}
            entry = history.get(prompt_id)
            if not entry:
                continue
            status_dict = entry.get("status") or {}
            if status_dict.get("completed"):
                outputs = entry.get("outputs") or {}
                views = persist_outputs(outputs, out_dir, view_order)
                elapsed = time.time() - t0
                return {
                    "prompt_id": prompt_id,
                    "status": "completed",
                    "views": views,
                    "elapsed_sec": elapsed,
                    "error": None,
                }
            if status_dict.get("errored"):
                err = (entry.get("status") or {}).get("error") or "unknown error"
                return {
                    "prompt_id": prompt_id,
                    "status": "failed",
                    "views": {},
                    "elapsed_sec": time.time() - t0,
                    "error": str(err)[:500],
                }

        return {
            "prompt_id": prompt_id,
            "status": "timeout",
            "views": {},
            "elapsed_sec": time.time() - t0,
            "error": f"timeout after {timeout_sec}s",
        }


async def submit_prompt(base_url: str, workflow: dict[str, Any]) -> tuple[str, str]:
    """POST ComfyUI /prompt, 返回 (prompt_id, client_id)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        client_id = str(uuid.uuid4())
        try:
            resp = await client.post(
                f"{base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
            )
        except httpx.RequestError as exc:
            raise RuntimeError(f"ComfyUI 不可达: {exc}") from exc
        if resp.status_code >= 400:
            raise RuntimeError(
                f"ComfyUI /prompt HTTP {resp.status_code}: {resp.text[:300]}"
            )
        try:
            prompt_id = resp.json()["prompt_id"]
        except (KeyError, ValueError) as exc:
            raise RuntimeError(f"ComfyUI 响应异常: {exc}") from exc
    return prompt_id, client_id


async def poll_history(
    base_url: str, prompt_id: str, timeout_sec: int
) -> dict[str, Any]:
    """轮询 /history/{prompt_id} 直到 completed/errored/超时. 返回 history entry."""
    deadline = time.time() + timeout_sec
    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.time() < deadline:
            await asyncio.sleep(2.0)
            try:
                h = await client.get(f"{base_url}/history/{prompt_id}")
            except httpx.RequestError:
                continue
            if h.status_code >= 400:
                continue
            entry = (h.json() or {}).get(prompt_id)
            if not entry:
                continue
            status_dict = entry.get("status") or {}
            if status_dict.get("completed"):
                return entry
            if status_dict.get("errored"):
                raise RuntimeError(
                    f"ComfyUI task errored: {(status_dict.get('error') or 'unknown')[:500]}"
                )
    raise RuntimeError(f"ComfyUI task timeout after {timeout_sec}s")
