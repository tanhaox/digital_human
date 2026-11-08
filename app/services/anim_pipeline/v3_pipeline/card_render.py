# -*- coding: utf-8 -*-
"""v3 配图渲染 (V3) — HTML 模板 + Playwright 截图, 复用封面视觉体系.

用户令 (1105): 配图不走 ComfyUI (AI 画不了精确中文排版, 必出乱码), 走系统已有的
封面渲染机制 — 同一套纸纹/色板/双层边框/字号自适配, HTML 排版想怎么排都行。

产物: data/l0/{书名}/cards/{图名}.png → 装配钉图 (anim_draft quote_img 轨, 已有链路)。
版式按内容类型 (六类口径) 分派:
  清单 (提示/总结) → 标题 + 编号条目左对齐
  金句 (金句/口诀) → 大字居中, 两行断
  结论 (结论)      → 单行超大字
  标题            → 段落标题大字
"""
from __future__ import annotations

import html as _html
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 复用封面色板与纸纹 (单一视觉源, 不另起一套皮肤)
from app.services.book_service.cover_render import _C, _FIBER, _GRAIN

SIZES = {
    "wide": (2720, 832),      # 宽幅信息图 (与既有 信任公式.png 同比例)
    "square": (1600, 1600),
    "vertical": (1080, 1350),
}


def _fit_fs(text: str, box_w: int, cap: int, floor: int = 40) -> int:
    """按字宽估字号 (中文全角 ≈ 1 字宽 = fs)。"""
    n = max(len(str(text or "").strip()), 1)
    return max(floor, min(cap, int(box_w / n)))


def _break_lines(text: str, max_chars: int) -> list[str]:
    """按标点优先断行。"""
    t = str(text or "").strip()
    if len(t) <= max_chars:
        return [t]
    out, cur = [], ""
    for ch in t:
        cur += ch
        if len(cur) >= max_chars and ch in "。！？；，、,.!?;":
            out.append(cur.strip("，、,;"))
            cur = ""
    if cur.strip():
        out.append(cur.strip("，、,;"))
    return out or [t[:max_chars]]


def build_card_html(kind: str, title: str, lines: list[str], *,
                    size: str = "wide", brand: str = "") -> str:
    """纯函数: 内容类型 + 文字 → 完整 HTML 字符串。
    1108 用户令: 白底 70% 半透明 (纯透明叠视频脏) / 去品牌条 / 去边框 / 字加大美化。"""
    W, H = SIZES.get(size, SIZES["wide"])
    e = _html.escape
    pad_x, pad_y = (int(W * 0.08), int(H * 0.10))

    if kind in ("图示", "公式") and len([x for x in lines if str(x).strip()]) <= 1:
        # 单行图示 (公式/单表) — 无编号, 超大字居中 (实锤: 公式带①很怪)
        txt = (lines[0] if lines else title).strip()
        parts = _break_lines(txt, max(8, len(txt) // 2))[:2]
        fs = min(_fit_fs(p, W * 0.86, cap=120) for p in parts) if parts else 90
        body = ('<div class="content center">' + "".join(
            f'<div class="big" style="font-size:{fs}px">{e(p)}</div>' for p in parts) + '</div>')
        align = "center"
    elif kind in ("提示", "总结", "清单", "图示", "公式"):
        # 图示类多条 / 清单: 标题 + 编号条目
        # 清单: 标题 + 编号条目 (字号按最长项铺满可用宽 — 固定号会让宽幅图右侧空白)
        title_fs = _fit_fs(title, W - 2 * pad_x - 80, cap=110)
        longest = max((len(str(t)) for t in lines[:8]), default=10)
        n_items = min(len(lines), 8)
        _avail_h = H - 2 * pad_y - int(H * 0.16) - 150   # 扣标题+品牌条
        _by_h = int(_avail_h / max(n_items, 1) / 1.45)
        item_fs = max(30, min(120, int((W * 0.88 - 150) / max(longest, 1)), _by_h))
        items = "".join(
            f'<li><span class="no">{i}</span><span class="tx">{e(t)}</span></li>'
            for i, t in enumerate(lines[:8], 1))
        body = (f'<div class="content"><div class="ttl" style="font-size:{title_fs}px">'
                f'{e(title)}</div><ul class="lst" style="font-size:{item_fs}px">'
                f'{items}</ul></div>')
        align = "left"
    elif kind in ("金句", "口诀"):
        # 金句: 大字居中, 每行 ≤12 字, 最多 2 行
        parts = _break_lines(lines[0] if lines else title, 12)[:2]
        fs = min(_fit_fs(p, W - 2 * pad_x - 60, cap=130) for p in parts) if parts else 90
        body = ('<div class="content center">' + "".join(
            f'<div class="big" style="font-size:{fs}px">{e(p)}</div>' for p in parts) + '</div>')
        align = "center"
    elif kind == "结论":
        # 结论 → 单行超大字 (事件字同源)
        txt = (lines[0] if lines else title)
        fs = _fit_fs(txt, W - 2 * pad_x - 40, cap=190, floor=64)
        body = (f'<div class="content center"><div class="big solo" '
                f'style="font-size:{fs}px">{e(txt)}</div></div>')
        align = "center"
    else:  # 标题 / 其他
        fs = _fit_fs(title, W - 2 * pad_x - 40, cap=150, floor=56)
        body = (f'<div class="content center"><div class="big" '
                f'style="font-size:{fs}px">{e(title)}</div></div>')
        align = "center"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; }}
body {{
  background:rgba(255,255,255,0.70);   /* 1108 用户令: 白底 70% 半透明 */
  color:{_C['title']};
  font-family:"Noto Serif SC","Source Han Serif SC","Microsoft YaHei",serif;
  display:flex; flex-direction:column; justify-content:center;
  padding:{pad_y}px {pad_x}px {int(H * 0.16)}px; text-align:{align};
}}
.ttl {{ font-weight:800; color:{_C['sub']}; letter-spacing:2px; margin-bottom:34px; }}
.lst {{ list-style:none; display:flex; flex-direction:column; gap:18px; }}
.content {{ width:100%; max-width:{int(W * 0.88)}px; margin:0 auto; }}
.center {{ text-align:center; }}
.lst li {{ display:flex; gap:20px; align-items:baseline;
           font-weight:600; line-height:1.35; }}
.lst .no {{ flex:0 0 auto; width:56px; height:56px; border-radius:50%;
            background:{_C['label_bg']}; color:{_C['label_fg']};
            display:flex; align-items:center; justify-content:center;
            font-size:32px; font-family:"Microsoft YaHei",sans-serif; }}
.lst .tx {{ flex:1; }}
.big {{ font-weight:800; line-height:1.28; letter-spacing:3px; }}
.big.solo {{ letter-spacing:8px; }}
</style></head><body>
{body}
</body></html>"""


def render_card(kind: str, title: str, lines: list[str], out_path: Path, *,
                size: str = "wide") -> Path:
    """渲染配图为 PNG (Playwright 截图, 零 GPU)。"""
    from playwright.sync_api import sync_playwright
    W, H = SIZES.get(size, SIZES["wide"])
    page_html = build_card_html(kind, title, lines, size=size)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True,
                                        args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"chromium 不可用: {exc}") from exc
        try:
            ctx = browser.new_context(viewport={"width": W, "height": H},
                                      device_scale_factor=1)
            page = ctx.new_page()
            page.set_content(page_html, wait_until="networkidle")
            page.screenshot(path=str(out_path), clip={"x": 0, "y": 0,
                                                      "width": W, "height": H},
                            omit_background=True)   # 1108 透明底
        finally:
            browser.close()
    logger.info("[card-render] %s %s → %s", kind, title[:20], out_path.name)
    return out_path


# ── 图示: 脑图 / 流程图 (ECharts 树形, 本地 vendor 零新依赖) ──────────────
# 实锤: tree 的 data 必须是数组 (传对象 → SVG 空壳); canvas 在 headless 截不到
# → 强制 SVG 渲染器; 动画关闭 (截图时定态)。

DIAGRAM_SIZES = {"wide": (1600, 900), "square": (1400, 1400)}


def build_diagram_html(root: dict, *, size: str = "wide", orient: str = "LR",
                       brand: str = "老谭读书 ｜ 商业认知系列") -> str:
    """脑图/流程图 HTML (ECharts tree)。root = {name, children:[{name, children}]}。"""
    import json
    W, H = DIAGRAM_SIZES.get(size, DIAGRAM_SIZES["wide"])

    def _leafless(node: dict) -> dict:
        """叶子节点去掉圆点 (实锤: leaves.symbolSize 不生效, 红点压住文字)。"""
        kids = node.get("children") or []
        out = {k: v for k, v in node.items() if k != "children"}
        if not kids:
            out["symbolSize"] = 0
            out["label"] = {"position": "right", "distance": 8}
        else:
            out["children"] = [_leafless(k) for k in kids]
        return out

    root = _leafless(root)
    # 1108 用户令二轮: 白底70%/字加大美化/去边框去品牌/切割修复
    # (实锤: c04 "过程与产出"过字被切 / c07 "客户反应判定" 客字被圆点+左边距吃掉)
    # 切割修复: 节点圆点全去 (symbolSize 0, 防压字头) + label distance 加宽
    # + 边距 left 6/right 12 (叶文字右侧留足)
    opt = {
        "backgroundColor": "rgba(255,255,255,0)",   # 白底由 body 承载 (半透明)
        "animation": False,
        "series": [{
            "type": "tree", "orient": orient, "roam": False,
            "data": [root],
            "label": {"fontSize": 48, "color": _C["title"],
                      "fontFamily": "Microsoft YaHei", "fontWeight": "bold",
                      "distance": 14, "position": "right",
                      "verticalAlign": "middle"},
            "leaves": {"label": {"fontSize": 44, "color": "#4a3a28",
                                 "fontWeight": "bold", "distance": 14},
                       "symbolSize": 0},
            "lineStyle": {"color": "#C4AB88", "width": 1.8},
            "itemStyle": {"color": _C["sub"], "borderColor": _C["gold"]},
            "symbolSize": 0, "initialTreeDepth": -1,
            "left": "4%", "right": "26%", "top": "5%", "bottom": "5%",
        }],
    }
    e = _html.escape
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html {{ width:{W}px; height:{H}px; background:transparent; }}
body {{ width:{W}px; height:{H}px; background:rgba(255,255,255,0.70); position:relative; }}
#c {{ width:{W}px; height:{H}px; position:relative; z-index:1; }}
</style></head><body>
<div id="c"></div>
<script>window.__opt = {json.dumps(opt, ensure_ascii=False)};</script>
</body></html>"""


def render_diagram(root: dict, out_path: Path, *, size: str = "wide",
                   orient: str = "LR") -> Path:
    """渲染脑图/流程图为 PNG (ECharts SVG 渲染器 + Playwright 截图)。"""
    from pathlib import Path as _P
    import json
    from playwright.sync_api import sync_playwright
    W, H = DIAGRAM_SIZES.get(size, DIAGRAM_SIZES["wide"])
    page_html = build_diagram_html(root, size=size, orient=orient)
    ec = _P(__file__).resolve().parents[4] / "web" / "vendor" / "echarts.min.js"
    if not ec.exists():
        raise RuntimeError(f"echarts 缺失: {ec}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        try:
            ctx = browser.new_context(viewport={"width": W, "height": H},
                                      device_scale_factor=1)
            page = ctx.new_page()
            page.set_content(page_html, wait_until="load")
            page.add_script_tag(path=str(ec))
            page.evaluate("""() => {
                const c = echarts.init(document.getElementById('c'), null, {renderer:'svg'});
                c.setOption(window.__opt);
                window.__ready = true;
            }""")
            page.wait_for_function("window.__ready === true", timeout=15000)
            page.wait_for_timeout(600)
            page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": W, "height": H},
                            omit_background=True)   # 1108 透明底
        finally:
            browser.close()
    logger.info("[card-render] 脑图 %s → %s", str(root.get("name"))[:16], out_path.name)
    return out_path
