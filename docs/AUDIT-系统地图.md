# digital_human 系统地图

> 代码审计 · 第一段 · 2026-10-16 · 全程只读静态分析(grep/awk),未 import 任何模块、未执行 git。
> 范围:`app/ config/ web/ scripts/ tests/ workflows/ migrations/ tools/ docs/`(排除 .venv/__pycache__/data/outputs 等)。
> 所有结论附 文件:行号;不确定处标 UNKNOWN。

## 一、技术栈

### 1.1 依赖清单(requirements.txt,全部无版本号)

| 依赖 | 用途 |
|---|---|
| fastapi + uvicorn | Web 框架与 ASGI 服务器(run_web.py 启动) |
| sqlalchemy | ORM;SQLite 引擎 |
| pydantic | schemas/ 请求响应模型 |
| pyyaml | config/app.yaml 配置加载 |
| requests + httpx | 出站 HTTP(LLM/TTS/ComfyUI 调用) |
| soundfile + numpy | 音频 wav 读写与处理(TTS 产物) |
| msgpack | UNKNOWN(未定位调用点) |
| python-multipart | FastAPI 文件上传 |
| jsonschema | 结构化输出校验(分镜/JSON 契约) |
| Pillow | 图片处理(封面/缩略) |
| send2trash | 回收站删除(安全红线落地) |
| agentmemory | UNKNOWN(未定位调用点) |
| pypinyin | 汉字转拼音(TTS 正音,pinyin_fix) |

pyproject.toml 无 `[project]` 段——**非安装包**,仅承载 ruff 规则(select B008/I001/F401/S110/BLE001/DTZ011/F841/RUF100)与 pytest marker `real_api`(2026-09-17 T3 基线)。

### 1.2 端口 / 服务 / 数据库

| 服务 | 地址 | 出处 |
|---|---|---|
| Web 后端 | 127.0.0.1:54321 | config/app.yaml;run_web.py `uvicorn.run("app.main:app", reload=False)` |
| 数据库 | SQLite `data/pipeline.db` | app/config.py:214-216 默认 `sqlite:///{data_dir}/pipeline.db`;app/database.py:271-273 `create_engine(check_same_thread=False)`;app/main.py:48 启动 init_db |
| DeepSeek | api.deepseek.com | config/app.yaml |
| Kimi | api.kimi.com/coding/v1 | config/app.yaml |
| Qwen | config/app.yaml qwen 段 | config/app.yaml |
| 本地 LLM | 127.0.0.1:8080 | config/app.yaml local_llm 段 |
| 智谱搜索 | open.bigmodel.cn | config/app.yaml zhipu 段 |
| ComfyUI(生图/生视频) | 127.0.0.1:8188 | config/app.yaml;输出目录 E:/AI/ComfyUI_windows_portable/ComfyUI/output |
| fish TTS | 127.0.0.1:7860 | config/app.yaml defaults |
| f5 TTS | 127.0.0.1:7861 | 同上 |
| IndexTTS | 127.0.0.1:7862 | 同上 |
| ffmpeg | 本地 PATH,fallback C:\Programs\ffmpeg\bin | run_web.py 启动检查 |

默认 host_id "laochen",max_host_slots 4(config/app.yaml)。

## 二、目录职责

### 2.1 app/(核心)

| 目录 | 文件数 | 行数 | 职责 |
|---|---|---|---|
| app/ 根 | 4 | 1138 | main.py 449(装配+启动)、config.py 378(配置加载)、database.py 311(引擎/会话)、__init__.py |
| infrastructure/ | 2 | 231 | 基础设施胶水 |
| models/ | 8 | 966 | SQLAlchemy 表模型 |
| routers/ | 32 | 9626 | HTTP 端点层(薄壳,逻辑多下沉 service) |
| schemas/ | 10 | 1393 | Pydantic 请求/响应模型 |
| services/ | 199 | 55865 | **全部业务逻辑**(12 子包 + ~54 平铺模块) |

services/ 12 子包:alignment_service、anim_pipeline(动画管线本体)、book_service(拆书五步流)、composition_service、director_parser、director_prompt、director_service、gpu_service_manager、jy_draft_service(剪映草稿)、pexels_service(素材搜索)、slot_workflows、video_tagging_service。
平铺关键模块:anim_draft、anim_service(动画外壳)、comfyui_client/comfyui_service、compliance、dhv_service(数字人合成)、evidence_service、job_events、director_events、llm_service、local_llm_client、material_service/material_ingest_service、pinyin_fix、prompt_guard、ppt_pipeline/ppt_service、shot_contract、slot_executor、tts_service/tts_verify、visual_render_service、voice_service、zhipu_search、boost_service 等。

**核心=services/(占 app/ 行数 92%);routers/ 是薄壳;models+schemas 是契约。**

### 2.2 非 app 目录(审计范围内)

| 目录 | py 文件 | py 行数 | 说明 |
|---|---|---|---|
| web/ | 3 | 357 | 多页前端(anim.html、books*.html、audio.html、director.html、bs1_story.html、app.js 等);静态挂载于 /web(app/main.py:429) |
| scripts/ | 96 | 10700 | 大量一次性/诊断脚本(_diag_*/_audit_*/_probe_* 前缀),部分已被系统化并入 app/(如 director2 自 scripts/anim_director.py 并入) |
| tests/ | 24 | 3373 | pytest;real_api marker 隔离真实 API 用例 |
| workflows/ | 0(19 个 json) | 0 | ComfyUI 工作流 JSON |
| migrations/ | 3 | 165 | 数据库迁移 |
| tools/ | 8 | 1016 | 辅助工具 |
| docs/ | 25 | 9959 | 文档为主(含少量 py 脚本副本) |
| crawler/(范围外顺带发现) | 8 | 995 | 爬虫脚本 |

## 三、模块依赖(services/ 静态 import 分析,139 条跨包边)

### 3.1 被依赖最多(fan-in Top 5)

| 模块 | 被引次数 |
|---|---|
| models | 8 |
| pinyin_fix | 7 |
| gpu_service_manager | 7 |
| director_events | 7 |
| config | 7 |

(次席:boost_service 5、video_validator 4、book_service 4)

### 3.2 依赖最多(fan-out Top 5)

| 模块 | 引出次数 |
|---|---|
| ppt_pipeline | 12 |
| book_service | 11 |
| slot_workflows | 9 |
| video_tagging_service | 8 |
| tts_service | 8 |

### 3.3 循环依赖(全部以函数级延迟 import 在运行时解开)

**二元环 4 组:**

1. `anim_service ↔ anim_pipeline`:anim_service.py:28-32 顶层 import h3/k2/planner/shots/config;反向 anim_pipeline/character_sheets.py、style_board.py 引回外壳(具体行号 UNKNOWN,待补验)
2. `anim_pipeline ↔ book_service`:character_sheets.py:125 `from app.services.book_service.creation_common import _llm, _parse_json`、:191 引 distiller.load_section;反向 episode_gen.py:1628 引 anim_pipeline.shots(延迟)
3. `book_service ↔ ppt_pipeline`:slide_render.py:60 引 ppt_pipeline._book_brand(延迟);反向 ppt_pipeline.py:54/66/80 引 book_service persona/slide_render(延迟)
4. `slot_executor ↔ slot_workflows`:slot_executor.py:19 顶层 `from app.services.slot_workflows import WORKFLOW_HANDLERS`;反向 slot_workflows/host_comfy.py:83 延迟引 slot_executor._is_cancelled——host_comfy.py:3 docstring 明文记录"延迟导入"是刻意设计

**三元环 2 组:**

- director_service →(_fallback.py:14-15 引 slot_workflows.hf_chart/hf_extract)slot_workflows →(host_comfy.py:83)slot_executor → director_service
- book_service →(slide_render.py:60)ppt_pipeline → tts_service →(tts_service.py:171,185 延迟引 book_service.module_map.find_walls)book_service

**意外发现(设计侵蚀)**:anim_service.py:2-4 docstring 规定"管线本体(anim_pipeline)保持零 app.* import、状态机全在 shots.json",但 character_sheets.py:124-125/191/195-196 已出现 app.services.* import——该设计规则已被侵蚀。

## 四、入口与出口

### 4.1 启动链

`run_web.py`(sys.path 注入 → load_config(config/app.yaml) → ffmpeg PATH 检查)→ `uvicorn app.main:app` → app/main.py:48 `init_db()` → main.py:389-407 `include_router` × 20 → main.py:429 挂载 `/web` 静态站。

### 4.2 Router 挂载表(20 个)

| Router | 前缀 | 定义处 |
|---|---|---|
| anim | /api/anim | app/routers/anim.py:27 |
| articles | /api/articles | app/routers/articles.py:31 |
| audio | /api/audio | app/routers/audio.py:23 |
| books | (无前缀,绝对路径) | app/routers/books.py:29 |
| comfyui | /api/comfyui | app/routers/comfyui.py:36 |
| digital_human_video | /api/dhv | app/routers/digital_human_video.py:45 |
| hosts | /api/hosts | app/routers/hosts.py:16 |
| jobs | /api/jobs | app/routers/jobs.py:12 |
| library | /api/library | app/routers/library.py:34 |
| materials | /api/materials | app/routers/materials.py:33 |
| personas | /api/personas | app/routers/personas.py:11 |
| ppt | /api/ppt | app/routers/ppt.py:34 |
| reverse | /api/reverse | app/routers/reverse.py:41 |
| roles | /api/roles | app/routers/roles.py:41 |
| scripts | /api/scripts | app/routers/scripts.py:22 |
| tagging | /api/library/tagging | app/routers/tagging.py:26 |
| tts_services | /api/tts-services | app/routers/tts_services.py:8 |
| visual_render | /api/visual-render | app/routers/visual_render.py:49 |
| voices | /api/voices | app/routers/voices.py:25 |
| director_routes | /api/director | app/routers/director_routes/__init__.py:32(子路由 cleanup/compose/download/execution/jy_export/list/planning/retry/stream) |

books.py 无前缀,端点为绝对路径(如 POST /books:499、POST /distill/start:240)。

### 4.3 SSE / job 通道

- **双事件总线**(语义不同,REFACTOR_PLAN T16b 明文不合并):
  - `job_events.py`:job_id → list[asyncio.Queue],容量 64,put_nowait,线程锁——后台线程 → SSE 桥
  - `director_events.py`:线程安全 pub/sub(publish/subscribe/unsubscribe),set_event_loop + call_soon_threadsafe,容量 128
- **SSE 端点 5 处**(text/event-stream):books.py:313(/distill/events)、director_routes/stream.py:50、jobs.py:38、ppt.py:676、tagging.py:128
- `routers/_sse.py` 提供 sse_stream() 工厂(T5 重构,拟收编五份同构生成器),**但当前仅 books.py:25 实际采用**——收编未完成,其余 4 处仍为各自内联实现
- jobs.py:15 `GET /api/jobs/{job_id}/events` 终止类型清单含 rewrite/tts/carnival/correct/material/anim/bible/samples/board/retune/chars 的 done/error(+anim/tts_cancelled)
- 动画线并发闸:anim_service._GPU_JOB_LOCK,GPU 相位(k2/h3)互斥(anim_service.py:190 附近)

### 4.4 外部出口汇总

LLM ×5(DeepSeek/Kimi/Qwen/本地8080/智谱)+ ComfyUI 8188(生图生视频)+ TTS ×3(fish 7860/f5 7861/IndexTTS 7862)+ pexels_service(素材)+ zhipu_search(搜索)+ ffmpeg(本地合成)。全部出站集中 config/app.yaml。

## 五、核心业务路径

### 5.1 新闻线(文章→成品,6 步)

1. `POST /api/articles`(articles.py:166)存原文;或 `/fetch-url`(articles.py:138)抓取 URL
2. `POST /{id}/deconstruct`(articles.py:212)→ boost_service.deconstruct_article(articles.py:234 延迟 import)→ LLM 解构观众反应 → article.deconstruct_json
3. `POST /{id}/rewrite`(articles.py:251)→ llm_service.LLMService.rewrite_article(articles.py:27 引入,:417 调用;辅以 material_service.build_material_context_block、prompt_guard.wrap_source)→ 改写稿
4. `POST /api/audio/scripts/{id}/generate-audio`(audio.py:57)→ tts_service.TTSService → fish/f5/indextts → wav
5. `POST /api/visual-render/jobs`(visual_render.py:99)+ `/jobs/{id}/generate`(visual_render.py:143)→ visual_render_service → ComfyUI → 帧图
6. `POST /api/dhv/videos`(digital_human_video.py:102)+ `/videos/{id}/generate`(digital_human_video.py:181)→ dhv_service.generate_dhv_video → ffmpeg 合成成品

### 5.2 拆书线(书→剪映草稿,6 步)

1. `POST /books`(books.py:499)建档(source_path)
2. `POST /books/{id}/setup-full`(books.py:142)→ book_service/pipeline.run_book_setup(books.py:166 延迟)级联:reader.clean_book_title → distiller(GemmaClient 全书蒸馏)→ l0.run_l0 → facing.run_facings → _gen_viewpoints(pipeline.py:222)→ fandeng_full → series_outline(pipeline.py:55-205)
3. 风格选型:`/books/{id}/style-board` 系列 9 条(books.py:588-693)→ anim_pipeline/style_board.py(真内容试镜+Kimi 配方)
4. 逐集生成:book_service/episode_gen.py(自称"拆书步骤 5",创作层+编辑层两层契约);books.py:1067 auto_confirm_if_anim_done 自动确认
5. 页面渲染:`POST /api/ppt/{job_id}/render`(ppt.py:123)→ book_service/slide_render.run_bs1_plan(ppt.py:166/261)+ slide_gen;bs1 分流 rerender(ppt.py:492)
6. 导出:`POST /api/ppt/{job_id}/export-jy-draft`(ppt.py:692)→ jy_draft_service → 剪映草稿

### 5.3 动画线(大纲→成片,6 步)

1. `POST /api/anim/ep/{book}/{ep}/plan`(anim.py:100)→ anim_service.start_phase("plan")(anim_service.py:165)→ anim_pipeline/planner.plan(anim_service.py:280,两步法:导演步+转译步)
2. planner_flow 为 director2 时 → anim_pipeline/director2.load_or_derive_bible(anim_service.py:505)推导圣经;bible/品牌句经 brand_mod(anim_service.py:500)
3. 概念图:`/concept` 系列(anim.py:134-177);K2 批生图:`/k2`(anim.py:230)→ k2_mod.run_batch(anim_service.py:589)→ ComfyUI(2ST 两段式高清配方)
4. H3 批生视频:`/h3`(anim.py:243)→ h3_mod.run_batch(anim_service.py:632,643,chain_h3 可接力)→ ComfyUI;k2/h3 同锁互斥
5. 对齐/审校:`/align`(anim.py:396)→ assembler.assemble_episode(anim_service.py:979)+ director2.validate_doc(anim_service.py:994)
6. 装配:`/draft`(anim.py:420)→ anim_draft.build_draft(对齐归真+剪映草稿装配)→ 剪映

单镜修补族端点(anim.py:287-395):text-layer/anim/fix-text/replan/brand-tag/brand-pass/ai-fix-motion。

## 六、大文件清单(app/ 超 800 行,共 10 个,降序)

| # | 文件 | 行数 | 首行 docstring | 职责判断 |
|---|---|---|---|---|
| 1 | app/services/anim_pipeline/director2.py | 4294 | "导演引擎 v2→v3 (0915 三层画面心智模型定稿, 自 scripts/anim_director.py 系统化并入)" | 单镜导演:圣经推导/plan_shot/文本层归一/校验——动画线大脑,也是全系统最大风险点 |
| 2 | app/services/anim_draft.py | 2020 | "动画产线 对齐归真 + 剪映草稿装配 (0914 系统化, P1 段手工流程的机器化)" | 动画线终点站:对齐+剪映草稿 |
| 3 | app/services/book_service/episode_gen.py | 1682 | "拆书步骤 5 — 逐集生成 (创作层 + 编辑层两层契约) + 确认 + 级联重跑" | 拆书线逐集引擎 |
| 4 | app/routers/books.py | 1575 | "拆书项目端点 (2026-08-19) — 5 步创作流 + 级联重跑 + 挂车清单" | 拆书线全部 HTTP 端点(无前缀绝对路径) |
| 5 | app/routers/audio.py | 1451 | "Audio router: TTS generation and downloads." | TTS 任务/文件/锚点/变速全套端点(ruff 挂账 S110+BLE001) |
| 6 | app/services/anim_service.py | 1375 | "动画产线编排服务 — app/services/anim_pipeline 的系统外壳 (0914 系统化)" | 动画线状态机外壳:相位分发/job 锁/审批 |
| 7 | app/services/anim_pipeline/style_board.py | 1268 | "风格选择板 (0922 用户令) — 书级动画风格选型: 真内容试镜 + 库轮询 + Kimi 新配方提案" | 书级风格选型决策链 |
| 8 | app/services/book_service/distiller.py | 1132 | "离线蒸馏器 — 全书 → 精华 txt (规模.txt 式), 本地 Qwen3.6 批量跑" | 全书→精华离线蒸馏(本地 LLM 批量) |
| 9 | app/services/anim_pipeline/planner.py | 1031 | "LLM 规划器 (两步法, 0911 用户架构令) — 导演步 + 转译步" | 动画线起点:大纲→分镜规划 |
| 10 | app/services/anim_pipeline/k2.py | 861 | "K2 批生图 — 2ST 两段式高清配方 (0911 用户 A/B 裁决定稿 B 档)" | 批生图配方与批次执行 |

700-800 行梯队(11-18):ppt_pipeline.py 781、book_service/slide_gen.py 764、material_ingest_service.py 728、template_library.py 725、tts_verify.py 718、ppt_service.py 713、book_service/creation_steps.py 712、anim_pipeline/assembler.py 702。

---

### 审计方法备注

- 依赖图:grep 解析 app/services/ 全部 from/import(含相对导入点数回溯),折叠到子包级,过滤命名空间伪影,得 139 条跨包边。
- 已知局限:scripts/ 诊断脚本未逐个审阅;msgpack、agentmemory 两个依赖的调用点未定位(标 UNKNOWN);anim_pipeline→anim_service 反向引用行号待补验。
