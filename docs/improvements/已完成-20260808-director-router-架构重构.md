# 已完成-20260808-director-router-架构重构

**文件**: `app/routers/director.py`(860 行)→ `app/routers/director_routes/` 包(10 个模块,共 444 行)
**日期**: 2026-08-08
**范围**: 第二批「核心服务层重构」第 3 个文件

## 目标

将 860 行的 FastAPI 路由层 `director.py` 按任务生命周期拆分为扁平包结构,满足大文件重构硬性约束(文件 ≤250 行 / 函数 ≤40 行 / 类 ≤200 行 / 绝对导入 / 无死代码 / 语义逐字不变 / 循环导入经函数体内延迟导入处理)。

## 拆分结果

| 模块 | 行数 | 职责 |
|---|---|---|
| `director_routes/__init__.py` | 14 | `router = APIRouter(prefix="/api/director", tags=["director"])` + include_router 聚合 8 个子模块 + `__all__` |
| `director_routes/common.py` | 106 | `_WORKFLOW_PIPELINE`/`_encode_pipelines`/`_decode_pipelines`/`_job_or_404`/`_slot_or_404`/`_find_composition_mp4`/`_cleanup_job_files` |
| `director_routes/planning.py` | 182 | `create_job` + `_plan_in_background`/`_plan_steps`/`_mark_plan_failed`/`_resolve_audio_id`/`_validate_audio`/`_spawn_plan_thread` |
| `director_routes/execution.py` | 136 | `execute_job`/`_execute_in_background`/`cancel_job` + `_decode_request_pipelines`/`_check_executable` |
| `director_routes/retry.py` | 213 | `retry_slot`/`retry_by_workflow`/`purge_replaced_slots` + `_original_workflow`/`_apply_retry_pipeline_degrade`/`_reset_slot_for_retry`/`_restore_original_workflow`/`_execute_slot_retry` |
| `director_routes/compose.py` | 155 | `compose_job`/`_compose_in_background` + `_compose_start_trace`/`_compose_success`/`_compose_failure` |
| `director_routes/download.py` | 56 | `download_job`/`open_output_folder` |
| `director_routes/stream.py` | 50 | `job_events`(SSE) |
| `director_routes/cleanup.py` | 69 | `delete_job`/`cleanup_jobs` |
| `director_routes/list.py` | 39 | `list_jobs`/`get_job`/`list_slots` |

**已删除**(send2trash 回收站): 原 `app/routers/director.py`(860 行,无 shim — 同 slot_workflows 批次结论)。

## 关键设计决策

1. **按生命周期拆 8 个子模块 + `__init__.py` 聚合**: 端点按「创建/规划 → 列表/详情 → 执行/取消 → 重试/purge → 合成 → 下载 → SSE → 清理」分组。`main.py` 导入从 `from .routers import director` 改为 `from .routers.director_routes import router as director_router`,`app.include_router(director_router)` 不变。
2. **绝对导入修正(本批次关键发现)**: `director.py` 在 `app/routers/` 下用 `..` 解析到 `app`;拆为 `director_routes/` 嵌套包后深度 +1,`..` 解析到 `app.routers` 而非 `app`。9 个子模块全部改为 `from app.xxx` 绝对导入(含函数体内延迟导入),grep 确认无 `from ..` 残留。
3. **FastAPI `_IncludedRouter` 包装**: 新版 FastAPI 将 `include_router` 包装为 `_IncludedRouter`(无 `.path`/`.routes`,有 `.original_router`)。`len(router.routes)` 返回 8(子 router 数)而非端点数;端点检查须遍历 `original_router.routes`。聚合 router 最终暴露 **15 个端点**(与原 director.py 完全一致)。
4. **三态管线语义保持**: `_encode_pipelines`(None→None, 空→"")与 `_decode_pipelines`(None→None, ""→set())逐字不动;`""` 与 `None` 严格区分(全关 vs 全启),落库列 `job.pipelines` 语义不变。
5. **函数体内延迟导入保持**(循环导入防护): `director_events.publish/PlanCancelled`、`director_prompt.build_real_material_catalog`、`composition_service.compose_director_job`、`director_service.append_trace`、`gpu_service_manager`、`config`、`shutil` 等全部原位不动。
6. **线程去重锁跨模块共享**: `_planning_jobs/_planning_lock`(planning.py)、`_executing_jobs/_executing_lock`(execution.py)、`_composing_jobs/_composing_lock`(compose.py)各自独立。`_executing_jobs` 被 retry.py 引用(僵尸检测),通过 `from app.routers.director_routes.execution import _executing_jobs` 共享。
7. **纯工具下沉 common.py**: `_WORKFLOW_PIPELINE`(7-key 映射)、三态编解码、404 守卫、产物查找、文件清理 — 与 slot_workflows 批次 common.py 同理,只放无状态纯工具,不承载端点逻辑。

## 行为契约(逐字验证通过)

对原 `director.py` 15 个端点逐段读取对比,以下全部逐字一致:

- **create_job**: `Script {id} not found`(404)/`AudioFile {id} not found`(404)/`音频文件丢失: {path}`(409)/`audio_file_id 未提供,且找不到该脚本的可用整段音频 (先生成 TTS)`(400) → 自动选整段音频逻辑、响应 `DirectorDirectResponse` message `"规划已提交后台, 请通过 SSE 或轮询查看进度"`。
- **execute_job**: `job 已完成, 不可重复执行`(409)/`job 已失败, 请新建任务`(409)/`job 正在执行中, 请勿重复触发`(409),query 缺省回退 `job.pipelines` + 三态解码。
- **cancel_job**: `job 状态为 {job.status}，无法取消`(409);planning 返回 `"已发送停止信号，规划将在当前步骤完成后中止"`,executing 返回 `"已发送停止信号，将在当前阶段完成后停止"`。
- **retry_slot**: `slot 正在执行中，请等待完成后再重新生成`(409)/`管线 {PIPELINE} 已禁用, 且 {wf} 无可用替代管线`(400)/`retry failed: {exc}`(500),降级顺序 + `_best_fallback_workflow` + black_placeholder 恢复 + Response message 拼接逐字。
- **purge_replaced_slots**: `任务执行中，不可清理`(409),连带删 `composition_output_root/<job_id>/slots/<slot_id>/` 目录。
- **retry_by_workflow**: 僵尸检测(`executing` + 不在 `_executing_jobs` → `reviewing`)/`任务执行中，请等待完成后再重试`(409)/`无 {workflow} 类型的 failed/completed/skipped slot 可重新生成`(404)。
- **compose_job**: `job 无 slot, 无法 compose`(409)/`至少需要 1 个 completed slot 才能 compose`(409)/`合成正在进行中, 请勿重复触发`(409),后台 trace 消息 `合成开始 (crossfade={}s, lufs={})`/`合成完成: {duration:.1f}s → {path}` + 成品登记 `director_{job_id[:8]}` 标题兜底。
- **download/open-folder**: `job 状态 {job.status}, 暂未产出 (需 status=completed)`(409)/`未找到合成产物路径`(404)/`产物文件丢失: {p}`(410)/`产物目录不存在: {folder}`(410)/`打开文件夹失败: {exc}`(500)。
- **job_events(SSE)**: `subscribe`/`unsubscribe`/20s 心跳 `{"type": "heartbeat", "elapsed_sec": ..., "msg": "服务端仍在运行…"}`/break 类型集合 `("exec_done","exec_cancelled","plan_done","plan_cancelled","plan_error","compose_done","compose_error")` 完全一致。
- **delete_job/cleanup_jobs**: `任务执行中，不可删除`(409)/`{"status": "ok", "deleted": ...}`/`{"status": "ok", "deleted_count": ..., "deleted_ids": ...}` 一致。
- **common**: `_WORKFLOW_PIPELINE` 7-key 映射、三态编解码、404 detail(`DirectorJob {job_id} not found`/`DirectorSlot {slot_id} not in job {job_id}`)、`_find_composition_mp4`、`_cleanup_job_files` 逐字一致。
- **规划线程 PlanCancelled 分支**: `j.status in ("planning", "reviewing")`(原 L171)与拆分版 `_mark_plan_failed` 的 `("planning", "reviewing")` statuses 参数一致。

## 验证命令

```bash
cd /f/AI-Agent-Local/digital_human
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m py_compile app/routers/director_routes/*.py
# AST 硬约束: 物理行 ≤250 / 函数 ≤40 / 类 ≤200 / 模块 ≤3 类 → 全 PASS
# 冷启动导入:
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from app.routers.director_routes import router; from app.main import app"
```

- 10 个文件全部 AST_OK;超限端点全部收窄到 ≤40 物理行(create_job=37, execute_job=37, retry_slot=39)。
- 冷启动导入 OK;聚合 router 暴露 15 个端点,`/api/director` 前缀正确。
- 循环导入检查: 删除原 `director.py` 后冷启动 `app.main` 成功,无残留引用。

## 遗留说明

- `_is_executable` 实际命名 `_check_executable`(诊断报告已更正)。
- 相对导入深度陷阱是**全局模式**: 后续拆包时,凡嵌套层级 +1 的位置,原 `..` 相对导入必须整体改为 `from app.` 绝对导入(含函数体内延迟导入),否则冷启动抛 `ModuleNotFoundError: No module named 'app.routers.xxx'`。

## 下一步

第二批第 4 个文件: **composition_service(593 行)** 诊断与拆分。
