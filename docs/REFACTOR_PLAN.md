# digital_human 重构计划

> 依据:[AUDIT-系统地图.md](AUDIT-系统地图.md) + [AUDIT-问题清单.md](AUDIT-问题清单.md)(2026-10-16 只读审计)。
> 本文档 = 审计产出的处置计划:21 批次、四节。执行时逐批小步提交,每批可独立回滚。
> 红线:删除一律走回收站(send2trash);数据库禁破坏性操作;部署走 本地→GitHub→服务器。

## 新旧导演引擎对比结论(孤岛处置依据)

- 旧 `scripts/anim_director.py`(415 行,0914 垂直切片)六项能力——取数/美术圣经/分镜规划/确定性校验/QC 面板/CLI——已全部被 `app/services/anim_pipeline/director2.py`(4294 行,0915 系统化并入)覆盖并扩展:缓存圣经+系列锁、arcs/scene/shot 多层规划、validate_doc/consistency_review/text_layer_lint/prop_lock_audit/visual_ban_scan/duration_audit 六专项审计器。
- 旧版独有仅两点:独立 CLI 入口、硬编码吉普力风(新版圣经已参数化)。
- 调用链实证:旧版全仓**零代码调用**(仅自身 docstring 与 2 篇历史文档文本引用);新版经 anim_service→planner(planner_flow=director2)接主产线,web(/api/anim)与 CLI(run.py plan)双入口;新版零 app.* 顶层 import,符合管线铁律。
- **结论:旧引擎两步走处置(标弃用→观察→send2trash 删),无任何功能损失** → 见 B14。

## 一、总览(21 批一览)

| 批 | 主题 | 风险 | 依赖 | 决策 |
|---|---|---|---|---|
| B01 | 计划落档 + INDEX 登记 | 零 | - | 自行 |
| B02 | 垃圾归档第一步(51 项移 archive,不删) | 低 | - | 自行 |
| B03 | 垃圾真删第二步(send2trash) | 低 | B02 | 🔴 须拍板(时机) |
| B04 | 误落文档归位(boosted_backup txt) | 零 | - | 自行 |
| B05 | docstring 同步(director_routes 8→9) | 零 | - | 自行 |
| B06 | 配置双源收敛(3 key + l0_output_root) | 低 | - | 自行 |
| B07 | 零 app.* import 侵蚀修复(环#1) | 中 | - | 自行 |
| B08 | anim_pipeline↔book_service 环解(环#2) | 中 | - | 自行 |
| B09 | SSE 收编剩余 4 处 | 低 | - | 自行 |
| B10 | jobs.py 终止类型清单单一事实源 | 低 | - | 自行 |
| B11 | book_service↔ppt_pipeline 环解(环#3) | 中 | - | 自行 |
| B12 | slot_executor↔slot_workflows 环处置(环#4) | 中 | - | 🔴 须拍板(A/B 方向) |
| B13 | 三元环 #5/#6 处置 | 中 | B11,B12 | 自行(默认文档化) |
| B14 | 孤岛三件套两步处置 | 低 | - | 🔴 须拍板(删除时机) |
| B15 | ruff 第一波(audio.py S110+BLE001) | 低 | - | 自行 |
| B16 | ruff 第二波(I001/F401/DTZ011/F841) | 低 | - | 自行 |
| B17 | 个案补丁收编(asset_matcher/compliance_gate) | 低 | - | 自行 |
| B18 | main.py rmtree 四类临时目录裁量 | 低 | - | 🔴 须拍板(例外认定) |
| B19 | spiral_check.py 死路径处置 | 零 | - | 自行 |
| B20 | director2.py 4294 行拆分 | 高 | B07-B13 后 | 🔴 须拍板(做否+时机) |
| B21 | 其余大文件梯队拆分(可选) | 高 | B20 | 🔴 再评估(默认不做) |

顺序原则:先零风险文档/清理(B01-B06),再低风险重构(B09-B10,B15-B19),环解类居中(B07-B08,B11-B13),大文件拆分压轴(B20-B21)。

**执行进度** (2026-10-16): B01✅ 681b47c | B02+B04✅ b0ef110 (51项归档+B04随批) | B05✅ ff86a3f | B06✅ 8842abc | B10✅ 51d3ea3 | B19✅ f811130 | B09✅（4 SSE 收编：5d1972e/4fda8a3/48c56c6/f718d40） | B15✅（a21ce02，audio.py 24 处清理） | B16a✅（Wave1-5：5bc2b9b/cb8275e/da4ee6d+cb67c62+b50e4ee+5784ea3/4e73546/c52b580+e66973a，122 文件 283 处+33 alias，app/ I001/F401 归零） | B16b✅（31942e7，DTZ011 5 处全 noqa 本地日语义，0 改 UTC，app/ DTZ011 归零） | B16c✅（0c26284，F841 15+2 连锁处清理：11 删/3 改 _/1 删 as，app/ F841 归零） | B17✅（8aa7578，两处注释固化：ilike Unicode 转义陷阱 + 引号段优先级机理；新发现 library_service:103 同坑留专项）。
pytest 门槛口径: `tests/ -m "not real_api"` 基线 6 failed/217 passed (6 项均为既有失败: test_module_map×3, test_tts_chunk_semantic×2, test_book_audience_map×1), 全仓裸跑含 _ref_* 第三方 94 收集错不属本计划范围。
B03 观察期 2026-10-16 起算 ≥1 周后拍板。待执行: B07/B08/B11/B13(环解) → 拍板批 B03/B12/B14/B18/B20/B21。

## 二、批次明细(B01-B21)

### B01 计划落档 + INDEX 登记
- **目标**:本计划成为正式文档;docs 根遵守「只放 INDEX.md」约定。
- **动作**:REFACTOR_PLAN.md 落 docs/;docs/INDEX.md 加条目(指向本文件+两份 AUDIT)。
- **验证**:INDEX 链接可达。
- **风险/决策**:零风险 / 自行。

### B02 垃圾归档第一步(移不删)
- **目标**:51 项垃圾(48 py + 3 数据残留,清单见附录 A)移入 `scripts/archive/_2026-10_audit/`,保留可回滚。
- **依据**:问题清单一(Top30 + 另 19 + 3 数据残留,约 1.55MB)。
- **动作**:整目录新建+移动(git mv 保留历史);`scripts/__init__.py`/`tools/__init__.py`/tts_lib 等包文件**不动**。
- **验证**:pytest 全绿;后端启动无 import 错(垃圾全在 scripts//tools/,不在 app/ import 链,理论零风险)。
- **风险/决策**:低 / 自行。

### B03 垃圾真删第二步(须拍板)
- **目标**:B02 归档观察一段时间后真删,回收站可恢复。
- **动作**:`send2trash` 批删 archive 目录;🔴 禁 del/rm/os.remove(CLAUDE.md 红线)。
- **验证**:回收站可见条目;scripts/ 目录干净。
- **风险/决策**:低(可恢复)/ 🔴 **须用户拍板:观察期与执行时机**。

### B04 误落文档归位
- **目标**:`docs/improvements/test_input/boosted_backup_dbce2f8e.txt`(3882B,boost 战役残留)移出文档目录。
- **动作**:随 B02 一并移入 archive;test_input 若仅剩它则清空目录。
- **风险/决策**:零 / 自行。

### B05 docstring 同步(director_routes 8→9)
- **依据**:规则冲突②(director_routes/__init__.py:3-15 写 8 分组、缺 jy_export;:25 实际 import、:33-44 实挂 9 个)。
- **动作**:docstring 分组清单补 jy_export,改「9 个分组」。
- **验证**:import 无碍;行号证据已录审计。
- **风险/决策**:零 / 自行。

### B06 配置双源收敛
- **依据**:规则冲突③。
- **动作**:①`base_url_f5`/`indextts25_emo_ref`/`bs1_page_gap_sec` 定 app.yaml 为唯一源,config.py(:271,:274,:276)默认值与 yaml 对齐并注明「以 yaml 为准」;②`l0_output_root` 在 app.yaml 补条目(config.py:93,:297 现仅代码默认,改 yaml 不生效)。
- **验证**:启动日志打印生效值核对;改 yaml 一次冒烟确认生效。
- **风险/决策**:低(默认值已同值则零行为变化)/ 自行。

### B07 零 app.* import 侵蚀修复(环#1)
- **目标**:恢复 anim_service.py:2-4 铁律「管线本体零 app.* import」,_GPU_JOB_LOCK 反向 5 条清零。
- **依据**:规则冲突①/环#1(character_sheets.py:353,435、style_board.py:306,693,820 延迟引 anim_service._GPU_JOB_LOCK)。
- **动作**:_GPU_JOB_LOCK 抽到 `anim_pipeline/gpu_lock.py` 公共模块,anim_service 与管线内 5 处同引一把锁(锁对象单一实例,不改互斥语义);grep 验证 anim_pipeline 内 `from app\.` 归零。
- **验证**:grep anim_pipeline 目录 `from app\.` 顶层引用=0(允许建包前遗留 docstring);k2/h3 相位互斥冒烟(两 job 并发只跑一)。
- **风险/决策**:中(触 GPU 并发闸)/ 自行。

### B08 anim_pipeline↔book_service 环解(环#2)
- **目标**:解 character_sheets.py:125,191 ↔ episode_gen.py:1628 双向引用。
- **依据**:环#2。
- **动作**:`creation_common._llm/_parse_json` 与 `distiller.load_section` 均为通用能力,下沉到双方共用的 neutral 模块(或改回调注入,由外壳 anim_service 传入);反向 episode_gen 引 anim_pipeline.shots 保持(单向许可)。
- **验证**:import 图复扫无环;books setup-full 冒烟。
- **风险/决策**:中(拆书+动画交界)/ 自行。

### B09 SSE 收编剩余 4 处
- **依据**:重复实现#1(_sse.py 工厂仅 1/5 落地)。
- **动作**:stream.py:50、jobs.py:38、ppt.py:676、tagging.py:128 四处内联生成器改用 `_sse.py sse_stream()` 工厂,与 books.py:25,313 同构。
- **验证**:4 端点各触发一次 SSE 冒烟(事件流格式不变形)。
- **风险/决策**:低(行为等价替换)/ 自行。

### B10 jobs.py 终止类型清单单一事实源
- **依据**:规则冲突⑤(jobs.py:15 硬编码 11 类 job 终止类型)。
- **动作**:清单抽公共常量(如 job_events 或独立模块),新增 job 类型时一处维护。
- **验证**:jobs SSE 冒烟;grep 全仓无第二份清单。
- **风险/决策**:低 / 自行。

### B11 book_service↔ppt_pipeline 环解(环#3)
- **目标**:解 slide_render.py:60 ↔ ppt_pipeline.py:54,66,80 双向引用。
- **依据**:环#3。
- **动作**:`_book_brand` 下沉 neutral 模块(book_service 与 ppt_pipeline 均可顶层引用);ppt_pipeline 反向引 persona/slide_render 保留(单向)。
- **验证**:import 图复扫环#3 消失;ppt render 冒烟。
- **风险/决策**:中 / 自行。

### B12 slot_executor↔slot_workflows 环处置(环#4,须拍板)
- **依据**:环#4;host_comfy.py:3 docstring 自证「延迟导入」为刻意设计。
- **动作(两案)**:A=接受现状写 ADR 文档化(host_comfy.py:83 延迟引 _is_cancelled,注明设计意图与改动禁区);B=重构:_is_cancelled 抽回调注册表,slot_workflows 启动时注入,消环。
- **验证**:A=文档落档;B=slot 全链路冒烟(HF/J 线各一 job)。
- **风险/决策**:中(触 GPU 执行核心)/ 🔴 **须用户拍板:A 保守文档化 vs B 重构消环**。

### B13 三元环 #5/#6 处置
- **依据**:环#5(director_service→slot_workflows→slot_executor→回)、环#6(book_service→ppt_pipeline→tts_service→回)。
- **动作**:随 B11/B12 完成后复扫——环#5 随环#4 同方案,环#6 随环#3 同方案;若二元环已解则三元环大概率自动消失;残余部分默认文档化(ADR)不强行重构。
- **风险/决策**:中 / 自行(默认文档化)。

### B14 孤岛三件套两步处置(须拍板)
- **依据**:孤岛清单二(cleanup.py 2175B 零引用 / qc.py 5541B 仅 CLI / anim_director.py 20044B 被取代)。
- **动作**:
  - `cleanup.py`:零引用真死代码→标弃用注释→send2trash;
  - `qc.py`:**保留**(run.py:29,:111-112 CLI 在用),文件头 docstring 标注「仅 CLI 消费,web 不经此」;
  - `anim_director.py`:引擎对比结论支持删除→标弃用→send2trash(无功能损失)。
- **验证**:pytest 全绿;`python -m app.services.anim_pipeline.run qc` 冒烟(qc.py 保留侧);grep 全仓无 anim_director 残留引用。
- **风险/决策**:低(可恢复)/ 🔴 **须用户拍板:删除时机**(建议 B02 归档观察期同节奏)。

### B15 ruff 第一波:audio.py 挂账清理
- **依据**:补丁#1(S110×8 + BLE001×20,:736,:778,:793,:937 附近)。
- **动作**:28 处 except 逐个补日志(logger.exception/warning,禁 console.print 裸吞)——只加日志不改控制流;清空 pyproject 对应豁免行。
- **验证**:`ruff check app/routers/audio.py` 零 S110/BLE001;pytest 全绿。
- **风险/决策**:低(行为不变)/ 自行。

### B16 ruff 第二波:机械修类
- **依据**:补丁#6(I001/F401 未 --fix、DTZ011×5、F841×12)。
- **动作**:`ruff check --fix`(import 排序+未用 import);DTZ011 加 `tz=` 显式时区;F841 删未用变量。逐类一次 commit 便于回滚。
- **验证**:ruff 全绿;pytest 全绿;后端启动冒烟。
- **风险/决策**:低 / 自行。

### B17 个案补丁收编
- **依据**:补丁#3、#4。
- **动作**:①`asset_matcher.py:229` ilike `\uXXXX` 特判——上升为通用工具函数(参 memory: SQLite JSON unicode escape 陷阱),或注释注明陷阱来源;②`compliance_gate.py:24,26` 引号段「0908 实锤」——个案规则移配置(禁用词同源思路,参 compliance_common.json)或注释固化决策理由。
- **验证**:两处单测/冒烟;个案行为不回归。
- **风险/决策**:低 / 自行。

### B18 main.py rmtree 裁量(须拍板)
- **依据**:补丁#5(main.py:267,270,300,309 运行期 rmtree comp/legacy/slots/hf 四类临时目录)。
- **动作(两案)**:A=认定四目录属「build/cache 产物」例外(运行期重建的临时工作区),在 CLAUDE.md/代码注释写明裁量依据,维持 rmtree;B=改 send2trash(代价:运行期删除变慢+回收站堆积大文件)。
- **验证**:A=注释落档;B=启动冒烟观察删除延迟。
- **风险/决策**:低 / 🔴 **须用户拍板:例外认定口径**。

### B19 spiral_check.py 死路径处置
- **依据**:补丁#2(:30 glob 指向已改名目录,现状 0 命中——死路径)。
- **动作**:倾向随 B02 归档(一次性校验脚本,目标目录已不存在);若保留则修 glob 至新路径。
- **验证**:归档则 pytest 全绿(零引用);修复则跑通一次。
- **风险/决策**:零 / 自行(默认归档)。

### B20 director2.py 4294 行拆分(须拍板)
- **依据**:大文件#1(全系统最大风险点,单文件 4294 行聚合圣经/规划/校验/审计)。
- **前置**:B07-B13 环解完成(拆分时 import 图已干净,避免环叠加);先补测试网(validate_doc/六审计器各至少 1 用例)再动。
- **动作(拟)**:按职责拆子模块——bible(圣经推导+缓存锁)/planning(arcs/scene/shot 多层规划)/validators(validate_doc+六专项审计器)/text_layer(文本层归一);director2.py 保留为门面(re-export),对外调用点零改动。
- **验证**:测试网全绿;anim plan/k2 冒烟一集;grep 无残留跨模块私引。
- **风险/决策**:高(动画线大脑)/ 🔴 **须用户拍板:做否+时机**(可长期挂起,仅在需要再动 director2 时顺带做)。

### B21 其余大文件梯队(可选,默认不做)
- **依据**:大文件#2-#10(anim_draft 2020 / episode_gen 1682 / books.py 1575 / audio.py 1451 / anim_service 1375 / style_board 1268 / distiller 1132 / planner 1031 / k2 861)+ 700-800 行梯队 8 个。
- **动作**:默认**不做**——均单职责聚合体(系统地图 §六判定),拆分收益低于风险;仅在后续某文件需实质改动时按 B20 模式顺势拆。
- **风险/决策**:高 / 🔴 **再评估**(须拍板,默认搁置)。

## 三、风险与红线

### 3.1 红线(执行期全程生效)

1. **删除一律回收站**:`send2trash`;禁 del/rm/os.remove(build/cache 产物例外口径见 B18 拍板)。
2. **数据库禁破坏**:禁 DROP/TRUNCATE/无 WHERE DELETE;全部批次不触 DDL。
3. **部署链路**:本地→GitHub→服务器;本计划全部本地改动,不 SSH 直改服务器。
4. **日志规范**:禁 console.log/裸 print 吞错;B15 补日志一律 logger.exception/warning。

### 3.2 风险排序

| 档 | 批次 | 说明 |
|---|---|---|
| 高 | B20/B21 | 动画线大脑拆分,无测试网不动手 |
| 中 | B07/B08/B11/B12/B13 | 环解触核心链路,须冒烟对应产线 |
| 低 | 其余 14 批 | 文档/清理/行为等价替换 |

通用护栏:每批一小步一 commit 可独立回滚;环类批次执行前确认无生成任务在跑(k2/h3/slot job 空闲);pytest 全绿是每批退出门槛。

## 四、暂停点

- **每批一 commit**:批内多动作(如 B16 逐类)可拆多 commit,全部归属该批。
- **拍板点停等**:B03/B12/B14/B18/B20/B21 到批即停,等用户拍板后进下一批。
- **观察期**:B02 归档后建议 ≥1 周再触发 B03 真删拍板;B14 删除时机同节奏。
- **顺序弹性**:零风险批(B01/B04/B05/B19)可穿插;环解类保持 B07→B08→B11→B12→B13 相对次序;B20 严格压轴。

## 附录 A 垃圾分类表(~51 项,归档清单)

> 处置=全部移入 `scripts/archive/_2026-10_audit/`(B02 归档不删;真删另走 B03 拍板)。
> 未注明路径者均在 scripts/;时间集中 2026-08-27~09-08 三场战役(OCR 回听/geo 素材清洗/takaichi)。

| 组 | 数量 | 文件 | 战役 |
|---|---|---|---|
| rlr_ 前缀 | 9 | resplit / recognize_v2 / stage2_parallel / sample50 / recognize_all / reocr / batch_all / batch / pipeline_test | OCR 回听 |
| purge_ 前缀 | 5 | bw_assets / by_keywords / bad_yt / grade_c / ocr_dirty | 素材清洗 |
| geo_ 前缀 | 5 | batch / batch_review / register_clean / status / thin | geo 图库 |
| takaichi 系列 | 8 | _promo_rerun_tk / _rerun_takaichi / _diag_takaichi / _diag_tk2~4 / _final_export_tk / _verify_export_tk | takaichi |
| OCR/ASR 诊断 | 8 | _audit_polyphone / _audit_ocr / _probe_ocr / _gpu_ocr_bench+bench2 / _asr_verify_smoke / _relisten_peek / _revalidate_19cc5080 | OCR 回听 |
| TTS 个案 | 2 | tools/_fix_bleed_328e0093 / tools/_shift_slots_328e0093 | TTS 串句修复 |
| 杂项一次性 | 11 | _vl_compare / _fix_refcard / _sweep_watermark / _t3r3_clues / _test_planner / _wk / _q / _rlr_q / _diag_factory / _prog / tools/_shot | 各战役杂项 |
| 数据残留 | 3 | scripts/.relisten_results.jsonl (981193B) / scripts/.relisten_rediff.jsonl (434017B) / scripts/_t.jpg (39999B) | OCR 回听 |
| **合计** | **51** | 48 py + 3 数据(体积 ~1.55MB 大头在两条 jsonl) | — |

另 1 项误落文档:docs/improvements/test_input/boosted_backup_dbce2f8e.txt(3882B,0811 boost 战役)随 B04 归位,不计入上表。
