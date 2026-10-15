# -*- coding: utf-8 -*-
"""tools.i2v — Wan2.2 意象图生视频, 系统级常备工具 (2026-08-27).

来源: E:/AI/ComfyUI_windows_portable/wan22_i2v_test.py (体外测试脚本) 收编 —
区别: 走 GPU 服务托管 (gpu_service_manager "comfyui" 后端, 排队+按需拉起+
空闲自动关停腾显存), 不再要求手工先开 ComfyUI。提示词消费 tools/i2v_prompt
(Hell Grind 骨架), 生成后可 --qc 自动质检+迭代记录 (sandbox/i2v_iteration)。

工作流范式照抄官方 wan22/image_to_video_wan22_5B.json:
首帧嵌 LATENT (Wan22ImageToVideoLatent) · 30步/cfg5/uni_pc/simple/shift8 ·
1280×704×81帧@16fps (兵器谱定稿, ~150s/条)。

用法 (CLI):
  python -m tools.i2v <图片|none> "<运动提示词>" [前缀] [--bright] [--length 121] [--seed 7]
  python -m tools.i2v <图片> --contract contract.json mytask      # 契约驱动提示词
  python -m tools.i2v <图片> "<提示词>" mytask --task-id t1 --batch 1   # 生成+自动QC+迭代记录

用法 (代码):
  from tools.i2v import generate
  r = generate("start.png", motion="光点沿重力倾泻", prefix="test", seed=7)
  r["video_path"], r["seconds"]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.i2v_prompt import AB_SET, NEG_BASE, NEG_BRIGHT, build_prompt, targeted_negs

logger = logging.getLogger(__name__)

BASE = "http://127.0.0.1:8188"
INPUT_DIR = Path("E:/AI/ComfyUI_windows_portable/ComfyUI/input")
OUTPUT_ROOT = Path("E:/AI/ComfyUI_windows_portable/ComfyUI/output")
W, H, LENGTH = 1280, 704, 81  # 官方最优对齐: 32步进 (720 非法)


# ── 首帧准备 (照抄体外脚本, PIL 居中裁 16:9 → 1280×704) ────────────────
def prep_image(src_path: str, prefix: str) -> str:
    from PIL import Image

    im = Image.open(src_path).convert("RGB")
    tw, th = im.size
    target = W / H
    if tw / th > target:
        nw = int(th * target)
        x0 = (tw - nw) // 2
        im = im.crop((x0, 0, x0 + nw, th))
    else:
        nh = int(tw / target)
        y0 = (th - nh) // 2
        im = im.crop((0, y0, tw, y0 + nh))
    im = im.resize((W, H), Image.LANCZOS)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    fn = f"{prefix}_start.png"
    im.save(INPUT_DIR / fn)
    logger.info("[i2v] 首帧 %s %dx%d → input/%s", src_path, tw, th, fn)
    return fn


def _widget_options(node_def: dict, input_name: str) -> list:
    spec = node_def["input"]["required"][input_name]
    if isinstance(spec[0], str) and spec[0] == "COMBO":
        return spec[1].get("options", [])
    if isinstance(spec[0], list):
        return spec[0]
    return []


def discover() -> tuple[str | None, str | None, str | None]:
    oi = json.loads(urllib.request.urlopen(BASE + "/object_info", timeout=30).read())
    unets = _widget_options(oi["UNETLoader"], "unet_name")
    clips = _widget_options(oi["CLIPLoader"], "clip_name")
    vaes = _widget_options(oi["VAELoader"], "vae_name")
    unet = next((u for u in unets if "ti2v_5B" in u), None)
    clip = next((c for c in clips if "umt5_xxl_fp8" in c), None)
    vae = (next((v for v in vaes if "wan2.2_vae" in v), None)
           or next((v for v in vaes if "2.2" in v and "vae" in v.lower()), None))
    return unet, clip, vae


def build_wf(motion_prompt: str, img_fn: str | None, unet: str, clip: str, vae: str,
             prefix: str, seed: int = 42, bright: bool = False,
             length: int | None = None, negative: str | None = None) -> dict:
    """官方 wan22 5B ti2v 范式; negative 缺省用实测基线 (可注入针对性负向)."""
    neg = negative or (NEG_BRIGHT if bright else NEG_BASE)
    latent_inputs: dict[str, Any] = {
        "vae": ["3", 0], "width": W, "height": H, "length": length or LENGTH, "batch_size": 1}
    if img_fn:
        latent_inputs["start_image"] = ["11", 0]
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "wan", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"shift": 8.0, "model": ["1", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": motion_prompt, "clip": ["2", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["2", 0]}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": latent_inputs},
        "8": {"class_type": "KSampler", "inputs": {
            "seed": seed, "control_after_generate": "fixed",
            "steps": 30, "cfg": 5.0, "sampler_name": "uni_pc", "scheduler": "simple",
            "denoise": 1.0, "model": ["4", 0], "positive": ["5", 0],
            "negative": ["6", 0], "latent_image": ["7", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": 16}},
        "12": {"class_type": "SaveVideo", "inputs": {
            "video": ["10", 0], "format": "mp4", "codec": "h264",
            "filename_prefix": f"video/{prefix}"}},
    }
    if img_fn:
        wf["11"] = {"class_type": "LoadImage", "inputs": {"image": img_fn}}
    return wf


def _submit_and_wait(wf: dict, notify=print, poll_sec: float = 10.0) -> dict:
    """提交工作流 → 轮询 history → 返回 {ok, files[], seconds, error?}."""
    data = json.dumps({"prompt": wf}).encode("utf-8")
    req = urllib.request.Request(BASE + "/prompt", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        pid = json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "提交失败: " + e.read().decode("utf-8", "replace")[:500]}
    notify(f"已提交 prompt_id={pid} | {wf['7']['inputs'].get('length', LENGTH)}帧@16fps 等待生成…")
    t0 = time.time()
    while True:
        time.sleep(poll_sec)
        try:
            q = json.loads(urllib.request.urlopen(BASE + f"/history/{pid}", timeout=30).read())
        except Exception:
            continue
        if pid not in q:
            notify(f"  [{time.time()-t0:.0f}s] 排队/执行中…")
            continue
        entry = q[pid]
        if entry.get("status", {}).get("completed"):
            # SaveVideo 输出键为 "images" (老脚本只查 gifs/videos 漏取, 2026-08-27 修)
            outs = [v for o in entry["outputs"].values()
                    for v in (o.get("images") or o.get("gifs") or o.get("videos") or [])]
            files = [str(OUTPUT_ROOT / o.get("subfolder", "") / o["filename"])
                     for o in outs if o.get("filename")]
            return {"ok": True, "files": files, "seconds": round(time.time() - t0)}
        serr = entry.get("status", {})
        if serr.get("status_str") == "error":
            return {"ok": False, "seconds": round(time.time() - t0),
                    "error": "执行失败: " + json.dumps(serr.get("messages", []),
                                                        ensure_ascii=False)[:400]}
        # status_str=success 但无输出 (节点被跳过) 也当失败
        if serr.get("status_str") == "success" and not any(
                o for o in entry.get("outputs", {}).values()):
            return {"ok": False, "seconds": round(time.time() - t0), "error": "无输出文件"}


def generate(
    image: str | None,
    motion: str,
    prefix: str = "i2v",
    *,
    seed: int = 42,
    bright: bool = False,
    length: int | None = None,
    negative: str | None = None,
    notify=print,
) -> dict:
    """托管入口: GPU 服务 session 自动拉起 ComfyUI → 生成 → 释放显存.

    Returns: {ok, video_path?, files, seconds, error?}
    """
    from app.services.gpu_service_manager import get_gpu_service_manager

    # CLI 直跑 (无 FastAPI lifespan): 懒加载配置 — manager 依赖 config.raw
    try:
        get_gpu_service_manager()
    except RuntimeError:
        from app.config import load_config, set_config
        set_config(load_config())

    manager = get_gpu_service_manager()
    with manager.session("comfyui", status_callback=notify):
        unet, clip, vae = discover()
        if not (unet and clip and vae):
            return {"ok": False, "error": f"模型不齐: unet={unet} clip={clip} vae={vae}"}
        img_fn = prep_image(image, prefix) if image else None
        wf = build_wf(motion, img_fn, unet, clip, vae, prefix,
                      seed=seed, bright=bright, length=length, negative=negative)
        result = _submit_and_wait(wf, notify=notify)
    result["video_path"] = result["files"][0] if result.get("files") else None
    return result


def generate_from_contract(contract: dict, image: str | None, prefix: str = "i2v", **kw) -> dict:
    """契约驱动: 风格前缀 + build_prompt + 亮度锚 + 针对性负向 → generate.

    亮度链 (2026-08-27, 用户实测"总是这么暗"修): 契约 brightness 非暗调 →
    负面词换亮调手术版 (NEG_BRIGHT: 去"色调艳丽"压亮度项 + 加过暗死黑) +
    正向追加单亮度锚 — 兵器谱定稿做法接入契约链。
    风格链 (同日, 用户定稿"示意了非要弄成真的=较劲"): style 缺省示意 →
    非写实前缀 + 写实负面 — AI 意象放弃写实较劲, 真实画面走素材API管线。
    """
    from tools.i2v_prompt import (NEG_BASE, NEG_BRIGHT, brightness_anchor,
                                  style_directives)

    style_prefix, style_negs = style_directives(contract)
    anchor = brightness_anchor(contract)
    pos = "，".join(p for p in (style_prefix, build_prompt(contract), anchor) if p)
    negs = targeted_negs(contract) + style_negs
    neg = (NEG_BRIGHT if anchor else NEG_BASE) + ("，" + "，".join(negs) if negs else "")
    return generate(image, pos, prefix, negative=neg, **kw)


def _qc_and_log(task_id: str, batch: int, video: str, seed, prompt_ver: str) -> None:
    """生成后自动 QC + 迭代记录 (sandbox/i2v_iteration, 沙盒工具路径 shim)."""
    import importlib.util
    p = ROOT / "sandbox" / "i2v_iteration.py"
    spec = importlib.util.spec_from_file_location("i2v_iteration", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    qc = mod._qc_video(video)
    mod.append_log({"task_id": task_id, "batch": batch, "seed": seed,
                    "prompt_ver": prompt_ver, "video": video, "qc": qc,
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    print(f"[qc] pass={qc['pass']} codes={qc['codes']} {qc['note']}")
    v = mod.verdict(task_id)
    for a in v["stop_advice"]:
        print(f"⛔ {a['code']} → {a['action']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Wan2.2 意象 i2v (系统托管)")
    ap.add_argument("image", help="首帧图片路径; none=纯 t2v")
    ap.add_argument("motion", help="运动提示词 (或 --contract 时忽略)")
    ap.add_argument("prefix", nargs="?", default="i2v")
    ap.add_argument("--bright", action="store_true", help="亮调负面词")
    ap.add_argument("--length", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--contract", help="契约 JSON 文件路径 (驱动正向+针对性负向)")
    ap.add_argument("--task-id", help="生成后自动 QC + 迭代记录 (配合 --batch)")
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--prompt-ver", default="v1")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    img = None if args.image == "none" else args.image
    if args.contract:
        contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        r = generate_from_contract(contract, img, args.prefix,
                                   seed=args.seed, bright=args.bright, length=args.length)
    else:
        r = generate(img, args.motion, args.prefix,
                     seed=args.seed, bright=args.bright, length=args.length)
    if not r["ok"]:
        print("❌", r.get("error"))
        return 1
    print(f"✅ 完成 ({r['seconds']}s): {r['video_path']}")
    if args.task_id and r["video_path"]:
        _qc_and_log(args.task_id, args.batch, r["video_path"], args.seed, args.prompt_ver)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
