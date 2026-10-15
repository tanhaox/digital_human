# -*- coding: utf-8 -*-
"""PPT 单页逐帧捕获 — Playwright seek-and-snap (2026-08-21).

HyperFrames determinism pattern: 每帧 t=floor(i)/fps, JS hook 把所有 CSS
animation.currentTime 设到 t 时刻并 pause (页面定格到 t), screenshot 单帧,
最后 frames_to_mp4 编码. 比 Chrome 录视频确定、不掉帧.

复用 scripts/_verify_pages.py 已验证的 chromium 路径 + sync API.
失败抛异常, 由 ppt_service.render_slide_mp4 捕获回退 static.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["capture_slide_frames", "capture_slide_png", "capture_element_png", "PLAYWRIGHT_CHROME"]

# _verify_pages.py 实测可用; 生产若无, render_slide_mp4 会回退 static.
PLAYWRIGHT_CHROME = r"C:\Users\tanhaox\AppData\Local\ms-playwright\chromium-1208\chrome-win64\chrome.exe"


def capture_slide_frames(
    html_path: Path,
    frames_dir: Path,
    duration_sec: float,
    fps: int = 25,
    width: int = 1920,
    height: int = 1080,
) -> None:
    """逐帧 seek-and-snap 截图到 frames_dir (frame_000000.png ...)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright 未安装 (pip install playwright && playwright install chromium)") from exc

    frames_dir.mkdir(parents=True, exist_ok=True)
    n_frames = max(1, int(round(duration_sec * fps)))
    chrome_args = ["--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1"]

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True, executable_path=PLAYWRIGHT_CHROME, args=chrome_args)
        except Exception as exc:
            # chromium 路径不存在时尝试默认安装
            logger.warning("[ppt-capture] 指定 chromium 路径不可用, 尝试默认: %s", exc)
            browser = p.chromium.launch(headless=True, args=chrome_args)

        try:
            ctx = browser.new_context(viewport={"width": width, "height": height},
                                      device_scale_factor=1)
            page = ctx.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.wait_for_function(
                "window.__hf && typeof window.__hf.seek === 'function'",
                timeout=15000,
            )
            page.wait_for_timeout(500)  # 字体/图片 base64 加载

            for i in range(n_frames):
                t = round(i / fps, 4)
                page.evaluate(f"window.__hf.seek({t})")
                page.screenshot(
                    path=str(frames_dir / f"frame_{i:06d}.png"),
                    clip={"x": 0, "y": 0, "width": width, "height": height},
                )
        finally:
            browser.close()

    made = len(list(frames_dir.glob("frame_*.png")))
    if made < n_frames:
        raise RuntimeError(f"帧捕获不完整: {made}/{n_frames}")
    logger.info("[ppt-capture] %s 捕获 %d 帧 (%.1fs@%dfps)",
                frames_dir.parent.name, made, duration_sec, fps)


def capture_slide_png(
    html_path: Path,
    output_png: Path,
    width: int = 1920,
    height: int = 1080,
    seek_sec: float = 10.0,
) -> Path:
    """单帧静态截图 (2026-08-21) — 页面最终态, 供 剪映/zoompan 路径.

    相对逐帧捕获: 每页 1 张截图 (~1s), 动画不在帧里烧, 交给渲染层
    (剪映 IntroType 入场动画 或 ffmpeg Ken Burns). seek_sec 取大值
    (动画 fill-mode:both → 全部定格 end 态), 保证元素全在.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright 未安装 (pip install playwright && playwright install chromium)") from exc

    output_png.parent.mkdir(parents=True, exist_ok=True)
    chrome_args = ["--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1"]

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True, executable_path=PLAYWRIGHT_CHROME, args=chrome_args)
        except Exception as exc:
            logger.warning("[ppt-capture] 指定 chromium 路径不可用, 尝试默认: %s", exc)
            browser = p.chromium.launch(headless=True, args=chrome_args)
        try:
            ctx = browser.new_context(viewport={"width": width, "height": height},
                                      device_scale_factor=1)
            page = ctx.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.wait_for_function(
                "window.__hf && typeof window.__hf.seek === 'function'",
                timeout=15000,
            )
            page.evaluate(f"window.__hf.seek({seek_sec})")
            page.wait_for_timeout(300)  # 字体/图片 base64 加载
            page.screenshot(
                path=str(output_png),
                clip={"x": 0, "y": 0, "width": width, "height": height},
            )
        finally:
            browser.close()

    if not output_png.exists():
        raise RuntimeError(f"单帧截图失败: {output_png}")
    logger.info("[ppt-capture] 静态帧 %s (%dx%d)", output_png.name, width, height)
    return output_png


def capture_element_png(
    html_path: Path,
    output_png: Path,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """元素层透明 PNG 截图 (2026-08-21) — 无 seek, 页面加载完即截.

    PPT 元素级拆解: 每元素一张全画布透明 PNG (元素画在自身坐标),
    供剪映多轨叠层 + 逐级入场动画. 页面必须设透明背景, Chromium
    截图 PNG 保留 alpha (html/body background:transparent).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright 未安装 (pip install playwright && playwright install chromium)") from exc

    output_png.parent.mkdir(parents=True, exist_ok=True)
    chrome_args = ["--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1"]

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True, executable_path=PLAYWRIGHT_CHROME, args=chrome_args)
        except Exception as exc:
            logger.warning("[ppt-capture] 指定 chromium 路径不可用, 尝试默认: %s", exc)
            browser = p.chromium.launch(headless=True, args=chrome_args)
        try:
            ctx = browser.new_context(viewport={"width": width, "height": height},
                                      device_scale_factor=1)
            page = ctx.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.wait_for_timeout(300)  # 字体/图片 base64 加载
            page.screenshot(
                path=str(output_png),
                clip={"x": 0, "y": 0, "width": width, "height": height},
                omit_background=True,  # 保留页面透明背景 (alpha)
            )
        finally:
            browser.close()

    if not output_png.exists():
        raise RuntimeError(f"元素透明截图失败: {output_png}")
    logger.info("[ppt-capture] 元素层 %s (%dx%d)", output_png.name, width, height)
    return output_png
