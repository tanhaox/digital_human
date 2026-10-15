# -*- coding: utf-8 -*-
"""模板视觉自检闭环工具 (0908) — 截图 + 视觉模型描述, 供模板自主迭代.

用法: python tools/_shot.py <html路径> [输出png] — 1920 视口, networkidle+延时,
确保 JS 填充/GSAP 终态/字体就绪后截图 (Chrome --virtual-time-budget 抓空帧的教训)。
"""
import sys
import asyncio
from pathlib import Path


async def shot(html_path: Path, out_png: Path, width: int = 1920, height: int = 1080, settle_ms: int = 2500) -> Path:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": width, "height": height})
        await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        # 播到终态: 触发 preview-play (模板 restart), 等 settle
        try:
            await page.evaluate("window.postMessage({type:'preview-play'}, '*')")
        except Exception:
            pass
        await page.wait_for_timeout(settle_ms)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out_png))
        await browser.close()
    return out_png


if __name__ == "__main__":
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".tmp/_shot") / (src.stem + ".png")
    r = asyncio.run(shot(src, dst))
    print("shot:", r, r.stat().st_size // 1024, "KB")
