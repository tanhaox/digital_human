"""发布卡封面渲染 (0918): HTML 模板 + Playwright 截图 — 模板化可控, 不走 LLM 生图.

0918 终版: 用户提供参考图原始提示词 (设计正源) + 像素取证校准:
- 米黄复古宣纸纹理 + 细浅棕双层边框, 国风书卷
- 安全区规范 (提示词原文): 上下各预留 25%, 所有文字放中间 48% 内容区
  → 内容 y 25%~74%, 天然满足抖音主页 3:4 裁切 (裁切区 y 12.5%~87.5%)
- 排版: 左上深棕圆角标签[第N集]白字 → 居中大号粗体书名 → 居中砖红副标
  → 居中虚线+书图标 → 底部浅棕圆角长条[老谭读书｜商业认知系列]深棕字
- 0918c 竖版对表豆包正源提示词 (用户贴原文重发): 标签回左上, 内容回
  中间 48% (y500~1400), 撤虚线+书图标 (无多余装饰), 书名块在标签与
  品牌条之间光学居中; 横版维持 0918 终版口径不动.
- 渲染图漂移修正史: 参考图(生成图)品牌栏在 90.7% 违反了自己的安全区规范,
  终版按提示词收回内容区; 标签圆角/品牌条浅棕实底为提示词明示、渲染图
  丢失/变体的细节, 按提示词还原.
"""
from __future__ import annotations

import html as _html
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 品牌栏固定文案 (0921d 用户令: 老谭读书中间无空格)
BRAND_TEXT = "老谭读书 ｜ 商业认知系列"

# 色板 — 参考图像素实测 + 提示词口径 (细浅棕框/浅棕圆角品牌条)
_C = {
    "paper": "#FDF3DC", "paper_low": "#F8EACB",
    "gold": "#C7AC82", "gold_thin": "#D6BE97",     # 细浅棕双层框
    "label_bg": "#3C1D08", "label_fg": "#FFFFFF",
    "title": "#0C0000",                            # 主标近黑
    "sub": "#941E02",                              # 副标砖红
    "deco": "#3C1D08",                             # 虚线/书图标
    "brand_fill": "#D8C3A0", "brand_fg": "#4A2E18",  # 浅棕圆角条+深棕字
}

# 双层纸纹: 细颗粒 + 粗纤维 (净底色须落在 #F5E8CD 以上 — 旧纸感来自
# 颗粒本身, 不来自压暗底色; 三版教训: 暗化过量 → 土黄)
_GRAIN = (
    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' "
    "width='300' height='300'><filter id='n'>"
    "<feTurbulence type='fractalNoise' baseFrequency='0.5' numOctaves='4' "
    "seed='7'/>"
    "<feColorMatrix values='0 0 0 0 0.62 0 0 0 0 0.52 0 0 0 0 0.34 "
    "0 0 0 0.10 0'/></filter>"
    "<rect width='300' height='300' filter='url(%23n)'/></svg>"
)
_FIBER = (
    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' "
    "width='500' height='500'><filter id='f'>"
    "<feTurbulence type='fractalNoise' baseFrequency='0.035 0.09' "
    "numOctaves='3' seed='3'/>"
    "<feColorMatrix values='0 0 0 0 0.55 0 0 0 0 0.46 0 0 0 0 0.30 "
    "0 0 0 0.05 0'/></filter>"
    "<rect width='500' height='500' filter='url(%23f)'/></svg>"
)

def _clip(text: str, limit: int) -> str:
    t = str(text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _hero_break(text: str, max_px_w: int, cap_fs: int,
                floor_fs: int = 76) -> tuple[str, int]:
    """红字钩子断行+定号 (0921 实测改版: 钩子=主视觉, 网格缩略一眼可读).

    抖音个人主页 1:1 格实测 (ep1 打样): 旧版钩子 52px → 404px 格里 19px 糊,
    书名反而是最大字 — 层级倒挂, 主信息最先死。规则: 单行若已达 cap 的 85%
    就单行, 否则两行均衡断 (标点优先) 吃满 cap — 两行大字 > 一行小字。
    0921e 用户令: 封面上逗号直接去除 ("同一份内容，凭什么卖五遍" → 两行
    无逗号) — 顿/分号同除, ？！保留 (句意符号)。
    返回 (含 <br> 的 HTML, font_px)。
    """

    def _nc(s: str) -> str:  # 去封面逗号族 (？！句意符号保留)
        return s.replace("，", "").replace("、", "").replace("；", "")

    raw = str(text or "").strip()
    if not raw:
        return "", floor_fs
    t = _nc(raw)
    n = len(t)
    fs1 = min(cap_fs, int((max_px_w - (n - 1) * 2) * 0.96 / n))
    if fs1 >= cap_fs * 0.85:
        return _html.escape(t), fs1
    # 切点在原文找 (逗号即天然断行信号 — 先剥逗号再找会把切点抹掉), 离中心
    # 最近者优先; 断点字符: 逗号族丢弃, ？！。：保留在行尾
    best: tuple[float, int, str] | None = None
    mid = len(raw) / 2
    for i, ch in enumerate(raw):
        if ch in "，。！？：；、" and 1 <= i <= len(raw) - 2:
            score = abs(i + 0.5 - mid)
            if best is None or score < best[0]:
                best = (score, i, ch)
    # 0923 均衡守卫 (用户令·限宽换行): 标点断行若两行悬殊 (<0.5) 弃用 —
    # 长行拖小字号 (5/13 断实锤 77px), 均衡两行才能吃满上限
    if best is not None:
        _l = best[1] + 1
        _r = len(raw) - _l
        if _r and min(_l, _r) / max(_l, _r) < 0.5:
            best = None
    if best is None:
        left, right = t[: (n + 1) // 2], t[(n + 1) // 2:]
    else:
        keep = best[2] in "。！？："
        left = _nc(raw[: best[1] + (1 if keep else 0)])
        right = _nc(raw[best[1] + 1:])
    m = max(len(left), len(right))
    fs = min(cap_fs, int((max_px_w - (m - 1) * 2) * 0.96 / m))
    return (_html.escape(left) + "<br>" + _html.escape(right),
            max(fs, floor_fs))


def build_cover_html(kind: str, *, ep_label: str, main_title: str,
                     subtitle: str, brand: str = BRAND_TEXT) -> str:
    """kind: 'v' (1080×1920) | 'h' (1920×1080). 纯函数 → 完整 HTML 字符串.

    版式=提示词正源: 标签左上/其余居中, 全内容在中间 48% 安全区.
    """
    vertical = kind == "v"
    W, H = (1080, 1920) if vertical else (1920, 1080)
    # 0921d 用户令: 书名加书名号《》 (模板层, 数据不动)。
    # 书名号只压**外侧**空半边 (《压左 / 》压右, 内侧零压缩 — 对称负边距会把
    # 正文拉进括号墨迹, 用户实锤"叠在一起"); 净省 0.6em, 行加宽出安全区补字号
    inner = _clip(main_title, 16)
    sub = _clip(subtitle, 26)

    def _t_fit(w: int, cap: int) -> int:
        n = max(len(inner), 1)
        eff = n + 2 * 1.0 - 2 * 0.3  # 两括号各留 1em、外侧各压 0.3em
        return min(cap, max(int((w - (n + 1) * 2) / eff), 44))

    # 0921d 用户复审令 (逐条): ①外围线框删 ②书名占原第N集位 (左上) ③红字
    # 两行变三行 = 字宽贴边溢出换行 → 定号乘 0.96 安全余量 ④第N集移到老谭读书
    # 下面并加大到书名同号 ⑤老谭读书 64 号 (原角标号) ⑥横版书图标装饰删。
    # 预算 (竖版安全区 900px): 书名 137 + 钩子 2×116×1.28≈297 + 品牌条 ~100
    # + 大角标 ~148 + 间隙 ≈ 820 < 900 ✓
    def _fit(text: str, w: int, cap: int) -> int:
        n = max(len(str(text or "").strip()), 1)
        return min(cap, int((w - (n - 1) * 2) * 0.96 / n))

    if vertical:
        sub_html, s_fs = _hero_break(sub, 860, 132)
        t_fs = _t_fit(980, 132)  # 书名行宽 860+120 (加宽出安全区 60px/侧)
        content = dict(
            safe="left:110px;right:110px;top:500px;bottom:520px",
            t_fs=t_fs,
            s_fs=s_fs, sub_lh=1.28,
            brand_fs=64, brand_ls=2, brand_pad="18px 20px",
            badge_pad="16px 44px", badge_ls=10, foot_gap=34,
            title_widen="120px",
            # 0921d 用户令: 书名居中, 且从安全区顶下挪一行 ("回车后的位置")
            title_mt=f"{round(t_fs * 1.18)}px",
        )
    else:
        # 0923 用户令(修正): 横版红字非缩小而是**限宽换行** — 一行铺满 1580 太霸道;
        # 钩子块宽限 1080 (≈安全区 2/3), 字号上限维持 116, 超宽自然两行
        sub_html, s_fs = _hero_break(sub, 1080, 116)
        h_t = _t_fit(1580, 116)
        content = dict(
            safe="left:170px;right:170px;top:100px;bottom:100px",
            t_fs=h_t,
            s_fs=s_fs, sub_lh=1.24,
            brand_fs=58, brand_ls=2, brand_pad="14px 20px",
            badge_pad="12px 36px", badge_ls=8, foot_gap=28,
            title_widen="0px",
            title_mt=f"{round(h_t * 1.18 / 2)}px",  # 0921d 用户令: 下挪半行
        )

    e = _html.escape
    c = content
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html,body {{ width:{W}px; height:{H}px; overflow:hidden; }}
  body {{
    font-family:"Source Han Serif SC","Noto Serif SC","SimSun","NSimSun",serif;
    background:linear-gradient(180deg,{_C['paper']},{_C['paper_low']});
    position:relative;
  }}
  .grain {{ position:absolute; inset:0; opacity:.8; mix-blend-mode:multiply;
           background-image:url("{_GRAIN}"),url("{_FIBER}"); }}
  .vig {{ position:absolute; inset:0;
         background:radial-gradient(ellipse at 50% 40%, rgba(253,243,220,0) 60%,
                    rgba(203,172,120,.15) 100%); }}
  .safe {{ position:absolute; {c['safe']};
          display:flex; flex-direction:column; align-items:center; }}
  .main-title {{ align-self:center; text-align:center; margin-top:{c['title_mt']};
                width:calc(100% + {c['title_widen']});
                color:{_C['title']};
                font-weight:900; line-height:1.18;
                font-size:{c['t_fs']}px; letter-spacing:2px;
                -webkit-text-stroke:1.5px {_C['title']}; }}
  .bm {{ letter-spacing:0; }}      /* 书名号: 只压外侧空半边, 内侧零压缩防叠字 */
  .bo {{ margin-left:-0.3em; }}
  .bc {{ margin-right:-0.3em; }}
  .mid {{ flex:1; display:flex; flex-direction:column;
         justify-content:center; align-items:center; width:100%; }}
  .sub-title {{ color:{_C['sub']};
               font-weight:900; line-height:{c['sub_lh']}; text-align:center;
               font-size:{c['s_fs']}px; letter-spacing:2px;
               -webkit-text-stroke:1px {_C['sub']}; }}
  .foot {{ display:flex; flex-direction:column; align-items:center;
          gap:{c['foot_gap']}px; }}
  .brand {{ background:{_C['brand_fill']}; border-radius:12px;
           color:{_C['brand_fg']}; font-size:{c['brand_fs']}px; font-weight:700;
           letter-spacing:{c['brand_ls']}px; padding:{c['brand_pad']};
           text-align:center; white-space:nowrap; }}
  .ep-badge {{ background:{_C['label_bg']}; color:{_C['label_fg']};
              font-size:{c['t_fs']}px; font-weight:700;
              letter-spacing:{c['badge_ls']}px; line-height:1.1;
              padding:{c['badge_pad']}; border-radius:14px; }}
</style></head>
<body>
  <div class="vig"></div><div class="grain"></div>
  <div class="safe">
    <div class="main-title"><span class="bm bo">《</span>{e(inner)}<span class="bm bc">》</span></div>
    <div class="mid">
      <div class="sub-title">{sub_html}</div>
    </div>
    <div class="foot">
      <div class="brand">{e(brand)}</div>
      <div class="ep-badge">{e(ep_label)}</div>
    </div>
  </div>
</body></html>"""


def render_covers(slots: dict, out_dir: Path) -> dict[str, Path]:
    """一次浏览器会话渲染竖+横 → {kind: png_path}.

    大字海报 (cover_p) 0921 用户令撤下 — 入口/模板/渲染全链移除,
    存量 png 留盘不删.
    """
    from playwright.sync_api import sync_playwright

    from app.services.ppt_frame_capture import PLAYWRIGHT_CHROME

    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for kind, (w, h) in (("v", (1080, 1920)), ("h", (1920, 1080))):
        page_html = build_cover_html(kind, **slots)
        jobs.append((kind, w, h, page_html))

    chrome_args = ["--no-sandbox", "--disable-gpu", "--force-device-scale-factor=1"]
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True,
                                        executable_path=PLAYWRIGHT_CHROME,
                                        args=chrome_args)
        except Exception as exc:  # noqa: BLE001 — 指定路径缺失回退默认安装
            logger.warning("[cover] 指定 chromium 不可用, 用默认: %s", exc)
            browser = p.chromium.launch(headless=True, args=chrome_args)
        try:
            for kind, w, h, page_html in jobs:
                ctx = browser.new_context(viewport={"width": w, "height": h},
                                          device_scale_factor=1)
                page = ctx.new_page()
                page.set_content(page_html, wait_until="networkidle")
                # 0921 红字保两行 (用户实锤 3 行): 字号公式按理论字宽算, 真实
                # 字体宽度略超即换行 — 浏览器内实测行数, 超两行降号到装下为止
                page.evaluate("""() => {
                    const el = document.querySelector('.sub-title');
                    if (!el) return;
                    const fits = () => {
                        const lh = parseFloat(getComputedStyle(el).lineHeight) || 1;
                        return Math.round(el.clientHeight / lh) <= 2;
                    };
                    let fs = parseInt(getComputedStyle(el).fontSize);
                    for (let i = 0; i < 40 && !fits() && fs > 56; i++) {
                        fs -= 3;
                        el.style.fontSize = fs + 'px';
                    }
                }""")
                page.wait_for_timeout(500)
                out = out_dir / f"cover_{kind}.png"
                page.screenshot(path=str(out),
                                clip={"x": 0, "y": 0, "width": w, "height": h})
                ctx.close()
                logger.info("[cover] %s → %s (%dx%d)", kind, out, w, h)
        finally:
            browser.close()
    return {kind: out_dir / f"cover_{kind}.png" for kind, _, _, _ in jobs}
