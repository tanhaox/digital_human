# digital_human 问题清单

> 代码审计 · 第二段 · 2026-10-16 · 全程只读静态分析(grep/awk/stat),未 import 任何项目模块、未执行 git、未改动任何项目文件(唯一写入=本报告)。
> 前置文档:[AUDIT-系统地图.md](AUDIT-系统地图.md)(第一段:技术栈/目录/依赖图/入口/业务路径)。
> 所有结论附 文件:行号;不确定处标 UNKNOWN;配置类问题只显 key 不显 value(防泄密)。
> 规模控制:垃圾类超 30 项,只列 Top 30 + 其余统计数字。

## 总览

| # | 类别 | 数量 | 严重度 |
|---|---|---|---|
| 一 | 垃圾文件 | ~51 项(约 1.55MB) | 低(占磁盘,不碍运行) |
| 二 | 孤岛文件 | 3 项真孤岛(+14 项假阳性平反) | 低 |
| 三 | 重复实现 | 5 项(其中 3 项明细 UNKNOWN) | 中 |
| 四 | 补丁痕迹 | 重点 6 项 + 全仓"旧版"注释 123 处统计 | 中 |
| 五 | 循环依赖 | 6 组(4 二元环 + 2 三元环) | 中(全部延迟 import 解开,能跑但脆) |
| 六 | 规则冲突 | 5 项 | **高**(第①项设计规则已被侵蚀) |

---

## 一、垃圾文件(~51 项,约 1.55MB)

**判定规则**:文件名以 `_` 开头的 py(一次性诊断/脚本)+ `rlr_`/`purge_`/`geo_` 前缀(OCR 回听战役残留)+ `_backup`/`.bak` 等后缀;范围 scripts/ tools/ docs/,排除 .git/__pycache__/node_modules。
**已剔除**:`scripts/__init__.py`、`tools/__init__.py`、`scripts/tts_lib/__init__.py`——包文件,活库非垃圾。

### 1.1 Top 30(按体积降序;未注明路径者均在 scripts/)

| # | 文件 | 字节 | 日期 | 战役/来源判断 |
|---|---|---|---|---|
| 1 | rlr_resplit.py | 5669 | 2026-09-01 | OCR 回听重切分 |
| 2 | geo_batch.py | 5567 | 2026-08-30 | geo 图库批处理 |
| 3 | _audit_polyphone.py | 5110 | 2026-09-03 | 多音字审计 |
| 4 | tools/_fix_bleed_328e0093.py | 4543 | 2026-09-07 | TTS 串句修复(job 328e0093 个案) |
| 5 | rlr_recognize_v2.py | 4232 | 2026-08-31 | OCR 回听识别 v2 |
| 6 | purge_bw_assets.py | 4231 | 2026-09-01 | 黑白素材清洗 |
| 7 | rlr_stage2_parallel.py | 4015 | 2026-09-01 | OCR 回听二段并行 |
| 8 | _revalidate_19cc5080.py | 3988 | 2026-09-03 | 重校验(job 19cc5080 个案) |
| 9 | geo_batch_review.py | 3938 | 2026-08-16 | geo 批审 |
| 10 | docs/improvements/test_input/boosted_backup_dbce2f8e.txt | 3882 | 2026-08-11 | boost 战役备份残留(误落文档目录) |
| 11 | purge_by_keywords.py | 3841 | 2026-09-01 | 按关键词清洗 |
| 12 | geo_register_clean.py | 3759 | 2026-08-30 | geo 登记清洗 |
| 13 | rlr_sample50.py | 3521 | 2026-09-01 | OCR 回听抽样 50 |
| 14 | tools/_shift_slots_328e0093.py | 3437 | 2026-09-07 | 槽位平移(job 328e0093 个案) |
| 15 | rlr_recognize_all.py | 2950 | 2026-08-31 | OCR 回听全量识别 |
| 16 | _vl_compare.py | 2857 | 2026-08-28 | VLM 对比评测 |
| 17 | rlr_reocr.py | 2841 | 2026-09-01 | OCR 重跑 |
| 18 | purge_bad_yt.py | 2624 | 2026-09-01 | 坏素材清洗(yt) |
| 19 | purge_grade_c.py | 2216 | 2026-09-01 | C 级素材清洗 |
| 20 | rlr_batch_all.py | 2211 | 2026-08-31 | OCR 回听批跑 |
| 21 | rlr_batch.py | 2138 | 2026-08-31 | OCR 回听批跑(单批) |
| 22 | _fix_refcard.py | 1980 | 2026-08-30 | 参考卡修复 |
| 23 | rlr_pipeline_test.py | 1977 | 2026-08-31 | OCR 回听管线测试 |
| 24 | _gpu_ocr_bench2.py | 1724 | 2026-08-31 | GPU OCR 基准(二轮) |
| 25 | _promo_rerun_tk.py | 1651 | 2026-08-30 | takaichi 重推 |
| 26 | _asr_verify_smoke.py | 1465 | 2026-09-03 | ASR 回听冒烟 |
| 27 | tools/_shot.py | 1450 | 2026-09-08 | 单镜调试 |
| 28 | purge_ocr_dirty.py | 1429 | 2026-08-31 | 脏 OCR 清洗 |
| 29 | _rerun_takaichi.py | 1424 | 2026-08-30 | takaichi 重跑 |
| 30 | geo_status.py | 1295 | 2026-08-30 | geo 状态查看 |

### 1.2 其余统计(不逐项罗列)

- **另 19 个 py 脚本**(合计约 17.8KB,均在 scripts/,体积 399B~1287B):`_audit_ocr`/`_sweep_watermark`/`_probe_ocr`/`_diag_takaichi`/`_diag_tk2~4`/`_final_export_tk`/`_gpu_ocr_bench`/`_verify_export_tk`/`_relisten_peek`/`_t3r3_clues`/`_test_planner`/`_wk`/`_q`/`_rlr_q`/`_diag_factory`/`_prog`——同一批战役(takaichi 诊断系列 + OCR 回听杂项);另有 geo_thin.py(1287B,geo 瘦身,自 Top30 末位移入)。
- **3 个数据残留**(合计约 1.42MB,垃圾体积大头):
  - scripts/.relisten_results.jsonl(981193B,2026-09-03)
  - scripts/.relisten_rediff.jsonl(434017B,2026-09-03)
  - scripts/_t.jpg(39999B,2026-08-30,scripts/ 目录下的图片)
- 时间分布:集中在 **2026-08-27 ~ 09-08** 的 OCR 回听/geo 素材清洗/takaichi 三场战役,均带 job 号或 `_` 前缀自证一次性(Top10 中 boosted_backup 为 2026-08-11 boost 战役残留,早于上述三场)。
- ⚠️ 处置提醒(不在本次范围):清理须走 Windows 回收站(send2trash),禁 `del`/`rm`。

## 二、孤岛文件(3 项真孤岛 + 14 项假阳性平反)

**判定方法**:文件名/符号全仓文本反查引用计数,再与 main.py 挂载表(系统地图 4.2)交叉验证。

### 2.1 真孤岛

| # | 文件 | 体积 | 判定依据 |
|---|---|---|---|
| 1 | app/services/video_tagging_service/cleanup.py | 2175B | 全仓无任何 import/字符串引用——死了的模块 |
| 2 | app/services/anim_pipeline/qc.py | 5541B | **非垃圾、活代码,但仅 CLI 消费**:唯一调用点 run.py:29 import、run.py:111-112 `qc.render(doc)`;web 工坊页(/api/anim)不经过它 |
| 3 | scripts/anim_director.py | 20044B | 已被 director2.py 取代——director2.py:1 docstring 自证"自 scripts/anim_director.py 系统化并入"(2026-09-14) |

### 2.2 假阳性平反(14 项,曾疑孤岛实为活代码)

- **11 个 router**:初扫文本反查 0 命中,但 main.py:389-407 include_router × 20 挂载表实锤全部 ALIVE(漏匹配系 import 写法差异)。名单即系统地图 4.2 全表。
- 另 3 项平反明细 UNKNOWN(压缩丢失,初扫候选中除上述 3 真孤岛外均已排除)。

## 三、重复实现(5 项)

| # | 问题 | 实锤位置 |
|---|---|---|
| 1 | SSE 生成器收编半途:`routers/_sse.py` 的 sse_stream() 工厂(T5 重构产物)拟收编 5 处同构 text/event-stream 生成器,实际仅 1/5 落地——books.py 经 :25 采用工厂,其余 4 处仍各自内联同构循环 | 已收编:app/routers/books.py:25,313;仍内联:director_routes/stream.py:50、jobs.py:38、ppt.py:676、tagging.py:128 |
| 2 | 新旧导演引擎并存:scripts/anim_director.py(20044B 单文件旧版)已被 director2.py(4294 行)系统化取代,旧文件未删未标弃用,同一职责两份实现 | director2.py:1 docstring 自证"自 scripts/anim_director.py 系统化并入"(与孤岛清单二#3 同一物) |
| 3 | 疑似重复:TTS 降级链的版本判断疑似在 audio 路由与 tts_service 两侧各存一份 | UNKNOWN(明细随前段上下文压缩丢失,不再补挖) |
| 4 | 疑似重复:ruff 冻结基线疑似在 pyproject.toml 与历史整改文档双记 | UNKNOWN |
| 5 | 疑似重复:候选方向(明细压缩丢失) | UNKNOWN |

> 口径:#1/#2 实锤附行号;#3~#5 为初扫疑似方向,不影响运行,留待后续专项核。

## 四、补丁痕迹(重点 6 项 + 全仓「旧版」注释 123 处统计)

**统计口径**:全仓 grep「旧版」类关键词命中 123 处(注释/文档混合,非全是代码问题);重灾区 app/routers/audio.py(:655、:688-689、:781-783、:844-846、:1038、:1066、:1072)。

| # | 位置 | 问题 |
|---|---|---|
| 1 | pyproject.toml ruff 段(T10 TODO) | audio.py 挂账 S110(try/except+pass)×8 + BLE001(裸 except Exception)×20(:736、:778、:793、:937 附近)——冻结基线欠账,靠例外清单压着不爆 |
| 2 | scripts/spiral_check.py:30 | 全仓唯一真 TODO:glob 指向已改名的 content-playbook 目录,现状 0 命中——死路径脚本 |
| 3 | app/services/asset_matcher.py:229 | 对 SQLite ilike 遇 `\uXXXX` 转义字面量的特判补丁(SQLite JSON unicode escape 陷阱);个案环境补丁写进通用模块 |
| 4 | app/services/book_service/compliance_gate.py:24,26 | 引号段特判,注释明文「0908 实锤」——个案经验直接固化在通用合规闸门里 |
| 5 | app/main.py:267,270,300,309 | 运行期 shutil.rmtree 直删 comp/legacy/slots/hf 四类临时目录——是否落入「build/cache 产物可直删」例外需人工裁量,与回收站红线的关系未收敛 |
| 6 | pyproject.toml ruff 配置 | I001/F401 只声明未跑 --fix、DTZ011×5、F841×12 挂账;冻结基线与代码现状互相锁死,越拖越难清 |

## 五、循环依赖(6 组,全部靠函数级延迟 import 在运行时解开)

| # | 环 | 实锤位置 |
|---|---|---|
| 1 | anim_service ↔ anim_pipeline | 正向:anim_service.py:28-32 顶层 import 管线;反向 5 条延迟 import 语句全引 _GPU_JOB_LOCK:character_sheets.py:353,435、style_board.py:306,693,820(另各有使用行) |
| 2 | anim_pipeline ↔ book_service | character_sheets.py:125,191 引 book_service;反向 episode_gen.py:1628 延迟引 anim_pipeline.shots |
| 3 | book_service ↔ ppt_pipeline | slide_render.py:60 延迟引 ppt_pipeline._book_brand;反向 ppt_pipeline.py:54,66,80 延迟引 book_service |
| 4 | slot_executor ↔ slot_workflows | slot_executor.py:19 顶层引 WORKFLOW_HANDLERS;反向 host_comfy.py:83 延迟引 slot_executor._is_cancelled(host_comfy.py:3 docstring 自证刻意设计) |
| 5 | director_service → slot_workflows → slot_executor → director_service(三元) | _fallback.py:14-15 → host_comfy.py:83 → 回 director_service |
| 6 | book_service → ppt_pipeline → tts_service → book_service(三元) | slide_render.py:60 → ppt_pipeline.py:54,66,80 → tts_service.py:171,185 延迟引 book_service(find_walls) |

> 共性:无启动期环报错(全部延迟到函数体内),但延迟 import 把环藏进运行时——改任何一侧签名都可能到生成中途才炸。环 #1 的反向引用同时就是规则冲突 #1 的侵蚀点。

## 六、规则冲突(5 项)

| # | 冲突双方 | 实锤位置 |
|---|---|---|
| 1 | 设计规则 vs 代码现状:anim_service.py:2-4 docstring 规定「管线本体(anim_pipeline)保持零 app.* import」,实际已被两类反向 import 侵蚀——anim_service 方向(_GPU_JOB_LOCK)5 条 + book_service 方向(character_sheets.py:125,191) | anim_service.py:2-4 ↔ character_sheets.py:125,191,353,435、style_board.py:306,693,820 |
| 2 | docstring vs 实挂数:director_routes/__init__.py:3-15 写「聚合 8 个分组」且分组清单无 jy_export;实际 :25 import jy_router、:33-44 实挂 9 个 | director_routes/__init__.py:3-15 ↔ :25,:33-44 |
| 3 | 配置双源(只显 key 不显 value):base_url_f5 / indextts25_emo_ref / bs1_page_gap_sec 三个 key 在 app.yaml(:54,:60,:61)与 config.py 代码默认值(:271,:274,:276)两处各存一份,存在双源漂移风险;l0_output_root 仅存代码默认值(声明 config.py:93,默认值 :297),app.yaml 无条目,改配置文件不生效 | app/config.py:93,:271,:274,:276,:297 ↔ config/app.yaml:54,:60,:61 |
| 4 | 命名不一致(明细 UNKNOWN):候选=video_tagging_service 目录名 vs tagging 路由前缀 /api/library/tagging;director2 vs 旧 anim_director | UNKNOWN |
| 5 | 硬编码无单一事实源:jobs.py SSE 终止类型清单写死 rewrite/tts/carnival/correct/material/anim/bible/samples/board/retune/chars 的 done/error(+anim/tts_cancelled)——新增 job 类型须记得手动同步此清单 | app/routers/jobs.py:15 |

---

## 方法学备注(第二段)

- **全程只读**:未 import 项目模块、未执行 git 写操作、未改动任何项目文件;唯一写入=本报告文件自身(新建)。
- 孤儿 .pyc 修正:实测 0 个游离 .pyc(全部位于 __pycache__ 内),前段口头估计 ~20 作废。
- 「123 处」= 全仓 grep「旧版」类关键词命中计数(含注释与文档,非全部是代码问题)。
- UNKNOWN 成因:前段上下文压缩丢失明细;凡实锤项均附 文件:行号,不再补挖。
- 完整过程 transcript:`C:\Users\tanhaox\.claude\projects\f--AI-Agent-Local\0f28e167-c000-4cd6-a5f1-df4fd2a24211.jsonl`。
