# -*- coding: utf-8 -*-
"""anim_rf 重构版浏览器烟测: 真实数据渲染 + 控制台零错误 + 交互抽检.
用法: python web/anim_rf/smoke.py  (需后端 127.0.0.1:54321 在线; book_id 可换)
注意: 本脚本只走只读路径或会触发前端校验失败的路径, 不改任何产线数据."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:54321/web/anim.html?book_id=HBO&ep_index=1"
errors, console, resp_500 = [], [], []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    # 响应级 500 记录 (带 URL): 比 console 文本精确, 支撑按路径豁免 mock 失败
    pg.on("response", lambda r: resp_500.append(r.url) if r.status == 500 else None)
    pg.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type == "error" else None)

    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(1200)

    print("== 基本渲染 ==")
    print("sub:", pg.text_content("#sub")[:70])
    print("counts:", pg.text_content("#counts"))
    scenes = pg.locator(".scene").count()
    cards = pg.locator(".card").count()
    groups = pg.locator(".group-row").count()
    print(f"scenes={scenes} groups={groups} cards={cards}")

    print("== H-3: 视频懒加载 ==")
    vids = pg.locator(".card video")
    n = vids.count()
    none_pre = pg.locator('video[preload="none"]').count()
    posters = pg.locator("video[poster]").count()
    print(f"videos={n} preload=none:{none_pre} poster:{posters}")
    assert none_pre == n, "存在未设 preload=none 的视频"

    print("== H-1: 无非法时间码 (:60 不存在, 进位为 01:00) ==")
    board_txt = pg.text_content("#board")
    bad = [seg for seg in board_txt.split() if seg.endswith(":60")]
    print("含 :60 片段:", bad if bad else "无 ✓")

    print("== H-2: h3-retry 走 store (勾选不报错, 按钮态重算) ==")
    pg.check("#h3-retry")
    pg.wait_for_timeout(100)
    pg.uncheck("#h3-retry")
    print("  checkbox toggle OK; btn-h3 disabled:", pg.locator("#btn-h3").is_disabled())

    print("== L-3: 弹窗 Enter 提交 (劫持主按钮为关闭, 端到端验证不触后端) ==")
    pg.locator(".card button[data-act=textLayer]").first.click()
    pg.wait_for_timeout(200)
    assert pg.locator("#dlg").evaluate("d => d.open"), "textLayer 弹窗未开"
    pg.evaluate("() => { document.querySelector('#dlg button.primary').dataset.act = 'dlgClose'; }")
    pg.locator("#dlg input[data-k=text]").first.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(200)
    enter_ok = not pg.locator("#dlg").evaluate("d => d.open")
    print("  Enter → primary 点击 → 弹窗关闭:", enter_ok)

    print("== M-3: 弹窗关闭后焦点归还触发按钮 ==")
    pg.locator(".card button[data-act=textLayer]").first.click()
    pg.wait_for_timeout(200)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    # 注意: Locator 不能直接当 evaluate 参数 (序列化后恒不等), 用选择器比较
    same = pg.evaluate(
        "() => document.activeElement === document.querySelector('.card button[data-act=textLayer]')")
    print("  焦点回到触发按钮:", same)

    print("== 动画拍弹窗 (常规回归) ==")
    pg.locator(".card button[data-act=animEdit]").first.click()
    pg.wait_for_timeout(200)
    print("  beats:", pg.locator(".am-row").count())
    pg.keyboard.press("Escape")

    print("== 失败路径A: 对齐试算 500 → toast 报错 + 按钮恢复可点 ==")
    # 注意: playwright glob 的 * 不跨 '/', 拦多级路径须用 **
    pg.route("**/api/anim/ep/**/align",
             lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"mock 500"}'))
    pg.click("#btn-align-dry")
    pg.wait_for_timeout(600)
    toast_a = pg.text_content("#toast")
    btn_recovered = not pg.locator("#btn-align-dry").is_disabled()
    print(f"  toast 含 mock 500: {'mock 500' in toast_a} | 按钮恢复: {btn_recovered}")
    pg.unroute("**/api/anim/ep/**/align")

    print("== 失败路径B: 文字层保存 500 → toast 报错 + 弹窗保留 (输入不丢) ==")
    pg.route("**/api/anim/ep/**/shot/**/text-layer",
             lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"mock 500"}'))
    pg.locator(".card button[data-act=textLayer]").first.click()
    pg.wait_for_timeout(200)
    pg.click("#dlg [data-act=tlAdd][data-col=cap]")   # 先加一行, 保证输入框存在
    pg.wait_for_timeout(100)
    pg.fill("#tl-cap input[data-k=text]", "失败路径测试行")
    pg.click("#dlg button[data-act=tlSave]")
    pg.wait_for_timeout(600)
    toast_b = pg.text_content("#toast")
    dlg_kept = pg.locator("#dlg").evaluate("d => d.open")
    input_kept = pg.input_value("#tl-cap input[data-k=text]") == "失败路径测试行"
    print(f"  toast 含 mock 500: {'mock 500' in toast_b} | 弹窗保留: {dlg_kept} | 输入保留: {input_kept}")
    pg.keyboard.press("Escape")
    pg.unroute("**/api/anim/ep/**/shot/**/text-layer")

    print("== 失败路径C: 网络中断 (route.abort → fetch TypeError) → toast 带'可重试' ==")
    pg.route("**/api/anim/ep/**/shot/**/anim", lambda r: r.abort())
    pg.locator(".card button[data-act=animEdit]").first.click()
    pg.wait_for_timeout(200)
    pg.click("#dlg button[data-act=animSave]")
    pg.wait_for_timeout(600)
    toast_c = pg.text_content("#toast")
    net_ok = "可重试" in toast_c
    print(f"  toast 含 可重试: {net_ok} | 内容: {toast_c[:40]}")
    pg.keyboard.press("Escape")
    pg.unroute("**/api/anim/ep/**/shot/**/anim")

    pg.evaluate("() => window.scrollTo(0, 1400)")
    pg.wait_for_timeout(400)
    pg.screenshot(path=r"F:\AI-Agent-Local\digital_human\.tmp\anim_rf_v2_shot.png")

    print("== 错误统计 ==")
    print("pageerrors:", errors if errors else "无")
    # 失败路径 A/B/C 各产生 1 条浏览器网络层资源日志 — 用"响应级"精确豁免:
    # 只放行命中 mock 端点 (align / text-layer / anim) 的失败, 其余 500/错误全计
    mocked_paths = ("/align", "/text-layer", "/anim")
    res_mocked = [u for u in resp_500 if u.endswith(mocked_paths)]
    res_other = [u for u in resp_500 if not u.endswith(mocked_paths)]
    bad_console = [c for c in console if c.startswith('error') and 'Failed to load resource' not in c]
    print("console errors:", bad_console if bad_console else "无",
          f"| mock 路径 500: {len(res_mocked)} 条 | 非预期 500: {res_other or '无'}")
    b.close()

ok = (not errors and not bad_console and not res_other and len(res_mocked) == 2
      and none_pre == n and enter_ok and net_ok
      and 'mock 500' in toast_a and btn_recovered
      and 'mock 500' in toast_b and dlg_kept and input_kept)
print("SMOKE", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
