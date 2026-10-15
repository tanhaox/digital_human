# HEALTH_REPORT — digital_human 代码健康报告

> 生成: 2026-09-17 · 第一轮勘察（只读，未改任何代码）
> 配套: [REFACTOR_PLAN.md](REFACTOR_PLAN.md)（任务清单与执行状态）
> 依据: 全量目录/配置/入口勘察 + 路由层与服务层两路深扫 + ruff/pytest 实跑基线。所有结论带 file:line 证据。

---

## 阶段 0 · 项目勘察

### 技术栈与启动方式

| 项 | 值 |
|---|---|
| 语言/框架 | Python 3.11+ · FastAPI + uvicorn · SQLAlchemy 2.x ORM · SQLite (`data/pipeline.db`) |
| 启动 | `./.venv/Scripts/python run_web.py` → `http://127.0.0.1:54321/web/index.html` |
| 构建 | 无打包步骤（脚本项目）；入口 `run_web.py` → `app/main.py:create_app()` |
| 测试 | `./.venv/Scripts/python -m pytest tests/ -m "not real_api" -q` |
| Lint | `./.venv/Scripts/python -m ruff check app/`（**无配置文件**，跑 ruff 0.16.5 默认规则） |
| 外部依赖服务 | ComfyUI 8188 · Fish/F5/IndexTTS2 7860-7862/7866 · Gemma llama-server · ffmpeg/ffprobe 二进制 |
| 密钥 | 全部走 `.env`（`app/config.py::_load_dotenv` 注入），代码内无明文 key ✔ |

### 模块地图与数据流

```
run_web.py → app/main.py (create_app + lifespan)
  ├─ app/routers/        31 文件 8,635 行，211 个路由（audio/books/ppt/articles/director_routes/…）
  ├─ app/services/       175 文件 42,379 行，14 子包
  │   ├─ book_service/       拆书: reader → distiller(L0) → episode_gen(逐集) → orchestrator
  │   ├─ anim_pipeline/      动画产线: planner → director2(导演) → k2(生图) → h3(生视频) → tts
  │   ├─ director_service/   导演 Agent 2.0 (plan/lifecycle/fallback/_postprocess)
  │   ├─ slot_workflows/     slot 分发 (host/broll/hf 族)
  │   ├─ jy_draft_service/   剪映明文草稿导出
  │   ├─ gpu_service_manager/ GPU 服务生命周期（全项目唯一有完整生命周期的组件 ★正面样板）
  │   └─ composition_service/ 成片合成 (ffmpeg)
  ├─ app/models/         ORM 按域拆分（assets/content/book/director/roles）
  ├─ app/infrastructure/  ffmpeg.py 统一入口（proc_registry 可强杀）★存在但只有一半人在用
  └─ scripts/ 94 个 · sandbox/ 27 个 · tools/ 8 个（生产工具与实验脚本混居）
```

数据流: 文章/书 → LLM 洗稿/蒸馏 → Script/Segment(DB) → TTS 音频（**manifest.json 为文本真相源**）→ 导演规划(DirectorJob/Slots) → slot 执行(素材/生成) → ffmpeg 合成 → 剪映草稿/成片。

### 关键风险点（勘察结论）

1. **单机硬编码路径**: `E:\数字人计划\`、`E:/AI/ComfyUI…`、`G:/音乐/*.wav` 直写代码，换机/迁移即断。
2. **manifest.json 真相源非原子写**: 长任务中途崩溃留半截 JSON。
3. **内存任务态 ×4 套互不兼容**，重启即丢，靠 lifespan 僵尸清理兜底。
4. **外部 GPU 服务强耦合**：端口散落 16 处硬编码，`gpu_service_manager` 只覆盖部分调用方。

---

## 阶段 1 · 健康基线

| 手段 | 状态 | 结果 |
|---|---|---|
| pytest | ✅ 可跑，5.5s | **186 通过 / 1 失败 / 5 剔除(real_api)** |
| 失败项 | ❌ 已知失败 | `tests/test_evidence_service.py::TestScanSmoke::test_rescan_preserves_vlm_on_failure` — **真 bug**，见 P0-1 |
| ruff | ⚠️ 可跑但无配置 | 1,434 项（BLE001 328 / B008 171 / I001 129 / F401 104 / S110 85 / F821 11…），规则集随 ruff 版本漂移 |
| 路由层测试 | ❌ 零覆盖 | 17 个测试文件全是 service/纯逻辑层 |
| `python -c "import app.main"` | ✅ 可作为冒烟 | （每任务的标准验证之一） |
| 无法验证部分 | — | 外部服务链路(ComfyUI/TTS/Gemma)、剪映草稿真机打开、SSE 推流——只能人工清单验证（见 REFACTOR_PLAN 附录） |

---

## 阶段 2 · 问题清单

### P0 — 真 bug / 数据风险

| # | 问题 | 证据 | 影响 | 建议 | 验证 |
|---|---|---|---|---|---|
| P0-1 | 关系属性当函数调用 | `app/services/evidence_service.py:397` `package.items().append(item)` — `items` 是 relationship 返回 InstrumentedList | 原文 URL 条目补建路径一走到即 TypeError（现有失败测试即为此而写，docstring 注明"真机实证"） | 改 `package.items.append(item)` 一行 | `pytest tests/test_evidence_service.py` 转绿 |
| P0-2 | 终态落库失败被静默吞掉 | `app/routers/audio.py:483-484, 494-495`（cancelled/failed 落库 `except: pass`） | TTS 任务永久卡 running——`main.py:194-225` lifespan 僵尸清理正是在给这个洞打补丁 | 保留捕获但加 `logger.exception` + 状态重试一次 | pytest 全绿 + 日志人工核 |
| P0-3 | 核心产物写失败静默漂移 | `app/routers/audio.py:880-884`（manifest 时长回写 pass）、`:886-889`（.df.json 锚点 pass）、`:924-931`（verify_report 更新 pass） | 后续加速/对齐基于旧值计算，音频参数无声漂移；回听判决丢失但接口报成功 | 同上：加日志 + 失败上抛到响应体 | 人工构造写失败场景看日志 |

### P1 — 架构耦合 / 重复 / 难测试

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P1-1 | **巨函**：`generate_episode` 962 行 | `app/services/book_service/episode_gen.py:158-1119` | 提示词拼装(~400行纯字符串逻辑)+DB+3轮LLM+9个调试落盘在同函数，完全不可单测 |
| P1-2 | **巨函**：`build_draft` 428 行 / `align_episode` 206 行 | `app/services/anim_draft.py:1194-1622, 634-839` | 对齐算法本体是纯逻辑却与 SQLAlchemy/ffmpeg/落盘交织；同文件混 ORM + 裸 sqlite3(170,842) 两套 DB 范式 |
| P1-3 | **巨函下沉**：`_do_tts` 348 行在 router 文件；`reroll_line` 296 行同步 handler 跑 whisper+ffmpeg | `app/routers/audio.py:151-498, 690-985`（其中 8 处 except-pass） | 路由层承载后台 worker；reroll 全链路占满一个请求 worker |
| P1-4 | **依赖倒置**：service 反向 import router | `app/services/anim_service.py:27` `from app.routers.jobs import _publish`（事件总线 `director_events` 明明存在） | 分层破产，测试 service 必须拉起 router 模块 |
| P1-5 | **私有状态跨层共享** | `app/routers/ppt.py:29-33` 读写 `ppt_pipeline._JOBS`；`book_service/slide_render.py` 同样 import `_JOBS/_book_brand`；`director_routes/execution.py:206-207,258-259` 取兄弟模块的锁 | 封装无存，改 service 内部即断 router |
| P1-6 | **循环依赖** ×2（靠函数内延迟 import 苟活） | `anim_service ↔ anim_draft`；`director2 ↔ anim_draft`（`anim_service.py` 6 处 `# 延迟导入避免模块环`） | import 顺序脆弱 |
| P1-7 | **LLM/HTTP 客户端 7 套平行实现** | `llm_service.py:132`（最完善）/ `anim_pipeline/llm.py:37` / `boost_service/llm.py:73` / `zhipu_search.py:132` / `reverse_prompt_service.py:282`（无重试）/ `local_llm_client.py:92` / `distiller.py:165`；重试习语 `for attempt in range` ≥16 处写法各异 | 重试/降级/计费行为不一致，改一处漏六处 |
| P1-8 | **ffprobe 3 个平行实现** + 绕过统一入口 | `infrastructure/ffmpeg.py:30`（正规军）；绕过者 `anim_draft.py:996,1186,1537`、`video_validator.py:26`、`frame_extraction.py:33` | proc_registry 强杀管不住裸进程（force-stop 失效） |
| P1-9 | **SSE 生成器 5 份近拷贝** | `jobs.py:36-51` / `ppt.py:655-675` / `tagging.py:105-129` / `books.py:295-315` / `director_routes/stream.py:23-48` | 心跳/终止事件名各写各的，行为漂移 |
| P1-10 | **配置三套真相源** | ① `app.config.get_config()`；② `anim_pipeline/config.py` 完全独立（自带 dotenv+config.json，`__init__.py:5` 自称"零 app.* import"契约已被 director2.py 6 处击穿）；③ `set_config(load_config())` 重载咒语 **17 处**（director2.py 独占 8 处） | 配置热更新根因未修，人人自我刷新 |
| P1-11 | **硬编码绝对路径/端口** | `audio.py:102` `E:\数字人计划\`；`anim_pipeline/config.py:50-51,63-64` E:/G:/ 个人媒体路径；`127.0.0.1:786x` 散落 16 处（`gpu_service_manager/_specs.py` 与 `voice_service.py:26` 同批服务两份清单） | 迁移即断（docs/status/迁移准备清单.md 早已立项） |
| P1-12 | **JSON 真相源非原子写** | 原子写仅 2 处（`anim_pipeline/shots.py:107`、`director_prompt/_vocabulary.py:230`）；直写 ≥10 处（`episode_gen.py:1109`、`director2.py:354`、`material_ingest_service.py:216,254,394`…） | 崩溃窗口内 manifest 半截 = 音频文本真相源损坏 |
| P1-13 | **SystemExit 当业务异常 28 处** | 全在 anim_pipeline（director2/planner/tts/run） | 库代码穿透框架，调用方被迫 `except (SystemExit, ValueError)`（anim_service.py:576） |
| P1-14 | **main.py 半个运维服务** | lifespan+辅助 258/411 行：`_seed_defaults`/`_cleanup_zombie_*`/`_auto_cleanup_stale_jobs`（含 shutil.rmtree） | 启动逻辑不可测 |
| P1-15 | **内存任务表 4 套 + daemon 线程 10 处各起各的** | `anim_service.py:41` / `ppt_pipeline.py:25` / `book_service/pipeline.py:48` / `video_tagging_service/jobs_state.py:30`；线程散布 asset_quality/distiller/material_ingest/… | 重启丢任务、多 worker 不共享、无统一取消 |
| P1-16 | `db_session().__enter__()` 反模式 ×7 | `ppt.py:249,273,388,470,502,576,622` | 绕过 `__exit__`，factory 的 rollback/cleanup 不执行 |
| P1-17 | **路由层吞异常 27 处 pass** | audio 13 / books 5 / 其余散布（routers 总 `except Exception` 80 处） | 与 P0-2/3 同根，量大单列 |

### P2 — 一致性 / 清理

| # | 问题 | 证据 |
|---|---|---|
| P2-1 | HTTPException 两种风格混用 | 位置参数式 127 处 vs kwargs 式 160 处，同一文件内并存（articles.py vs audio/books/ppt） |
| P2-2 | 分页三套写法无公共依赖 | 裸 `limit=50` / `Query(20,ge=1,le=100)` / `Query(50,ge=1,le=200)` |
| P2-3 | `get_or_404` 样板 10+ 文件手写；两套 helper 并存 | `books.py:203-215` 与 `director_routes/common.py:61-76` |
| P2-4 | FileResponse 下载三行样板 ×12 | anim.py:309、audio.py:611/1061、roles.py:138/160/363… |
| P2-5 | `Path(__file__).resolve().parents[...]` 根推导 ×10 且 parents[1]/[2] 混用 | audio.py:95、books.py:103、reverse.py:44、articles.py:39/68/100/123/444 |
| P2-6 | ruff 无配置文件，1,434 项默认规则告警 | BLE001 328 / I001 129 / F401 104 / S110 85 / F841 12 / F821 11 |
| P2-7 | F811/F821：SQLAlchemy 字符串前向引用未走 TYPE_CHECKING | `app/models/director.py:49-50` 等 11 处 |
| P2-8 | pytest mark `real_api` 未注册（每次跑 5 条 warning） | tests/test_pexels_service.py:102,131,165 |
| P2-9 | print 代替 logger（非 CLI 代码） | `anim_pipeline/tts.py:306-314`（9 个 print）、`video_tagging_service/llama.py:148` |
| P2-10 | 根目录取证垃圾文件 | 40+ 个 `_*.txt/_*.png/_*.log/_slot_contract_v*.json` + 5 个 `.tmp_fix_*.py`（git 未跟踪或已跟踪的死料） |
| P2-11 | 中文数字转换工具混在草稿装配文件 | `anim_draft.py:87-159` |
| P2-12 | B008 函数默认参调用（FastAPI Depends 惯用法为主，需甄别后豁免） | 171 处 |

---

## 总体判断

- **底子不差**：8-9 月已做过一轮包化（pexels/gpu_service_manager/director_parser 等都有"逐字迁移"记录），`director_events` 事件总线、`infrastructure/ffmpeg.py`、`gpu_service_manager` 是三个正面样板——重构方向是**把样板推广到全仓**，不是发明新架构。
- **病灶集中在四条**：① audio.py 一个文件集齐"巨函+吞异常+硬编码+跨层 import"全部症状；② 巨函（962/428/348/296 行）使核心逻辑不可测；③ 配置与路径多真相源；④ LLM/ffmpeg/任务管理各 N 套平行实现。
- **测试真实存在且快**（5.5s/187 条），失败的那条恰好指着真 bug——测试先行有抓手。
