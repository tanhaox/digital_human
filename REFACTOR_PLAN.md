# REFACTOR_PLAN — digital_human 代码健康化重构

> 生成: 2026-09-17 · 第一轮（等用户确认后开工）
> 诊断依据: [HEALTH_REPORT.md](HEALTH_REPORT.md)（P0/P1/P2 编号在两文档间互引）
> 原则: 行为不变 · 小步快跑 · 每步可验证可回滚 · 不引入新依赖 · 计划里的行号动刀前一律 grep 复核（2026-09-17 教训: T4 范围 3→6 处）

## 冻结基线（T3 · 2026-09-17 · ruff 0.16.5 + pyproject.toml 显式 8 规则）

| 指标 | 数字 |
|---|---|
| `ruff check app/` 总数 | **656**（T4 后; T3 冻结时 657 — voices.py import 块被 T4 顺手排好 -1; 改配置前同规则无豁免口径 856） |
| BLE001 blind-except | 302 |
| I001 unsorted-imports | 128（只计数, 暂不 --fix; T3 时 129, voices.py -1） |
| F401 unused-import | 104（只计数, 暂不 --fix） |
| S110 try-except-pass | 71 |
| RUF100 unused-noqa | 34 |
| F841 unused-variable | 12 |
| DTZ011 date-today | 5 |
| B008 | **0**（171 处全为 FastAPI Depends/File/Body 惯用法, 经 extend-immutable-calls 豁免） |
| pytest `tests/ -m "not real_api"` | **187 passed / 5 deselected / 0 warning**（T3 前 5 条 mark 未注册 warning） |

口径说明: 52 项是 audio.py **单文件**的基线（B008×12/I001×6/F401×3/S110×8/BLE001×20/DTZ011×1/F841×1/RUF100×1）,
不是全仓数; audio.py 的 S110×8+BLE001×20 已按存量豁免挂账 per-file-ignores（TODO(T10)）。**每个任务收尾 ruff
总数不得高于 656**（豁免清理后同步下调本表）。

## 标准验证命令（每个任务收尾必跑）

```bash
P=./.venv/Scripts/python
$P -m pytest tests/ -m "not real_api" -q        # 期望: 187 passed / 0 warning
$P -m py_compile <改动文件...>                   # 语法
$P -c "import app.main"                          # 装配冒烟
$P -m ruff check <改动文件...>                   # 不新增告警（基线 657, 见上表）
```

任务状态: ⬜ 待确认 · 🔄 进行中 · ✅ 完成 · ⏸ 搁置

---

## 第一批 · 止血（P0，预计 0.5 天）

### T1 ✅ 修 P0-1：evidence_service 一行真 bug（2026-09-17 完成）
- **文件**: `app/services/evidence_service.py:397`
- **步骤**: `package.items().append(item)` → `package.items.append(item)`
- **验证**: `pytest tests/test_evidence_service.py -q` 全绿；全套回到 187 pass
- **回滚**: 单行 revert。**风险**: 无。

### T2 ✅ 修 P0-2/P0-3/P1-17：关键路径吞异常加日志（2026-09-17 完成）
- **文件**: `app/routers/audio.py:483-484, 494-495, 880-884, 886-889, 918-931`（终态落库/manifest 回写/.df.json/verify_report 共 8 处）
- **步骤**: 每处 `except Exception: pass` → `except Exception: logger.exception("<具体语境>")`；不改变返回值与状态机走向
- **验证**: 标准四连 + 人工看一眼日志输出格式
- **回滚**: 按文件 revert。**风险**: 低。剩余 ~60 处非关键 pass 在后续批次随文件重构顺手收窄。

---

## 第二批 · 地基（让基线可持续，预计 0.5 天）

### T3 ✅ 修 P2-6/P2-8：固化 lint/测试基线（2026-09-17 完成）
- **文件**: 新建 `pyproject.toml`（ruff 配置：显式 select 当前默认集 + `B008` 豁免 FastAPI 惯用法 + `S110/BLE001` 暂挂 TODO 计数上限）；`pyproject.toml` 或 `pytest.ini` 注册 `real_api` mark
- **验证**: `ruff check app/` 总数从 1434 降到显式豁免后的存量数并**冻结**（基线数字写进本文件）；pytest 0 warning
- **回滚**: 删配置文件即回到现状。**风险**: 无行为影响。

### T4 ✅ 修 P1-4：消灭依赖倒置 — 搬家式，范围 6 处（2026-09-17 完成，SSE 眼验待用户）
- **范围（前摇实证 2026-09-17，grep 亲证）**: `_publish` 导入方共 **6 处** —
  `app/services/anim_service.py:27`（service→router 倒置本体）· `app/routers/audio.py:20` ·
  `app/routers/voices.py:23`（import 后 line 128 `publish=_publish` 回调注入 `voice_service.start_carnival`，注入写法不变）·
  `app/routers/articles.py:28` · `app/routers/scripts.py:18` · `app/routers/materials.py:28`（后四者为 router→router 私有共享）。
  `_job_channels` 仅 jobs.py 内部 4 处引用，无外部摸表，搬迁面干净。
- **方案（搬家式，不做总线合并）**: `_job_channels + _publish` **逐字平移**到新文件
  `app/services/job_events.py`（不重命名、不改逻辑、不改队列容量 64、不改 put_nowait 语义，
  保留原 docstring 与注释）；jobs.py 顶部改 `from app.services.job_events import _publish, _job_channels`
  供 SSE 端点继续使用（re-export 过渡），原定义处删除、不留兼容别名；6 个调用方各改一行
  import 为 `from app.services.job_events import _publish`。一个 commit 收完。
- **总线合并降级 → T16b**: `director_events`（`set_event_loop` 启动钩子 + `call_soon_threadsafe`
  跨线程投递 + 队列 128）与 jobs 总线（工作线程直接 `put_nowait` + 队列 64）语义不同，
  硬合会改投递时序与背压边界，超出本轮"行为不变"，降级为第五批独立任务。
- **验证**: 标准四连 + `grep -rn "from .jobs import _publish\|from app.routers.jobs import _publish" app/` 零命中
  + `grep -rn "_job_channels" app/` 仅存 job_events.py 定义与 jobs.py re-export 行 + ruff 全仓 ≤657
  + 平移前后代码逐字对照 + SSE 对照表（附件 A）逐项打勾 — **5 个任务页人工眼验**（事件流不断、
  心跳可见、14 型终止行为 + 两个怪癖原样：boost_done 与 boost_p* 不在终止列表，发完后不断流靠前端自关）。
- **回滚**: 单 commit revert。**风险**: 低 — job_events.py 在 services 层零依赖，无 import 环。

#### 附件 A · jobs 总线事件 type 全清单（2026-09-17 前摇录制，T4 验证对照基准）

| 发布方 | type（去重） |
|---|---|
| articles.py | rewrite_error / rewrite_progress / rewrite_done / rewrite_chunk / deconstruct_start / deconstruct_done / deconstruct_error / boost_done |
| audio.py | tts_service / tts_progress / tts_done / tts_error / tts_cancelling / tts_cancelled |
| voices.py → voice_service.start_carnival | carnival_service / carnival_progress / carnival_done / carnival_error |
| scripts.py | boost_error / correct_error / correct_chunk / correct_done / boost_done + 动态 `_emit` 经 run_boost 发 boost_p2/p3/p4_* 系 |
| materials.py | material_scan_start / material_scan_progress / material_scan_done / material_scan_error / material_fetch_progress / material_search_start / material_audit_start / material_error / material_done |
| anim_service.py | anim_service / anim_started / anim_progress / anim_log / anim_error / anim_cancelling / anim_cancelled / anim_done（line 217 三元发 cancelled 或 done） |

jobs.py:44 服务端终止列表（14 型）: rewrite_done/error · tts_done/error/cancelled · carnival_done/error ·
correct_done/error · material_done/error · anim_done/error/cancelled。
怪癖（原样保留）: boost_done 及 boost_p* 系不在终止列表。

---

## 第三批 · 机械去重（低风险高收益，预计 1-1.5 天）

### T5 🔄 修 P1-9：SSE 生成器 5 合 1（2a 完成: 工厂 + books 已换已眼验; 余 stream/jobs/ppt/tagging）
- **文件**: 新建 `app/routers/_sse.py`（或 services）；替换 `jobs.py:36-51` / `ppt.py:655-675` / `tagging.py:105-129` / `books.py:295-315` / `director_routes/stream.py:23-48`
- **要点**: 心跳格式两种（`: keep-alive` vs `data:{heartbeat}`）与终止事件列表参数化，**不统一行为只统一代码**；逐端点替换逐端点手验 SSE 流
- **验证**: 标准四连 + 打开 web 各任务页观察事件流不断

### T6 ⬜ 修 P1-16：`db_session().__enter__()` ×7 改 with 块
- **文件**: `app/routers/ppt.py:249,273,388,470,502,576,622`
- **验证**: 标准四连。**风险**: 低（`__exit__` 本就该执行）。

### T7 ⬜ 修 P1-12：JSON 原子写工具 + 推广到真相源写点
- **文件**: 新建 `app/infrastructure/jsonio.py::write_json_atomic`（tmp + os.replace，照抄 `anim_pipeline/shots.py:107` 习语）；先改 manifest 类 5 处：`audio.py` manifest 重写/回写、`episode_gen.py:1109`、`director2.py:354`、`material_ingest_service.py:216,254`
- **验证**: 标准四连 + 单测覆盖原子写（断言临时文件不留残）——本任务自带补测试

### T8 ⬜ 修 P1-11：硬编码路径/端口外移 config
- **文件**: `config/app.yaml` + `app/routers/audio.py:102` + `app/services/anim_pipeline/config.py:48-64`（E:/G:/ 个人路径、timbre/emo wav）+ `voice_service.py:26` 端口清单对齐 `_specs.py`
- **要点**: yaml 加键 + 代码读配置，**默认值保持现值**（行为不变）；anim_pipeline/config.py 的独立体系先只做"路径取默认值改由 app.config 供"的桥接，不整体合并（那是 T15）
- **验证**: 标准四连 + 启动后产一单音频确认落盘路径不变

---

## 第四批 · 结构归位（中风险，预计 2-3 天）

### T9 ⬜ 修 P1-14：main.py 拆出 startup
- **文件**: 新建 `app/services/startup_tasks.py`；`app/main.py:107-321` 的 5 个辅助函数平移（seed/僵尸清理/磁盘清理），main.py 只留 lifespan 调用
- **验证**: 标准四连 + 启动日志比对（清理条目数不突变）

### T10 ⬜ 修 P1-3：`_do_tts` 348 行下沉 service
- **文件**: 新建 `app/services/tts_pipeline.py`（从 `app/routers/audio.py:151-498` 平移）；router 只留参数解析 + spawn
- **要点**: 纯平移不改逻辑，私有名保持；P0/P1 已先修所以迁移的是干净版本
- **验证**: 标准四连 + 产一单 TTS 全流程手验（生成→manifest→display.json→下载）

### T11 ⬜ 修 P1-1：`generate_episode` 962 行第一刀——提示词装配纯函数化
- **文件**: `app/services/book_service/episode_gen.py:158-1119`
- **步骤（三小步各自成 commit）**: ① 抽提示词构造为模块级纯函数（输入人设/合规规则/集数上下文，输出 prompt 字符串）+ **补单测断言 prompt 骨架**；② 抽 9 个调试落盘为 `_dump_debug()`；③ 抽 LLM 三轮调用循环
- **验证**: 每小步标准四连；①的新单测锁住 prompt 关键段（合规硬约束/受众两层/打字卡句在位）

### T12 ⬜ 修 P1-2：anim_draft 对齐算法纯函数化 + DB 范式统一
- **文件**: `app/services/anim_draft.py:634-839`（`align_episode`）、`:170,842`（裸 sqlite3 → ORM 或统一 DAO）
- **步骤**: ① `_char_time_map/_locate/_sentence_segs` 抽纯模块 + 单测（喂假时间轴/假 manifest）；② ffmpeg concat 段改走 `infrastructure/ffmpeg`；③ 裸 sqlite3 收编
- **风险**: 对齐是产线命脉——改前先用现有 ep1/ep2 实数据快照输出比对（改前改后 manifest diff 必须为空）

### T13 ⬜ 修 P1-5/P1-6：私有跨层共享与循环依赖
- **文件**: `ppt.py:29-33`（改走 service 公共接口）；`book_service/slide_render.py`；`director_routes/execution.py:206-207,258-259`（锁归属统一）；anim_service↔anim_draft 环（把共享符号下沉到第三方模块）
- **验证**: `grep -rn "import _" app/routers/ | grep services` 归零 + 标准四连

---

## 第五批 · 收敛平行实现（大活，预计 3-4 天，逐个独立可停）

### T14 ⬜ 修 P1-7：LLM 客户端 7 → 1
- **步骤**: ① 先统一 Bearer 构造/超时/日志为 `llm_service` 公共 helper（不动各调用方重试策略）；② 逐个迁移：reverse_prompt（无重试→获得重试，**行为增强需用户点头**）→ boost → zhipu → local → anim_pipeline/llm；③ 每迁一个跑该模块关联测试
- **风险**: anim_pipeline/llm 的 kimi→qwen→deepseek 三级链是 token 套餐策略，**保持原语义**只换传输层

### T15 ⬜ 修 P1-10：配置三真相源归一
- **步骤**: ① `set_config(load_config())` 17 处逐个查根因（谁改脏了全局配置）→ 修源头后删咒语；② anim_pipeline/config.py 的 E:/G:/ 路径与端口并入 app.config（T8 已桥接）；③ 独立 dotenv 解析器退役
- **风险**: anim_pipeline 自称独立的契约要重写 `__init__.py:5` 文档

### T16 ⬜ 修 P1-8/P1-13/P1-15：ffprobe 收编 + SystemExit → 领域异常 + 任务注册表
- ① ffprobe 三实现 → `infrastructure/ffmpeg.py`（保 proc_registry 可强杀语义）
- ② anim_pipeline 28 处 `raise SystemExit` → `AnimPipelineError` 体系；调用方 `except (SystemExit, ValueError)` 同步收窄
- ③ 4 套内存任务表 → 轻量 `JobRegistry`（先聚合不持久化，持久化另立项——涉及 DB schema，超出本轮"行为不变"边界）

### T16b ⬜ （自 T4 降级）两条 SSE 总线合并评估
- `director_events`（跨线程投递总线: set_event_loop 启动钩子 + call_soon_threadsafe + 队列 128）vs
  `job_events`（工作线程 put_nowait + 队列 64）语义不同; 合并会改投递时序与背压边界，
  须先出行为差异清单并逐项眼验，不在本轮"行为不变"边界内。与 T14（LLM 客户端 7→1）同类。

---

## 第六批 · P2 清扫（穿插进行）

- T17 ⬜ HTTPException 风格统一（kwargs 式为准）+ 分页公共依赖 + `get_or_404` 收敛（P2-1/2/3）
- T18 ⬜ FileResponse 下载 helper ×12（P2-4）；`Path(__file__)` 根推导收敛到 settings（P2-5）
- T19 ⬜ F821 修 TYPE_CHECKING（P2-7）；print→logger 两处（P2-9）；中文数字工具移 `book_service/textops.py`（P2-11）
- T20 ⬜ 根目录取证垃圾清理：40+ `_*.txt/_*.png/_*.log`、5 个 `.tmp_fix_*.py` → `trash` 进 `docs/archive/` 或回收站（P2-10，动前列清单给用户过目）

---

## 明确不做（本轮边界）

1. DB schema 变更 / 任务持久化入库（行为边界外，另立项）
2. 前端 `web/*.html` 改动（除非 SSE 事件格式意外破坏）
3. anim_pipeline 生成语义/提示词内容任何调整（那是产品线，不是重构）
4. `scripts/` `sandbox/` 94+27 个实验脚本的结构整理（只清根目录死料，目录内部不动）
5. 升级依赖版本

## 人工验证清单（自动化覆盖不到的，每批收尾抽验）

- [ ] 启动 `run_web.py`，web 首页可开、任务列表可刷
- [ ] 产一单 TTS：生成成功、manifest/display.json 在位、可下载
- [ ] 拆书页任一书的集列表可开（不真跑生成）
- [ ] SSE 任务页事件流不断（心跳可见）
- [ ] ComfyUI/TTS 外部服务端口清单与 config 一致

## 进度记录

| 日期 | 任务 | 结果 |
|---|---|---|
| 2026-09-17 | 第一轮勘察 | 产出本计划 + HEALTH_REPORT.md，未改代码，等确认 |
| 2026-09-17 | T1 | `evidence_service.py:397` `items()`→`items`，测试套 186→187 全绿 |
| 2026-09-17 | T2 | `audio.py` 6 处关键 `except:pass` → `logger.exception`（区间内实为 6 处非 8，736/778/793/937 留待后续批次）；ruff 64→52 零新增 |
| 2026-09-17 | T3 | pyproject.toml 落地（ruff 显式 8 规则 + B008 immutable-calls 豁免 + audio.py S110/BLE001 挂账 + real_api mark 注册）；全仓基线冻结 657，pytest 187 pass / 0 warning |
| 2026-09-17 | T4 计划修正 | 前摇实证 _publish 调用方 3→6 处；方案复用 director_events→搬家式 job_events.py；总线合并降级 T16b（语义差异在案）；事件 type 全清单录为附件 A（独立 commit，未动代码） |
| 2026-09-17 | T4 执行 | job_events.py 逐字平移（diff exit=0 证明）+ jobs.py 瘦身（删 threading/Any 死 import）+ 6 调用方 import 换线；SSE 端点只 import _job_channels（_publish 在 jobs.py 内零调用, re-export 即死 import 会顶破基线——微偏差已报）；ruff 657→656（voices 顺手 -1），pytest 187/0w；SSE 眼验待用户 |
| 2026-09-17 | T5 第一步 | 五份 SSE 生成器只读差异分析：对照表+全文+差异分类（心跳三族五样/两总线/首帧/断开轮询为语义差异）；结论 5 份全可并入，换序 books→stream→jobs→ppt→tagging；CancelledError 捕获去留待拍板 |
| 2026-09-17 | T5-2a | 新 app/routers/_sse.py 工厂（sse_stream, 7 参: 订阅时序约束收掉 subscribe/channel_key）+ books.py distill_events 换线（26→21 行）；_sse.py 零告警，books 20→19（-1 I001），全仓 656→655，pytest 187/0w；**用户 SSE 眼验通过** |
| 2026-09-17 | 收工 | 会话结束。**下次从 T5-2b (换 stream.py) 续**，其后 jobs→ppt→tagging；CancelledError 拍板仍悬。另: 🔊 TTS 按钮改人工闸门二轮修 (force 全新重建) 在工作区**未 commit 待用户眼验** (web/books_story.js + CHANGELOG.md)；"音频生成开 Chrome"只读排查完毕无代码改动，wmic 指纹验证待用户 |
