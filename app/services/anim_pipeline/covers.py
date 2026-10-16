# -*- coding: utf-8 -*-
"""开场书墙素材 — 双轨制 (0916 用户令).

① 知名度墙 top10/ (资本论/经济学原理/贫穷的本质/富爸爸/薛兆丰/国家为什么
   会失败/浪潮之巅/塔勒布/智能革命/MBA十日读): **静态固化不换** — 商业认知
   味的公共背书墙, 与本书无关.
② 本书封面 00_本书_*.jpg: **每本书从其 epub 提取** (OPF cover-image 正式
   声明, 非最大图猜测), 同库共存, 效果期按书名取用.

源: zhihailib F 类爬取 (投资/股票/交易类关键词筛除, 50 张备池 web/).
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from app.services.anim_pipeline.config import load as cfg_load

WALL_DIR = Path(cfg_load().output_root) / "_资产" / "book_covers"
_BAD_TITLE = re.compile(r"[\\\\/:*?\"<>|，。()（）·—\s&#;\d]+")


def _safe_title(title: str) -> str:
    return _BAD_TITLE.sub("_", title)[:36].strip("_") or "book"


def book_cover_path(book_title: str) -> Path:
    """本书封面缓存路径 (00_本书_*.jpg, 可能不存在) — 页面出图/重提取共用."""
    return WALL_DIR / "top10" / f"00_本书_{_safe_title(book_title)}.jpg"


def extract_book_cover(book_title: str, src_dir: Path | None = None) -> Path | None:
    """本书 epub → 封面 jpg, 落 00_本书_{书名}.jpg (幂等缓存, 换书各提各的).

    epub 查找: src_dir (缺省 config book_source_dir) 下书名模糊匹配 .epub;
    提取走 OPF manifest cover-image 声明, 无声明兜底最大图.
    """
    if src_dir is None:
        try:
            from app.config import load_config
            src_dir = Path(load_config().defaults.book_source_dir)
        except Exception:  # noqa: BLE001
            src_dir = Path("G:/Desktop/畅销书")
    out = book_cover_path(book_title)
    if out.exists():
        return out
    src_dir = Path(src_dir)
    key = re.sub(_BAD_TITLE, "", book_title)[:6]
    epub = next((p for p in src_dir.glob("*.epub")
                 if key and key in re.sub(_BAD_TITLE, "", p.stem)), None)
    if not epub:
        return None
    try:
        z = zipfile.ZipFile(epub)
        names = z.namelist()
        imgs = [n for n in names if re.search(r"\.(jpe?g|png|webp)$", n, re.I)]
        if not imgs:
            return None
        target = None
        opf = next((n for n in names if n.endswith(".opf")), None)
        if opf:
            txt = z.read(opf).decode("utf-8", "ignore")
            m = (re.search(r'<item[^>]*properties="[^"]*cover-image[^"]*"[^>]*href="([^"]+)"', txt)
                 or re.search(r'<item[^>]*href="([^"]+)"[^>]*properties="[^"]*cover-image[^"]*"', txt))
            if m:
                href = m.group(1).split("/")[-1]
                target = next((n for n in imgs if n.split("/")[-1] == href), None)
            if target is None:
                # 0924 修: <meta name="cover" content="X"/> 给的是 id 不是文件名 —
                # 拿 id 查 <item id="X" href="Y"/> 取真实 href (定位.epub 实锤:
                # content="cover" 但文件名是 cover.jpeg, id≠文件名导致匹配失败)
                m2 = re.search(r'name="cover"[^>]*content="([^"]+)"', txt)
                if m2:
                    cid = m2.group(1)
                    m3 = re.search(rf'<item[^>]*id="{re.escape(cid)}"[^>]*href="([^"]+)"', txt) \
                         or re.search(rf'<item[^>]*href="([^"]+)"[^>]*id="{re.escape(cid)}"', txt)
                    if m3:
                        href = m3.group(1).split("/")[-1]
                        target = next((n for n in imgs if n.split("/")[-1] == href), None)
        if target is None:
            # 0924 修: 文件名含 "cover" 优先 (cover.jpeg/cover.jpg/cover_image.png)
            target = next((n for n in imgs if "cover" in n.lower().split("/")[-1]), None)
        if target is None:
            target = max(imgs, key=lambda n: z.getinfo(n).file_size)
        from PIL import Image
        im = Image.open(io.BytesIO(z.read(target))).convert("RGB")
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, quality=95)
        return out
    except Exception:  # noqa: BLE001
        return None


def wall_scroll_end_frame(book_title: str, scroll_mp4: Path | None = None) -> Path | None:
    """滚动末帧 jpg (定格/压暗垫底用, 幂等缓存)."""
    scroll = scroll_mp4 or (WALL_DIR / f"wall_scroll_{_safe_title(book_title)}.mp4")
    if not scroll.exists():
        return None
    out = scroll.with_suffix(".end.jpg")
    if out.exists():
        return out
    import subprocess
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.05", "-i", str(scroll),
         "-frames:v", "1", "-strict", "unofficial", str(out)],
        capture_output=True, text=True, timeout=30)
    return out if r.returncode == 0 and out.exists() else None


def wall_covers() -> list[Path]:
    """知名度墙 top10 (静态固化, 不含 00_本书_*)."""
    d = WALL_DIR / "top10"
    return sorted(p for p in d.glob("*.jpg") if not p.name.startswith("00_"))


# ── 书墙滚动过场渲染 (0916 用户令: 首帧4本 → 快速左→右扫 → 本书居中收,
#    身后剩1-2本, 全程1-1.5s; 预渲染范式 = 逐帧PIL + ffmpeg VHS档案) ──
_W_BG = (236, 240, 243)   # 墙底色 (目标帧实测)
_W_SLOT = 480             # 槽宽: 1920/4 = 首帧恰好 4 本
_W_COVER_W = 440          # 封面宽 (455:634 比例 → 高 613)
_W_OURS_AT = 8            # 本书在 11 张墙序中的位次 (身后留 2 本)


def render_wall_scroll(book_title: str, duration_s: float = 1.2,
                       out: Path | None = None) -> Path | None:
    """本书墙滚动 mp4 (幂等缓存). 墙序: 前8名 + 本书 + 第9/10名."""
    from PIL import Image
    walls = wall_covers()
    ours = extract_book_cover(book_title)
    if not walls or not ours:
        return None
    strip_files = walls[:_W_OURS_AT] + [ours] + walls[_W_OURS_AT:]
    out = out or (WALL_DIR / f"wall_scroll_{_safe_title(book_title)}.mp4")
    if out.exists():
        return out

    W, H = 1920, 1080
    ch = round(_W_COVER_W * 634 / 455)  # 613
    covers = []
    for p in strip_files:
        try:
            im = Image.open(p).convert("RGB").resize((_W_COVER_W, ch), Image.LANCZOS)
            covers.append(im)
        except Exception:  # noqa: BLE001 — 坏图跳槽
            covers.append(Image.new("RGB", (_W_COVER_W, ch), _W_BG))
    y0 = (H - ch) // 2
    # 终态: 本书槽中心 → 画面中心
    end_left = _W_OURS_AT * _W_SLOT + _W_SLOT // 2 - W // 2
    frames_n = max(int(duration_s * 24), 12)
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for f in range(frames_n):
            u = f / (frames_n - 1)
            off = round(end_left * (1 - (1 - u) ** 3))  # 三次方缓出: 快起慢停
            frame = Image.new("RGB", (W, H), _W_BG)
            for i, cv in enumerate(covers):
                x = i * _W_SLOT + (_W_SLOT - _W_COVER_W) // 2 - off
                if -_W_COVER_W < x < W:
                    frame.paste(cv, (x, y0))
            frame.save(tdp / f"f{f:04d}.jpg", quality=95)
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-framerate", "24",
             "-i", str(tdp / "f%04d.jpg"),
             "-c:v", "libx264", "-preset", "medium", "-crf", "19",
             "-pix_fmt", "yuv420p", "-r", "24",
             "-colorspace", "bt709", "-color_range", "tv",
             "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
             "-movflags", "+faststart", "-an", str(out)],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not out.exists():
            return None
    return out
