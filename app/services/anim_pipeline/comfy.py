# -*- coding: utf-8 -*-
"""ComfyUI 同步客户端 — 动画管线 CLI 批跑用 (k2/h3).

与 app/services/comfyui_client.py (async, web 用) 分工: 本模块面向 CLI 串行批任务:
提交 → 轮询 → 产物搬运 (move, comfy 侧不留副本, 单一事实源在管线目录).
节点缓存坑: 同 prompt+seed+参数 ComfyUI 直接回旧图, 重 roll 必换 seed (调用方负责).
"""
from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .config import load

logger = logging.getLogger(__name__)

__all__ = ["health", "upload_image", "run_workflow", "ComfyRunError"]


class ComfyRunError(RuntimeError):
    """提交校验失败 / 执行报错 / 超时."""


def health() -> bool:
    cfg = load().comfy
    try:
        with httpx.Client(timeout=5.0) as cli:
            r = cli.get(f"{cfg.base_url}/system_stats")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


def free_memory() -> bool:
    """卸载 ComfyUI 已缓存模型 + 释放显存 (POST /free).

    相位边界卫生 (0915 花屏根治): K2 批装 krea2 全家后紧接着 H3 批装 fl2va,
    两套模型栈叠满 VRAM/RAM → 权重流式读取崩 (HostBuffer.read_file_slice failed)
    或静默产出噪声废片 (码率 10-12Mbps vs 健康 1-2Mbps). 每个GPU批开跑前卸干净.
    """
    cfg = load().comfy
    try:
        with httpx.Client(timeout=30.0) as cli:
            r = cli.post(f"{cfg.base_url}/free",
                         json={"unload_models": True, "free_memory": True})
        if r.status_code in (200, 204):
            logger.info("[comfy] 相位边界卸载: 模型缓存已清 (防 K2/H3 栈叠加花屏)")
            return True
        logger.warning("[comfy] /free HTTP %d: %s", r.status_code, r.text[:150])
    except httpx.HTTPError as exc:
        logger.warning("[comfy] /free 失败 (服务可能未起, 忽略): %s", exc)
    return False


def upload_image(image_path: Path) -> str:
    """上传本地图片到 ComfyUI input 目录, 返回 LoadImage 可用的文件名."""
    cfg = load().comfy
    with httpx.Client(timeout=120.0) as cli:
        with open(image_path, "rb") as f:
            r = cli.post(
                f"{cfg.base_url}/upload/image",
                files={"image": (image_path.name, f, "image/png")},
                data={"overwrite": "true"},
            )
    if r.status_code >= 400:
        raise ComfyRunError(f"upload HTTP {r.status_code}: {r.text[:300]}")
    name = r.json().get("name") or image_path.name
    logger.info("[comfy] uploaded %s -> input/%s (%.0f KB)", image_path.name, name, image_path.stat().st_size / 1024)
    return name


def _collect_files(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """history.outputs → [{kind, filename, subfolder, type}] (images/gifs/videos)."""
    found: list[dict[str, Any]] = []
    for payload in (entry.get("outputs") or {}).values():
        for key in ("images", "gifs", "videos", "audio"):
            for v in payload.get(key) or []:
                if v.get("filename"):
                    found.append({"kind": key, **v})
    return found


def _interrupt(cli: httpx.Client, cfg: Any) -> None:
    """POST /interrupt — 步间生效 (爬行镜单步可达数分钟, 非即时). 失败只警告."""
    try:
        r = cli.post(f"{cfg.base_url}/interrupt")
        if r.status_code >= 400:
            logger.warning("[comfy] /interrupt HTTP %d: %s", r.status_code, r.text[:150])
    except httpx.RequestError as exc:
        logger.warning("[comfy] /interrupt 失败 (忽略): %s", exc)


def run_workflow(
    workflow: dict[str, Any],
    *,
    dest: Path,
    timeout_sec: int | None = None,
    label: str = "",
    soft_timeout_sec: int | None = None,
    interrupt_grace_sec: int = 180,
) -> list[Path]:
    """提交工作流 → 等完成 → 把唯一产物 move 到 dest, 返回 [dest].

    预期产物数 = 1 (K2 SaveImage / H3 VHS_VideoCombine 各一个).
    超时用 config.comfy.timeout_sec (默认 900s) 或显式覆盖.
    soft_timeout_sec (0921 用户令: 3min 没交货=卡住): 到点 POST /interrupt,
    再等 interrupt_grace_sec 让中断步间落地入 history; 仍无果则弃等抛错
    (调用方 anim_fail 降级, 批队列继续 — 不陪爬行/wedge 镜干等到硬超时)。
    """
    cfg = load().comfy
    timeout = timeout_sec or cfg.timeout_sec
    dest.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=30.0) as cli:
        # 1. 提交 (ComfyUI 协议: body={"prompt": wf, "client_id": uuid})
        try:
            r = cli.post(f"{cfg.base_url}/prompt", json={"prompt": workflow, "client_id": str(uuid.uuid4())})
        except httpx.RequestError as exc:
            raise ComfyRunError(f"ComfyUI 不可达 {cfg.base_url}: {exc}") from exc
        if r.status_code >= 400:
            raise ComfyRunError(f"/prompt HTTP {r.status_code}: {r.text[:500]}")
        res = r.json()
        node_errors = res.get("node_errors") or {}
        if node_errors:
            raise ComfyRunError(f"节点校验失败: {str(node_errors)[:800]}")
        prompt_id = res["prompt_id"]
        logger.info("[comfy] submitted %s prompt_id=%s", label, prompt_id)

        # 2. 轮询 /history
        t0 = time.time()
        deadline = t0 + timeout
        soft_dl = t0 + soft_timeout_sec if soft_timeout_sec else None
        abandoned = False
        entry: dict[str, Any] | None = None
        while time.time() < deadline:
            time.sleep(4.0)
            try:
                h = cli.get(f"{cfg.base_url}/history/{prompt_id}")
            except httpx.RequestError:
                continue
            if h.status_code >= 400:
                continue
            found = (h.json() or {}).get(prompt_id)
            if not found:
                # 软超时判卡: 未交货且已过软线 → 中断并把死线收到宽限尾
                if soft_dl and not abandoned and time.time() > soft_dl:
                    abandoned = True
                    logger.warning("[comfy] %s 软超时 %.0fs 未交货 — 判卡 /interrupt (步间生效, 宽限 %ds)",
                                   label, soft_timeout_sec, interrupt_grace_sec)
                    _interrupt(cli, cfg)
                    deadline = min(deadline, time.time() + interrupt_grace_sec)
                continue
            st = found.get("status") or {}
            if st.get("completed"):
                entry = found
                break
            if st.get("errored") or any(
                m[0] in ("execution_error", "execution_interrupted") for m in st.get("messages", [])
            ):
                err = st.get("error") or str(st.get("messages", []))[:500]
                raise ComfyRunError(f"执行失败: {err}")
        if entry is None:
            if abandoned:
                raise ComfyRunError(
                    f"软超时 {soft_timeout_sec}s 判卡, /interrupt 后 {interrupt_grace_sec}s 仍未返回 ({label})"
                    " — 已弃等, 若 ComfyUI 队列仍被占需重启 ComfyUI")
            raise ComfyRunError(f"超时 {timeout}s ({label})")
        logger.info("[comfy] done %s in %.0fs", label, time.time() - t0)

    # 3. 产物搬运 (WinError 32 实锤: comfy 刚写完句柄未释放, 重试兜)
    files = _collect_files(entry)
    if len(files) != 1:
        raise ComfyRunError(f"预期 1 个产物, 实得 {len(files)}: {files}")
    item = files[0]
    base = Path(cfg.input_dir if item.get("type") == "input" else cfg.output_dir)
    sub = item.get("subfolder", "")
    src = base / sub / item["filename"] if sub else base / item["filename"]
    last_err: Exception | None = None
    for attempt in range(4):
        if not src.exists():
            last_err = FileNotFoundError(src)
        else:
            try:
                shutil.move(str(src), str(dest))
                return [dest]
            except (OSError, shutil.Error) as exc:
                if isinstance(exc, shutil.Error):
                    try:
                        shutil.copy2(str(src), str(dest))
                        src.unlink(missing_ok=True)
                        return [dest]
                    except OSError as exc2:
                        exc = exc2
                last_err = exc
        time.sleep(2.0)
    raise ComfyRunError(f"产物搬运失败 (重试 4 次): {src} -> {dest}: {last_err}")
