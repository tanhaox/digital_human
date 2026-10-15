# -*- coding: utf-8 -*-
"""PPT 出片服务 (2026-08-20 v2) — 按 shape 原始坐标/字号/颜色/背景还原页面.

v2 (还原布局): 不再简陋重排文字, 而是用 python-pptx 读取每个 shape 的
  位置/大小/字号/颜色 + 图片 + 页面背景图, 按原始坐标在 1920x1080 HTML 里
  精准摆放 → Chrome headless 截图 → ffmpeg mp4 (Ken Burns + 淡入).
"""
from __future__ import annotations

import base64
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_config

logger = logging.getLogger(__name__)

__all__ = ["parse_pptx", "build_slide_html", "render_slide_mp4", "clean_notes"]

_CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
_EMU_PER_PX = 9525  # 12192000 EMU / 1280 逻辑px → 按 1920 宽等比: 1920*9525=18288000? 用比例
# 幻灯片 12192000x6858000 EMU, 目标 1920x1080: 比例系数
_PX_PER_EMU_W = 1920 / 12192000
_PX_PER_EMU_H = 1080 / 6858000

# 备注脏前缀/尾标
_NOTE_PREFIX_RE = re.compile(r"^\s*\d+\.\d+\.\d+\s*")
_NOTE_TAIL_RE = re.compile(r"\s*‹#›\s*$")


@dataclass
class TextBlock:
    text: str
    left: float
    top: float
    width: float
    height: float
    font_size_pt: float  # None → 默认
    color: str | None  # hex
    bold: bool = False
    shape_id: int = 0  # parse 时按 shape 迭代序赋 (动画 targeting)
    align: int = 0  # 0=左 1=中 2=右 (2026-08-21: 标题/小节号居中)


@dataclass
class ShapeBlock:
    """装饰自选图形 (卡片底/暗化层等): 填充色+alpha, 圆角, 位置. 不进动画层 (基底).
    path_d/path_w/path_h (2026-08-21): 自定义几何(custGeom)的 SVG 路径, 图标/特殊形状用.
    """
    left: float
    top: float
    width: float
    height: float
    fill_hex: str | None  # e.g. '000000' (无#)
    fill_alpha: float = 1.0  # 0-1 (XML alpha/100000)
    rounded: bool = False
    shape_id: int = 0
    path_d: str | None = None  # SVG path (custGeom)
    path_w: float = 10000.0
    path_h: float = 10000.0


@dataclass
class ImageBlock:
    b64_data: str
    left: float
    top: float
    width: float
    height: float
    shape_id: int = 0
    role: str = "content"  # content(搜图) | style(生图), v1 默认 content


@dataclass
class Slide:
    index: int
    texts: list[str] = field(default_factory=list)  # 纯文本列表 (预览用)
    text_blocks: list[TextBlock] = field(default_factory=list)
    image_blocks: list[ImageBlock] = field(default_factory=list)
    shape_blocks: list[ShapeBlock] = field(default_factory=list)  # 装饰自选图形
    background_b64: str | None = None  # 页面背景图
    bg_above_shape_id: int | None = None  # 背景图来自全幅 pic 时, 记录其 z 位置 (其下形状被盖住)
    notes: str = ""


def clean_notes(text: str) -> str:
    """清洗备注台词: 去豆包时间戳前缀 + ‹#› 页码标记."""
    t = _NOTE_TAIL_RE.sub("", text or "")
    t = _NOTE_PREFIX_RE.sub("", t)
    return t.strip()


def _pt_to_px(pt: float) -> float:
    """pt (1/72 英寸) → px. 画布 1920px = 13.33in 幻灯片 → 144dpi, 故 ×2.

    2026-08-21 修复: 原 96dpi(×1.333)导致字号缩到 67%, 文字偏小+填不满框=排布错位.
    """
    return max(pt, _MIN_FONT_PT) * 144 / 72  # == pt * 2 (6pt 下限)


# 最小可读字号 (2026-08-21): <6pt 在手机屏无法看, 作为小字底线
_MIN_FONT_PT = 6.0


def _color_hex(rgb) -> str | None:
    try:
        if rgb is None:
            return None
        return str(rgb)
    except Exception:
        return None


def _bg_image(slide, prs) -> str | None:
    """提取页面背景图 (p:cSld/p:bg/p:bgPr/a:blipFill/a:blip → rel → base64).

    精确锁定 p:bg 路径, 杜绝把 shape 配图当背景 (旧宽松正则 bug).
    p:bgRef (引母版) 无独立背景图 → 返回 None (由 skin 包统一补).
    """
    from pptx.oxml.ns import qn

    try:
        sld = slide._element
        cSld = sld.find(qn("p:cSld"))
        if cSld is None:
            return None
        bg = cSld.find(qn("p:bg"))
        if bg is None:
            return None
        bg_pr = bg.find(qn("p:bgPr"))
        if bg_pr is None:
            return None  # p:bgRef 引母版, 无独立图
        blip_fill = bg_pr.find(qn("a:blipFill"))
        blip = blip_fill.find(qn("a:blip")) if blip_fill is not None else None
        if blip is None:
            return None
        rid = blip.get(qn("r:embed"))
        if not rid:
            return None
        target_part = slide.part.related_part(rid)
        blob = target_part.blob
        ctype = target_part.content_type
        return f"data:{ctype};base64,{base64.b64encode(blob).decode()}"
    except Exception as exc:
        logger.warning("[ppt] 背景图提取失败: %s", exc)
        return None


def _text_align(shape) -> int:
    """段落对齐 → 0左/1中/2右 (标题/小节号常居中, 2026-08-21)."""
    from pptx.enum.text import PP_ALIGN
    try:
        if not (shape.has_text_frame and shape.text_frame.paragraphs):
            return 0
        al = shape.text_frame.paragraphs[0].alignment
        if al == PP_ALIGN.CENTER:
            return 1
        if al == PP_ALIGN.RIGHT:
            return 2
    except Exception:
        pass
    return 0


def _custgeom_to_svg(path_el) -> str:
    """PPT custGeom 路径 → SVG path d. moveTo/cubicBezTo/lineTo/close → M/C/L/Z."""
    from pptx.oxml.ns import qn
    parts = []
    for child in path_el:
        tag = child.tag.split("}")[-1]
        if tag == "moveTo":
            pt = child.find(qn("a:pt"))
            if pt is not None:
                parts.append("M%s,%s" % (pt.get("x"), pt.get("y")))
        elif tag == "cubicBezTo":
            pts = child.findall(qn("a:pt"))
            if len(pts) == 3:
                parts.append("C%s,%s %s,%s %s,%s" % (
                    pts[0].get("x"), pts[0].get("y"),
                    pts[1].get("x"), pts[1].get("y"),
                    pts[2].get("x"), pts[2].get("y")))
        elif tag == "quadBezTo":
            pts = child.findall(qn("a:pt"))
            if len(pts) == 2:
                parts.append("Q%s,%s %s,%s" % (
                    pts[0].get("x"), pts[0].get("y"),
                    pts[1].get("x"), pts[1].get("y")))
        elif tag in ("lnTo", "lineTo"):
            # lnTo = 直线段 (自由形状图标高频, 2026-08-22 修复: 原只认 lineTo,
            # 漏了 lnTo → 图标直线部分全部丢失, 只剩贝塞尔曲线 → 残缺"识别物")
            pt = child.find(qn("a:pt"))
            if pt is not None:
                parts.append("L%s,%s" % (pt.get("x"), pt.get("y")))
        elif tag == "close":
            parts.append("Z")
    return " ".join(parts)


def _shape_block(shape, sid: int) -> ShapeBlock | None:
    """自选图形 → 装饰块 (卡片底/暗化层/细线装饰). 填充/线色读 XML (含 alpha)."""
    from pptx.oxml.ns import qn
    try:
        sp_pr = shape._element.find(qn("p:spPr"))
        if sp_pr is None:
            return None
        fill = sp_pr.find(qn("a:solidFill"))
        hexval = None
        alpha = 1.0
        if fill is not None:
            clr = fill.find(qn("a:srgbClr"))
            if clr is not None:
                hexval = clr.get("val")
                a = clr.find(qn("a:alpha"))
                if a is not None and a.get("val"):
                    alpha = int(a.get("val")) / 100000.0
            elif shape.fill.type is not None:
                try:
                    hexval = str(shape.fill.fore_color.rgb)
                except Exception:
                    hexval = None
        else:
            # 无填充 → 有可见线(a:ln/a:solidFill)也算装饰块 (细线/边框, PPT 常见)
            ln = sp_pr.find(qn("a:ln"))
            if ln is not None:
                lf = ln.find(qn("a:solidFill"))
                if lf is not None:
                    clr = lf.find(qn("a:srgbClr"))
                    if clr is not None:
                        hexval = clr.get("val")
                        a = clr.find(qn("a:alpha"))
                        if a is not None and a.get("val"):
                            alpha = int(a.get("val")) / 100000.0
        if not hexval:
            return None
        rounded = False
        path_d = None
        path_w, path_h = 10000.0, 10000.0
        geom = sp_pr.find(qn("a:prstGeom"))
        if geom is not None:
            prst = geom.get("prst") or ""
            # roundRect 半径由 adj 控制; adj=0 → 直角 (本 deck 全为 0)
            if "round" in prst.lower():
                adj = 0.0
                av_lst = geom.find(qn("a:avLst"))
                if av_lst is not None:
                    for gd in av_lst.findall(qn("a:gd")):
                        if gd.get("name") == "adj":
                            fmla = (gd.get("fmla") or "").split()
                            if len(fmla) == 3:
                                try:
                                    adj = float(fmla[2])
                                except ValueError:
                                    adj = 0.0
                rounded = adj > 0
        # 自定义几何 (custGeom): 图标/特殊形状 → SVG 路径 (2026-08-21 修图标变方块)
        cust = sp_pr.find(qn("a:custGeom"))
        if cust is not None:
            path_el = cust.find(qn("a:pathLst"))
            if path_el is not None:
                p0 = path_el.find(qn("a:path"))
                if p0 is not None:
                    path_d = _custgeom_to_svg(p0)
                    if p0.get("w"):
                        path_w = float(p0.get("w"))
                    if p0.get("h"):
                        path_h = float(p0.get("h"))
        return ShapeBlock(
            left=shape.left, top=shape.top,
            width=shape.width, height=shape.height,
            fill_hex=hexval, fill_alpha=alpha, rounded=rounded, shape_id=sid,
            path_d=path_d, path_w=path_w, path_h=path_h,
        )
    except Exception:
        return None


def parse_pptx(path: str | Path) -> list[Slide]:
    """解析 pptx → 每页结构化 shape (坐标/字号/颜色/图/背景)."""
    from pptx import Presentation

    prs = Presentation(str(path))
    slides: list[Slide] = []
    for i, slide in enumerate(prs.slides, 1):
        text_blocks: list[TextBlock] = []
        image_blocks: list[ImageBlock] = []
        shape_blocks: list[ShapeBlock] = []
        texts: list[str] = []
        for sid, shape in enumerate(slide.shapes):
            try:
                # 千问 AIGC 水印 (2026-09-05): 千问导出 pptx 统一形状名 WatermarkAigc
                # (右下角小图), 跳过不渲染 — sid 照常计数保 z-order
                if "watermark" in (shape.name or "").lower():
                    continue
                if shape.has_text_frame and shape.text_frame.text.strip():
                    txt = shape.text_frame.text.strip()
                    texts.append(txt)
                    # 取首段首 run 的字号/颜色
                    sz_pt = None
                    color = None
                    bold = False
                    for para in shape.text_frame.paragraphs:
                        for run in para.runs:
                            if run.font.size:
                                sz_pt = run.font.size.pt
                            color = _color_hex(run.font.color.rgb) if (run.font.color and run.font.color.rgb) else color
                            bold = bool(run.font.bold)
                            if sz_pt:
                                break
                        if sz_pt:
                            break
                    tb = TextBlock(
                        text=txt,
                        left=shape.left, top=shape.top,
                        width=shape.width, height=shape.height,
                        font_size_pt=sz_pt, color=color, bold=bold,
                        shape_id=sid,
                        align=_text_align(shape),
                    )
                    text_blocks.append(tb)
                if shape.shape_type == 13:  # PICTURE
                    try:
                        img = shape.image
                        b64 = f"data:{img.content_type};base64,{base64.b64encode(img.blob).decode()}"
                        image_blocks.append(ImageBlock(
                            b64_data=b64, left=shape.left, top=shape.top,
                            width=shape.width, height=shape.height,
                            shape_id=sid,
                        ))
                    except Exception as exc:
                        logger.warning("[ppt] 页%d 图提取失败: %s", i, exc)
                # 装饰自选图形 (卡片底/暗化层): 有 solidFill 即捕获, 色值读 XML (含 alpha)
                sb = _shape_block(shape, sid)
                if sb:
                    shape_blocks.append(sb)
            except Exception as exc:
                logger.warning("[ppt] 页%d shape 解析失败: %s", i, exc)
        notes = ""
        try:
            if slide.has_notes_slide:
                notes = clean_notes(slide.notes_slide.notes_text_frame.text)
        except Exception as exc:
            logger.warning("[ppt] 页%d 备注失败: %s", i, exc)
        bg = _bg_image(slide, prs)
        bg_above_shape_id = None
        if bg is None:
            # 无 p:bg → 全幅 picture shape 即背景 (PPT 常见做法: 铺满整页的图不是 p:bg)
            # 全幅图升为背景, 并从前景 image_blocks 移除 (避免元素层重复渲染)
            for ib in list(image_blocks):
                if (ib.width >= 0.9 * prs.slide_width
                        and ib.height >= 0.9 * prs.slide_height):
                    bg = ib.b64_data
                    bg_above_shape_id = ib.shape_id  # 记录 z 位置: 其下形状被不透明背景盖住
                    image_blocks.remove(ib)
                    break
        slides.append(Slide(
            index=i, texts=texts, text_blocks=text_blocks,
            image_blocks=image_blocks, shape_blocks=shape_blocks,
            background_b64=bg, bg_above_shape_id=bg_above_shape_id, notes=notes,
        ))

    # 列宽几何裁剪 (2026-09-05 二修): 千问窄列文本框声明宽跨整页, 手工断行
    # 在异字体下行宽漂移 → 左列文字贴进右列读成一串 (ep2 页15 实锤)。
    # 规约: 同一水平带 (右侧框顶 落在本块文本竖向范围内) 且横向间距 >1.8in
    # 的右侧文本框 = 邻列 → 本块宽度不得越过它 (留 0.18in 水槽)。
    _cap_column_widths(slides)

    # 相邻页尾句去重 (2026-09-05): 千问生成 pptx 偶发把结尾句同时写进倒数两页
    # 备注 (ep1 实锤: 第7页备注末尾带"我是静，下期见", 第8页整页又是它 →
    # 倒数第二页音频尾+末页音频各说一遍)。本页备注以下一页备注整句收尾 → 截掉。
    for a, b in zip(slides, slides[1:]):
        if not a.notes or not b.notes:
            continue
        na, nb = a.notes.rstrip(), b.notes.rstrip()
        if len(nb) >= 8 and na.endswith(nb):
            a.notes = na[: -len(nb)].rstrip()
            logger.info("[ppt] 页%d 备注尾句与页%d 重复, 已截去 %d 字",
                        a.index, b.index, len(nb))

    # 末页台词重排 (2026-09-05 用户令): 千问母本偶发把人设收尾块 ("…我是静，
    # 下期见。") 放页首、CTA 放其后 → 成片先说下期见再说评论区聊。规约:
    # 含"下期见"的句子必须是全片台词最后一句 — 收尾块在页首时整块移到页尾。
    # ⚠️ 拼接禁用换行: TTS _split_line_indices 按行切, 页备注内 \n 会多出
    # 一行音频 → 17 行对 16 段 → _manifest_callback seg_pairs 越界 (实锤)。
    last = slides[-1]
    if last.notes and "下期见" in last.notes:
        import re as _re
        m = _re.match(r"^(.*?下期见[^。！？]*[。！？])", last.notes, _re.S)
        if m and 0 < m.end() < len(last.notes.rstrip()) and m.end() <= 80:
            block = m.group(1).strip()
            last.notes = (last.notes[m.end():].strip() + block).strip()
            logger.info("[ppt] 页%d 收尾块(下期见)前置 → 移到页尾 (%d 字)",
                        last.index, len(block))
    return slides


def _cap_column_widths(slides: list["Slide"]) -> None:
    """按几何列边界裁文本块声明宽 (2026-09-05, 见 parse 处注释)。

    竖向范围估计 = 手工行数 × 字号 × 1.3 行高 (声明框高不可信 — 千问用
    0.15in 装饰框)。只认"右侧且明显成列"(Δleft > 1.8in) 的邻居, 防把
    下方块误当邻列。宽度字段就地收紧, 下游 (_px_w/_flow_wrap) 全部受益。
    """
    EMU_PER_PX = 6350  # 1920px = 12192000 EMU
    GUTTER = int(0.18 * 914400)
    MIN_COL_GAP = int(1.8 * 914400)
    for s in slides:
        for a in s.text_blocks:
            fs_px = max((a.font_size_pt or 11.0) * 2.0, 12.0)
            n_lines = max((a.text or "").count("\n") + 1, 1)
            a_top, a_bottom = a.top, a.top + n_lines * fs_px * 1.3 * EMU_PER_PX
            cap = a.width
            for b in s.text_blocks:
                if b is a or b.left <= a.left:
                    continue
                if b.left - a.left < MIN_COL_GAP:
                    continue  # 紧贴的同行小块 (标点/序号), 不是列
                b_fs = max((b.font_size_pt or 11.0) * 2.0, 12.0)
                b_n = max((b.text or "").count("\n") + 1, 1)
                b_bottom = b.top + b_n * b_fs * 1.3 * EMU_PER_PX
                if b.top >= a_bottom or b_bottom <= a_top:
                    continue  # 竖向无重叠 → 是上方/下方内容不是右邻列
                cap = min(cap, b.left - a.left - GUTTER)
            if cap < a.width:
                a.width = max(cap, int(1.2 * 914400))  # 至少留 1.2in 防过窄


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_flow(s: str) -> str:
    """流式文本转义 (2026-09-05): 千问 deck 的手工换行符保留为 <br> —
    换行位置是设计的一部分 (窄列收行); 行超宽时由 CSS 自动换行兜底。"""
    return (_esc(s).replace("\r\n", "\n").replace("\r", "\n")
            .replace("\n", "<br>"))


def _flow_wrap(tb: "TextBlock", fs: float, w_px: int) -> tuple[int, str]:
    """千问窄列适配 (2026-09-05): 该类 deck 文本框声明宽跨整页 (wrap=none),
    实际靠手工换行收在窄列里 — 换渲染字体后行宽漂移, 单行穿列互相重叠
    (ep2 页15 左列文字穿过中列实锤)。按最长手工行估设计列宽, 自动换行
    收在列内; 短行 (≤12 字) 保持声明宽直连。
    ⚠️ 仅限左对齐 (align=0): 居中/右对齐是标题系全页排版, 收窄盒子会把
    text-align:center 的视觉中心拉偏 (章标题副句偏移回归实锤)。"""
    lines = [ln for ln in (tb.text or "").split("\n") if ln.strip()]
    if not lines:
        return w_px, ""
    longest = max(len(l) for l in lines)
    if tb.align != 0 or longest <= 12:
        return w_px, _esc_flow(tb.text)
    est = int(longest * fs * 1.12) + 8
    return min(w_px, est), _esc_flow(tb.text)


def _css_color(c) -> str:
    """确保 CSS 色值带 '#' 前缀 (parse 给 'FFFFFF' 不带 # → 非法 CSS → 浏览器回落黑字)."""
    if not c:
        return "#3d2b1f"
    s = str(c)
    return s if s.startswith("#") else "#" + s


def _px_left(v): return round(v * _PX_PER_EMU_W)
def _px_top(v): return round(v * _PX_PER_EMU_H)
def _px_w(v): return round(v * _PX_PER_EMU_W)
def _px_h(v): return round(v * _PX_PER_EMU_H)


def build_slide_html(slide: Slide, *, skin=None, width: int = 1920, height: int = 1080) -> str:
    """按 shape 原始坐标/字号/颜色/背景生成带分层入场动画的 HTML.

    skin (SeriesSkinPack|None): 非 None 时用 skin 的 font_family/animation_profile
    (色/字号/背景已在 apply_skin_to_slides 阶段覆盖到 slide 内).
    动画: shape 迭代序= back-to-front 堆叠, 按 idx*stagger 错峰入场.
    背景层恒显 (无 anim-layer) 保证首帧完整. 注入 window.__hf.seek(t) JS hook
    供 Playwright seek-and-snap 逐帧定位.
    """
    def px_left(v): return round(v * _PX_PER_EMU_W)
    def px_top(v): return round(v * _PX_PER_EMU_H)
    def px_w(v): return round(v * _PX_PER_EMU_W)
    def px_h(v): return round(v * _PX_PER_EMU_H)

    font_family = skin.font_family if skin else (
        'system-ui,"PingFang SC","Microsoft YaHei",sans-serif')
    anim = skin.animation_profile if skin else {"stagger": 0.22, "in_dur": 0.55}
    stagger = float(anim.get("stagger", 0.22))

    # 背景 (图片或纯色)
    if slide.background_b64:
        bg_style = (f"background-image:url('{slide.background_b64}');"
                    f"background-size:{width}px {height}px;background-position:center;")
    elif skin and skin.color_tokens:
        bg_style = f"background:{skin.color_tokens[-1]};"  # 末位色做背景底色
    else:
        bg_style = "background:#f5f0e6;"

    # 统一 shapes 迭代序 (text+image 按 shape_id 混排保 z-order)
    blocks = []
    for tb in slide.text_blocks:
        blocks.append(("text", tb))
    for ib in slide.image_blocks:
        blocks.append(("img", ib))
    blocks.sort(key=lambda b: b[1].shape_id)

    els = []
    for idx, (kind, b) in enumerate(blocks):
        delay = round(idx * stagger, 3)
        # 首块用纯 fade (避免最底层位移突兀), 余块 fade-slide-up
        anim_name = "hf-fade-in" if idx == 0 else "hf-fade-slide-up"
        anim_style = (f"animation-name:{anim_name};animation-delay:{delay}s;")
        if kind == "text":
            fs = _pt_to_px(b.font_size_pt) if b.font_size_pt else 28
            color = _css_color(b.color or (skin.color_tokens[0] if skin and skin.color_tokens else None))
            els.append(
                f'<div id="anim-{b.shape_id}" class="anim-layer" '
                f'style="position:absolute;left:{px_left(b.left)}px;top:{px_top(b.top)}px;'
                f'width:{_flow_wrap(b, fs, px_w(b.width))[0]}px;height:{px_h(b.height)}px;'
                f'font-size:{fs}px;line-height:1.3;color:{color};'
                f'{"font-weight:700;" if b.bold else ""}text-align:{ {0:"left",1:"center",2:"right"}.get(b.align, "left") };'
                f'overflow:visible;word-break:break-word;white-space:normal;box-sizing:border-box;'
                f'{anim_style}">{_flow_wrap(b, fs, px_w(b.width))[1]}</div>'
            )
        else:
            els.append(
                f'<img id="anim-{b.shape_id}" class="anim-layer" '
                f'style="position:absolute;left:{px_left(b.left)}px;top:{px_top(b.top)}px;'
                f'width:{px_w(b.width)}px;height:{px_h(b.height)}px;'
                f'object-fit:contain;{anim_style}" src="{b.b64_data}">'
            )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  html,body{{margin:0;padding:0;width:{width}px;height:{height}px;overflow:hidden;
    font-family:{font_family};}}
  .anim-layer{{opacity:0;animation-fill-mode:both;animation-duration:0.55s;
    animation-timing-function:cubic-bezier(0.22,1,0.36,1);}}
  @keyframes hf-fade-slide-up{{from{{opacity:0;transform:translateY(24px)}}to{{opacity:1;transform:none}}}}
  @keyframes hf-fade-in{{from{{opacity:0}}to{{opacity:1}}}}
</style></head><body>
  <div id="stage" style="position:relative;width:{width}px;height:{height}px;{bg_style}">
    {''.join(els)}
  </div>
<script>window.__hf={{seek:function(t){{document.getAnimations().forEach(function(a){{try{{a.currentTime=t*1000;a.pause()}}catch(e){{}}}})}}}};</script>
</body></html>"""


def _page_shell(width: int, height: int, bg_style: str = "background:transparent") -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
            f"html,body{{margin:0;padding:0;width:{width}px;height:{height}px;"
            f"overflow:hidden;{bg_style}}}</style></head><body>")


def build_base_html(slide: Slide, width: int = 1920, height: int = 1080) -> str:
    """基底层 HTML (2026-08-21 元素级拆解): 背景图 + 装饰自选图形合成.

    不透明白底 (底层). 文字/前景图作为独立透明层叠其上.
    """
    els = []
    # 背景图铺满
    if slide.background_b64:
        els.append(f"<img style='position:absolute;left:0;top:0;width:{width}px;"
                   f"height:{height}px;object-fit:fill' src='{slide.background_b64}'>")
    else:
        els.append(f"<div style='position:absolute;left:0;top:0;width:{width}px;"
                   f"height:{height}px;background:#f5f0e6'></div>")
    # 装饰自选图形 (含全幅暗化层/卡片底), 按 shape_id 序叠放
    for sb in slide.shape_blocks:
        if slide.bg_above_shape_id is not None and sb.shape_id < slide.bg_above_shape_id:
            continue  # 在不透明全幅背景图之下, 被盖住, 不渲染 (2026-08-21 修背景被色块覆盖)
        alpha = max(0.0, min(1.0, sb.fill_alpha))
        if sb.path_d:
            # 自定义几何图标 → SVG 路径渲染 (2026-08-21 修图标变方块)
            els.append(
                f"<svg style='position:absolute;left:{_px_left(sb.left)}px;top:{_px_top(sb.top)}px;"
                f"width:{_px_w(sb.width)}px;height:{_px_h(sb.height)}px;"
                f"overflow:visible' viewBox='0 0 {sb.path_w:.0f} {sb.path_h:.0f}' "
                f"preserveAspectRatio='none'>"
                f"<path d='{sb.path_d}' fill='{_css_color(sb.fill_hex)}' "
                f"fill-opacity='{alpha:.3f}'/></svg>")
        else:
            r = f"border-radius:{min(_px_w(sb.width), _px_h(sb.height)) * 0.12}px" if sb.rounded else ""
            els.append(
                f"<div style='position:absolute;left:{_px_left(sb.left)}px;top:{_px_top(sb.top)}px;"
                f"width:{_px_w(sb.width)}px;height:{_px_h(sb.height)}px;"
                f"background:{_css_color(sb.fill_hex)};opacity:{alpha:.3f};{r}'></div>")
    return _page_shell(width, height) + "".join(els) + "</body></html>"


def build_text_element_html(tb: TextBlock, width: int = 1920, height: int = 1080) -> str:
    """单文字块透明层 HTML: 块在自身坐标渲染 (像素级), 供剪映叠层逐级入场."""
    fs = _pt_to_px(tb.font_size_pt) if tb.font_size_pt else 28
    align = {0: "left", 1: "center", 2: "right"}.get(tb.align, "left")
    fw, ftxt = _flow_wrap(tb, fs, _px_w(tb.width))
    div = (
        f"<div style='position:absolute;left:{_px_left(tb.left)}px;top:{_px_top(tb.top)}px;"
        f"width:{fw}px;height:{_px_h(tb.height)}px;"
        f"font-size:{fs}px;line-height:1.3;color:{_css_color(tb.color)};"
        f"{'font-weight:700;' if tb.bold else ''}text-align:{align};"
        f"font-family:'Noto Sans SC','Source Han Sans SC','Microsoft YaHei','PingFang SC',sans-serif;"
        f"overflow:visible;word-break:break-word;white-space:normal;box-sizing:border-box'>"
        f"{ftxt}</div>"
    )
    return _page_shell(width, height) + div + "</body></html>"


def build_image_element_html(ib: ImageBlock, width: int = 1920, height: int = 1080) -> str:
    """单前景图透明层 HTML: 图在自身坐标渲染 (object-fit:contain 保形)."""
    img = (
        f"<img style='position:absolute;left:{_px_left(ib.left)}px;top:{_px_top(ib.top)}px;"
        f"width:{_px_w(ib.width)}px;height:{_px_h(ib.height)}px;"
        f"object-fit:contain' src='{ib.b64_data}'>"
    )
    return _page_shell(width, height) + img + "</body></html>"


def render_slide_png(slide_html: str, work_dir: Path, output_png: Path,
                     width: int = 1920, height: int = 1080) -> Path:
    """渲染单页为 1 张最终态静态 PNG (2026-08-21).

    替代逐帧捕获: Playwright seek 到动画终态截图 (~1s/页). 动画不烧进帧里,
    由剪映 IntroType 入场动画 (jy 模式) 或 ffmpeg zoompan (auto 模式) 提供.
    失败回退 Chrome headless --virtual-time-budget 截图.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    html_path = work_dir / "slide.html"
    html_path.write_text(slide_html, encoding="utf-8")
    try:
        from app.services.ppt_frame_capture import capture_slide_png
        return capture_slide_png(html_path, output_png, width=width, height=height)
    except Exception as exc:
        logger.warning("[ppt] Playwright 静态帧失败, 回退 Chrome 截图: %s", exc)
        return _chrome_screenshot_png(slide_html, work_dir, output_png, width, height)


def png_to_zoompan_mp4(png_path: Path, output_mp4: Path, duration_sec: float,
                       width: int = 1920, height: int = 1080) -> Path:
    """静态 PNG → Ken Burns 缩放+淡入 mp4 (auto 模式动画层)."""
    from app.infrastructure.ffmpeg import run_ffmpeg
    dur = max(1.5, duration_sec)
    fps = 25
    frames = int(dur * fps)
    vf = (
        f"zoompan=z='min(zoom+0.0004,1.08)':d={frames}:x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps},"
        f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, dur-0.4):.2f}:d=0.4"
    )
    run_ffmpeg([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(png_path),
        "-vf", vf, "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        str(output_mp4),
    ], timeout=120)
    return output_mp4


def _chrome_screenshot_png(slide_html: str, work_dir: Path, output_png: Path,
                           width: int, height: int) -> Path:
    """Chrome headless 截最终态 PNG (动画走完: --virtual-time-budget 拉满)."""
    from app.infrastructure.ffmpeg import run_ffmpeg
    html_path = work_dir / "slide.html"
    html_path.write_text(slide_html, encoding="utf-8")
    cmd = [
        _CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
        f"--screenshot={output_png}", f"--window-size={width},{height}",
        "--virtual-time-budget=15000", html_path.as_uri(),
    ]
    try:
        run_ffmpeg(cmd, timeout=60)
    except RuntimeError:
        pass  # Chrome 截图失败时直接判文件
    if not output_png.exists():
        raise RuntimeError("Chrome 截图失败 (static fallback)")
    return output_png


def render_slide_mp4(slide_html: str, work_dir: Path, output_mp4: Path,
                     duration_sec: float, width: int = 1920, height: int = 1080) -> Path:
    """渲染单页 mp4 — 主路径 Playwright seek-and-snap 逐帧 (分层入场动效).

    失败回退 _render_slide_mp4_static (旧 Chrome 截图 + zoompan Ken Burns),
    不阻断产线 (风险点 3: 生产若未装 playwright chromium).
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    html_path = work_dir / "slide.html"
    html_path.write_text(slide_html, encoding="utf-8")

    dur = max(1.5, duration_sec)
    try:
        from app.services.ppt_frame_capture import capture_slide_frames
        from app.infrastructure.ffmpeg import frames_to_mp4
        frames_dir = work_dir / "frames"
        if frames_dir.exists():
            for f in frames_dir.iterdir():
                f.unlink()
        capture_slide_frames(html_path, frames_dir, dur, fps=25, width=width, height=height)
        frames_to_mp4(frames_dir, "frame_%06d.png", output_mp4, fps=25,
                      width=width, height=height)
        return output_mp4
    except Exception as exc:
        logger.warning("[ppt] Playwright 动画渲染失败, 回退 static zoompan: %s", exc)
        return _render_slide_mp4_static(slide_html, work_dir, output_mp4, dur, width, height)


def _render_slide_mp4_static(slide_html: str, work_dir: Path, output_mp4: Path,
                             duration_sec: float, width: int = 1920, height: int = 1080) -> Path:
    """旧路径 fallback: Chrome 单 PNG → zoompan Ken Burns + 淡入."""
    png = _chrome_screenshot_png(slide_html, work_dir, work_dir / "slide.png", width, height)
    return png_to_zoompan_mp4(png, output_mp4, duration_sec, width, height)
