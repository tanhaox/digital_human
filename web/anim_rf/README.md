# anim_rf — 动画工坊重构版

`web/anim.html` (原 866 行单文件) 的模块化重构。**已替换上线 (0919)**：
新页 = `/web/anim.html + anim.css + anim/`（严格 CSP enforce 中），旧页已入回收站；
本目录 `anim_rf/` 退居**测试与工具之家**（tests / smoke / verify / restart / README）。

## 模块图

```
web/anim.html      语义骨架 (data-act, 无 onclick/无内联样式)
web/anim.css       全部样式 (含 reduced-motion / 原内联样式收编)
web/anim/          (含 package.json {"type":"module"} — node 测试解析用)
  context.js       URL 参数 / API 基址 / 媒体版本 (缓存策略)
  util.js          纯函数: esc/tc/statTxt/scenesReady… (无 DOM, 可单测)
  api.js           唯一 fetch 出口 + fileUrl (前端拒绝协议/绝对路径)
  store.js         极简 Store: 发布订阅 + 订阅者错误隔离
  ui.js            toast / <dialog> 封装 (焦点归还+Enter提交) / Disposer
  job.js           JobRunner: SSE+轮询, 熔断/隐藏暂停/面板竞态修复 (全依赖注入)
  topbar.js        顶栏视图 (store 订阅者)
  card.js          镜卡片纯 HTML 生成 (preload=none + poster)
  board.js         三层看板纯函数渲染 (展开态/滚动保留)
  concept.js       立意人闸面板 (AbortController 防竞态)
  dialogs.js       弹窗构建器/收集器 (作用域圈定 #dlg)
  actions.js       动作中枢: 唯一 click 委托 + 错误边界 + 防双击
  actions/flow.js  整集流程 (规划/K2/H3/对齐/…)
  actions/shot.js  镜级 (过审/打回/AI修/文字层/…)
  actions/arc.js   场级
  actions/concept.js 立意面板
  actions/draft.js 剪映草稿
tests/             node --test 单测 (24 用例: 纯函数+渲染+JobRunner)
smoke.py           Playwright 浏览器烟测 (真实数据, 只读路径)
package.json       {"type":"module"} — 仅为 node 测试服务
```

## 验证

```bash
# 单测 (24 用例: esc/tc 进位回归/特区边界/熔断/隐藏竞态/幂等 attach…)
node --test web/anim_rf/tests/*.test.js

# 语法门禁
for f in web/anim/*.js web/anim/actions/*.js; do node --check $f; done

# 浏览器烟测 (需后端 54321 在线; 不改产线数据 — 失败路径全部 mock/abort)
python web/anim_rf/smoke.py
```

smoke 覆盖三层：渲染/交互常规回归 + 失败路径 A(500→toast+按钮恢复) B(500→弹窗保留输入)
C(网络中断→toast 带"可重试")。mock 失败按**响应级 URL** 豁免（只放行 align/text-layer/anim
三端点），非预期 500 照样判失败。
（坑：playwright 路由 glob 的 `*` 不跨 `/`，拦多级路径必须用 `**`。）

## 空窗重启 (CSP 生效) · 三保险

```bash
python web/anim_rf/restart_csp.py --mode report   # 首轮探雷: violation 只上报不拦截
python web/anim_rf/restart_csp.py                 # 确认零违规后正式 enforce
```

一句话结论的**三条门禁**（全绿才算 pass，缺一不是"生效+正常"）：
① CSP 正生效：新页带头/旧页无头；② 无副作用：violation 事件数为 0（不是"页面能打开"）；
③ 功能未坏：smoke PASS + 对齐试算/花字弹窗两条真实路径通。

保险内容：1. **二次探活**（通知到执行间用户可能已起新任务 → 有 running_job 立即退出重挂）；
2. **实时取端口持有者**（`netstat` + 命令行核对防 PID 复用，只杀真正持有者）；
3. **浏览器实开验证**（`verify_csp.py`：`securitypolicyviolation` DOM 事件 + console 双通道、
两条真实交互路径、旧页未误伤；report 模式同样派发该事件，探雷轮即靠它确认零违规）。

**回滚（一行）**：停服后 `ANIM_RF_CSP=0 python run_web.py`（中间件 env 开关，
app/main.py）；观察模式 `ANIM_RF_CSP=report` 只上报不拦截。

## 关键设计

- **单向数据流**: `refresh → store.set → 3 视图投影`; `h3-retry` 复选框也入 store。
- **事件全委托**: `data-act` + dataset 取参 — 无内联 onclick（消灭注入面 + 全局污染 + CSP 不兼容）。
- **媒体版本缓存**: `media.ver` 数据刷新才 bump — 同版重渲染吃浏览器缓存（原 `?t=Date.now()` 全量重下）。
- **视频懒加载**: `preload="none"` + 首帧图 poster，46 镜首屏不再并行拉视频头。
- **JobRunner DI**: fetchJob/cancelJob/dom/notify/pollMs/hideMs/sseFactory 全可注入 → 单测覆盖熔断与面板竞态。
- **CSP**: 后端 `AnimRfCspMiddleware`（app/main.py）对 `/web/anim.html /web/anim.css /web/anim/` 三前缀下发严格头；改后端须重启 54321 生效, 回滚一行 `ANIM_RF_CSP=0`。

## 契约备注

- `audio_ready`: 取 script_id 下**最新 completed** AudioJob — 音频重生成自动切换、失败不回退；
  只有从未有 completed 音频才为 false（`anim_draft.fetch_episode_audio`）。
- 后端 `/shot/{sid}/fix-text` 端点已成孤儿（旧页死代码 `doFixText` 的遗迹），未清理。

## 上线替换清单 — ✅ 0919 已执行 (三门禁在新路径重判全绿)

记录当时操作序列 (回溯用)。**CSP 路径与文件搬迁必须同一批**——分两批会出现
"新页无 CSP"或"旧路径吃新头"的空窗：

1. ✅ 文件搬迁：旧 `web/anim.html` 先入回收站 → `anim.html/anim.css/anim/` 移至 `web/`；
   tests/smoke/verify/restart 留在 `anim_rf/` (导入路径已改 `../../anim/`)。
2. ✅ 同批改 `app/main.py` 中间件为三前缀 `("/web/anim.html", "/web/anim.css", "/web/anim/")`，
   TestClient 预检 6 路径 (4 带/2 不带) 后 restart_csp.py enforce 重启。
3. 旧 `anim.html` 在步骤 1 已被覆盖 → 原文件先进回收站再搬；确认无"同路径双 CSP 头"
   （中间件只 append 一次，`curl -I` 验证）。
4. ✅ 验证：三门禁在新路径重判 — 头对 (anim 三路径带, books 不带) / violation=0 /
   smoke PASS; 单测 24/24 连跑 3 次稳定 (熔断用例余量已放宽)。

## 待办（需后端配合）

- 前端错误上报端点（`/api/frontend-log`）— L-5。
- 任务日志落库存档（任务结束 POST 日志，便于事后排查失败镜）— L-5。
- `/shot/{sid}/fix-text` 孤儿端点清理。
- 失败 toast 分级细化（401/403 引导重新登录 vs 500 提示稍后再试）— 目前网络层 TypeError
  已标"可重试"，HTTP 层统一显示 detail，够用。
