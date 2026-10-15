# -*- coding: utf-8 -*-
"""tools.vision — 本地图片/视频帧识别 (系统级常备工具, 2026-08-27).

来源: 视频库打标流程 (app/services/video_tagging_service) 微改抽独立 —
拉起复用 _ensure_llama_server (health → spawn → ready), 判定复用
LocalLLMClient.chat_with_images (base64 多图 + OpenAI 兼容). 零重复代码。

视觉升级(剪辑)线的常备入口: 参考片拆解 / 帧审片 / 任何"看图说话"场景。

用法 (代码):
    from tools.vision import analyze, frames_from_video
    text = analyze(["frame1.png"], "描述画面动效")
    frames = frames_from_video("ref.mp4", times=[5, 12, 30])

用法 (CLI):
    python -m tools.vision a.png b.png -p "描述这两帧差异"
    python -m tools.vision ref.mp4 --times 5,12,30 -p "逐帧描述"
    python -m tools.vision ref.mp4 --every 30 -p "逐帧描述"     # 每30s一帧
    python -m tools.vision a.png --json -p "输出JSON: {scene, text_on_screen}"
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("tools.vision")

# ffmpeg 同项目沙盒约定 (缺失时按 PATH 找)
_FFMPEG = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"


def _client():
    """惰性初始化: 确保视觉服务在跑 + 返回 LLM 客户端 (CLI 场景自动 load config)."""
    from app.config import get_config, load_config, set_config
    from app.services.local_llm_client import LocalLLMClient
    from app.services.video_tagging_service.llama import _ensure_llama_server

    try:
        cfg = get_config()
    except RuntimeError:  # 进程外直接调用 (CLI/脚本), lifespan 未跑
        set_config(load_config())
        cfg = get_config()
    if not _ensure_llama_server():
        raise RuntimeError("llama-server 无法启动 (E:/Llama-cpp-12, 端口 8080)")
    return LocalLLMClient(cfg.local_llm)


def analyze(
    image_paths: list[str | Path],
    prompt: str,
    *,
    system_prompt: str = "你是图像分析助手。基于给出的图片精确回答, 不要编造看不见的内容。",
    max_images: int = 4,
) -> str:
    """识别图片 → 模型原始文本. 每次最多 4 张 (llama 上下文约束, 打标实测)."""
    paths = [Path(p) for p in image_paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"图片不存在: {missing}")
    if len(paths) > max_images:
        logger.warning("图片超 %d 张, 截断 (llama 上下文约束)", max_images)
        paths = paths[:max_images]
    client = _client()
    return client.chat_with_images(paths, system_prompt, prompt)


def analyze_json(
    image_paths: list[str | Path],
    prompt: str,
    *,
    max_images: int = 4,
) -> dict | list:
    """识别图片并强制解析为 JSON (prompt 须自带 schema 说明). 解析失败返回
    {"_raw": 原始文本} 不抛异常 (分析场景宁可降级不中断)."""
    text = analyze(image_paths, prompt, max_images=max_images)
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    if not m:
        return {"_raw": text}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"_raw": text}


def frames_from_video(
    video: str | Path,
    *,
    times: list[float] | None = None,
    every: float | None = None,
    out_dir: str | Path | None = None,
) -> list[Path]:
    """从视频抽帧 → JPEG 路径列表 (临时目录, 随用随抽).

    times: 指定秒列表; every: 每 N 秒一帧. 两者给其一。
    """
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(f"视频不存在: {video}")
    if not times and not every:
        raise ValueError("times / every 必须给其一")
    if out_dir:
        outdir = Path(out_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        keep = True
    else:
        outdir = Path(tempfile.mkdtemp(prefix="vision_frames_"))
        keep = False
    ff = _FFMPEG if Path(_FFMPEG).exists() else "ffmpeg"
    frames: list[Path] = []
    if times:
        for t in times:
            p = outdir / f"frame_{t:g}s.jpg"
            subprocess.run(
                [ff, "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video),
                 "-frames:v", "1", "-q:v", "3", str(p)],
                check=True, capture_output=True)
            frames.append(p)
    else:
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-i", str(video),
             "-vf", f"fps=1/{every},q:v=3", str(outdir / "frame_%05d.jpg")],
            check=True, capture_output=True)
        frames = sorted(outdir.glob("frame_*.jpg"))
    if not keep:
        logger.info("抽帧 %d 张 (临时目录, 进程内复用): %s", len(frames), outdir)
    return frames


def analyze_video_at(
    video: str | Path,
    times: list[float],
    prompt: str,
    *,
    batch: int = 4,
) -> list[dict | str]:
    """视频指定时刻 → 分批识别 (每批 4 帧, llama 约束). 返回逐批结果."""
    frames = frames_from_video(video, times=times)
    results: list[dict | str] = []
    for i in range(0, len(frames), batch):
        chunk = frames[i:i + batch]
        label = ", ".join(f.stem.replace("frame_", "").replace("s", "s ") for f in chunk)
        results.append(analyze(chunk, f"[这批帧对应时刻: {label}] {prompt}"))
    return results


def _cli() -> None:
    ap = argparse.ArgumentParser(prog="tools.vision", description="本地图片/视频帧识别")
    ap.add_argument("inputs", nargs="+", help="图片路径, 或单个视频路径 (配合 --times/--every)")
    ap.add_argument("-p", "--prompt", required=True, help="识别指令")
    ap.add_argument("--times", help="视频抽帧时刻, 逗号分隔秒数")
    ap.add_argument("--every", type=float, help="视频每 N 秒抽一帧")
    ap.add_argument("--json", action="store_true", help="强制 JSON 输出")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    inputs = [Path(x) for x in args.inputs]
    is_video = len(inputs) == 1 and inputs[0].suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")

    if is_video and (args.times or args.every):
        times = [float(t) for t in args.times.split(",")] if args.times else None
        frames = frames_from_video(inputs[0], times=times, every=args.every)
        print(f"[抽帧 {len(frames)} 张]")
        out = []
        for i in range(0, len(frames), 4):
            chunk = frames[i:i + 4]
            label = ", ".join(f.stem.replace("frame_", "") for f in chunk)
            r = (analyze_json if args.json else analyze)(
                chunk, f"[本批帧时刻: {label}] {args.prompt}")
            out.append(r)
            print(f"--- 帧 {label} ---")
            print(json.dumps(r, ensure_ascii=False, indent=1)
                  if isinstance(r, (dict, list)) else r)
        return

    fn = analyze_json if args.json else analyze
    r = fn(inputs, args.prompt)
    print(json.dumps(r, ensure_ascii=False, indent=1) if isinstance(r, (dict, list)) else r)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _cli()
