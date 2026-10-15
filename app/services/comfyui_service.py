"""ComfyUI service — manifest 装载 + prompt 注入 + sha256 校验.

从 `app/routers/comfyui.py` 下沉的纯逻辑 (不依赖 FastAPI).
提交/轮询/产物落盘在 `app/services/comfyui_client.py`
(错误协议: 返回 dict + 英文消息, 与 dhv 用的 raise-RuntimeError 客户端区分).
本模块负责 workflow 装配 (load manifest / load json / inject inputs).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import yaml

from app.services.comfyui_client import submit_and_wait  # noqa: F401 — router 经 comfyui_service.submit_and_wait 访问

logger = logging.getLogger(__name__)

__all__ = [
    "WORKFLOWS_DIR",
    "MANIFEST_PATH",
    "load_manifest",
    "find_workflow",
    "load_workflow_json",
    "inject_inputs",
    "submit_and_wait",
    "sha256",
]

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "workflows"
MANIFEST_PATH = WORKFLOWS_DIR / "manifest.yaml"


def load_manifest() -> list[dict[str, Any]]:
    """读 manifest.yaml → workflow 列表."""
    if not MANIFEST_PATH.exists():
        logger.warning("manifest.yaml not found at %s", MANIFEST_PATH)
        return []
    raw = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
    wf_list = raw.get("workflows", [])
    return wf_list if isinstance(wf_list, list) else []


def find_workflow(name: str) -> dict[str, Any] | None:
    """按 name 找 manifest 条目."""
    for wf in load_manifest():
        if wf.get("name") == name:
            return wf
    return None


def load_workflow_json(source: str) -> dict[str, Any]:
    """读 workflows/<source>.json."""
    src_path = WORKFLOWS_DIR / source
    if not src_path.exists():
        raise FileNotFoundError(f"Workflow JSON not found: {src_path}")
    return json.loads(src_path.read_text(encoding="utf-8"))


def _detect_prompt_nodes(workflow: dict[str, Any]) -> tuple[list[int], list[int]]:
    """扫一遍 workflow nodes, 把 CLIPTextEncode 节点按方向分类.

    启发式:
    - class_type == "CLIPTextEncode"
    - title 含 negative/neg → 负向 (保持空)
    - 其余 → 正向 (灌 description)

    注: 当前 character_three_view.json 的 8 个 CLIPTextEncode 全部为空;
    manifest.yaml.output_view_order 决定产物顺序.
    """
    pos: list[int] = []
    neg: list[int] = []
    for node in workflow.get("nodes", []):
        if node.get("type") != "CLIPTextEncode":
            continue
        nid = node.get("id")
        if nid is None:
            continue
        title = (node.get("title") or "").lower()
        if "negative" in title or "neg" in title:
            neg.append(int(nid))
        else:
            pos.append(int(nid))
    return pos, neg


def _inject_description(
    workflow: dict[str, Any], pos_ids: list[int], description: str
) -> None:
    """description → 正向 CLIPTextEncode 节点 widgets_values[0]."""
    for node in workflow.get("nodes", []):
        if node.get("id") not in pos_ids:
            continue
        wv = node.get("widgets_values") or ["", ""]
        # widgets_values[0] = text, [1] = 通常是某种 flag
        if len(wv) >= 1:
            wv[0] = description
        else:
            wv = [description]
        node["widgets_values"] = wv


def _inject_reference_image(
    workflow: dict[str, Any], reference_image: str
) -> None:
    """reference_image → 第一个 LoadImage/ImageLoader 的 image 输入."""
    for node in workflow.get("nodes", []):
        if node.get("type") not in ("LoadImage", "ImageLoader"):
            continue
        inputs_dict = node.get("inputs") or []
        for inp in inputs_dict:
            if inp.get("name") in ("image", "path", "file"):
                inp["value"] = reference_image
        node["inputs"] = inputs_dict


def _inject_seed(workflow: dict[str, Any], seed: int) -> None:
    """seed → 所有 KSampler 节点的 seed (widgets_values[0])."""
    for node in workflow.get("nodes", []):
        if node.get("type") != "KSampler":
            continue
        wv = node.get("widgets_values") or []
        # KSampler.widgets_values 顺序: seed, steps, cfg, sampler_name, scheduler, denoise
        if len(wv) >= 1:
            wv[0] = seed
        else:
            wv.insert(0, seed)
        node["widgets_values"] = wv


def inject_inputs(workflow: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """注入 inputs 到 workflow 的深拷贝.

    支持 keys: description / reference_image / seed.
    """
    wf = json.loads(json.dumps(workflow))  # deep copy
    description = inputs.get("description")
    reference_image = inputs.get("reference_image")
    seed = inputs.get("seed")

    pos_ids, _neg_ids = _detect_prompt_nodes(wf)
    if description is not None:
        _inject_description(wf, pos_ids, str(description))
    if reference_image is not None:
        _inject_reference_image(wf, str(reference_image))
    if seed is not None:
        _inject_seed(wf, int(seed))
    return wf


def sha256(p: Path) -> str | None:
    """流式 sha256 (缺文件返回 None)."""
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
