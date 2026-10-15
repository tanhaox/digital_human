# -*- coding: utf-8 -*-
"""保险 3: CSP 生效后的浏览器实开验证。
- anim_rf 页: 捕获 securitypolicyviolation (DOM 事件, 权威通道) + console 红字 (诊断通道);
  跑两条交互路径 (对齐试算真调 dry-run + 花字弹窗开合); 渲染计数正常。
- 旧页 anim.html: 加载无 pageerror (确认未被 CSP 误伤 — 中间件只圈 anim_rf)。
violation 噪声白名单: 浏览器扩展注入 (chrome-extension:// 等) 不算页面问题 —
门禁只看"页面自身来源"的 violation (sourceFile/blockedURI 非扩展协议), 须为 0。"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from playwright.sync_api import sync_playwright

NEW = "http://127.0.0.1:54321/web/anim.html?book_id=HBO&ep_index=1"
OLD = "http://127.0.0.1:54321/web/index.html"   # 其他 /web 页面 (旧 anim.html 已被新页同名替换)
violations, errs = [], []
EXT = ("chrome-extension://", "moz-extension://")


def is_noise(v):
    """扩展注入 ≠ 页面问题: sourceFile 或 blockedURI 挂扩展协议即噪声"""
    return v.get("s", "").startswith(EXT) or v.get("b", "").startswith(EXT)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    # CSP 违规权威通道: DOM 事件 (带 sourceFile/行号, 可分噪声); console 文本只做诊断
    pg.add_init_script("""
      window.__cspViol = [];
      document.addEventListener('securitypolicyviolation', e => window.__cspViol.push({
        d: e.violatedDirective, b: String(e.blockedURI).slice(0, 90),
        s: String(e.sourceFile || '').slice(0, 90), l: e.lineNumber,
      }));
    """)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: violations.append(m.text) if m.type == "error" and "Content Security Policy" in m.text else None)

    # ── 新页: 加载 + 渲染 + 两条交互路径 ──
    pg.goto(NEW, wait_until="networkidle")
    pg.wait_for_timeout(1200)
    cards = pg.locator(".card").count()
    groups = pg.locator(".group-row").count()
    print(f"渲染: cards={cards} groups={groups} (期望 46/9)")
    # 路径1: 对齐试算 (真实 dry-run, 只读)
    pg.click("#btn-align-dry")
    pg.wait_for_timeout(2500)
    prog_ok = pg.locator("#btn-align-dry").is_enabled()
    # 路径2: 花字弹窗开合
    pg.locator(".card button[data-act=textLayer]").first.click()
    pg.wait_for_timeout(300)
    dlg_ok = pg.locator("#dlg").evaluate("d => d.open")
    pg.keyboard.press("Escape")
    v_all = pg.evaluate("() => window.__cspViol || []")
    noise = [v for v in v_all if is_noise(v)]
    own = [v for v in v_all if not is_noise(v)]
    print(f"交互: 对齐试算按钮恢复={prog_ok} | 花字弹窗开={dlg_ok}")
    print(f"[门禁②] 页面自身 violation: {own if own else '0 条 ✓'}"
          + (f" | 扩展噪声 (白名单放行): {len(noise)} 条 {noise}" if noise else ""))
    if violations:
        print(f"(诊断) console CSP 红字: {violations}")

    # ── 其他 /web 页: 未被 CSP 外溢误伤 ──
    pg.goto(OLD, wait_until="networkidle")
    pg.wait_for_timeout(1000)
    print(f"其他页(index)渲染: title={pg.title()[:12]} | pageerrors={errs if errs else '无 ✓'}")
    b.close()

ok = (cards == 46 and groups == 9 and prog_ok and dlg_ok
      and not own and not errs)
print("VERIFY", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
