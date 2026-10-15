# 已完成-20260808-director_service-架构重构

**文件**: `app/services/director_service.py`(524 行) → `app/services/director_service/`(8 模块包)
**日期**: 2026-08-08
**阶段**: ✅ 已完成

## 变更摘要

| 模块 | 职责 | 行数 | 关键成员 |
|---|---|---|---|
| `__init__.py` | re-export 公共 API + `__all__` | 28 | 6 公共 API + `_collect_tts_segment_durations`(私有符号被 verify_timeline 引用) |
| `_trace.py` | 环节轨迹 | 60 | `append_trace`(34)/ `get_trace`(3) |
| `_alignment.py` | TTS 快速对齐 + Whisper 兜底 | 114 | `_align_fast_or_whisper`(52, 编排)/ `_collect_tts_segment_durations`(40) |
| `_plan.py` | 主规划编排 | 211 | `create_director_plan`(66, 编排)+ `_resolve_inputs` / `_record_alignment_done` / `_mark_plan_failed` / `_ensure_director_job` |
| `_llm.py` | LLM 工序单阶段 | 78 | `_llm_plan_phase`(53, 编排) |
| `_postprocess.py` | clamp / references 卡 / 落库 | 98 | `_clamp_slot_durations` / `_append_references_slot` / `_persist_plan` |
| `_fallback.py` | 失败槽位替换 | 88 | `replace_failed_slot`(69, 编排) |
| `_lifecycle.py` | 作业生命周期 | 41 | `mark_job_reviewed`(8)/ `complete_job_if_slots_done`(16) |

**设计要点**:
- `create_director_plan`(原 249 行)拆成 **7 阶段 helper**(输入校验 / 对齐轨迹 / 对齐失败 / job 管理 / LLM 阶段 / 后处理),编排函数压到 66 行。
- 3 个编排函数(`create_director_plan` 66 / `_llm_plan_phase` 53 / `replace_failed_slot` 69 / `_align_fast_or_whisper` 52)以 `--orch` 声明,上限 70 全达标。
- `_postprocess.py` 保留 references 卡 15s / `hf_title` / `no_voiceover=True` 逐字逻辑。
- 全部绝对导入 (`from app.services.`),无 `from module import *`。

## 验证记录

| 检查 | 结果 |
|---|---|
| AST 硬约束(8 模块, 文件 ≤250 / 函数 ≤40 / 编排 ≤70) | ✓ 0 问题 |
| py_compile 8 模块 | ✓ 通过 |
| 循环导入检查(包 + 7 子模块 + 外部引用面) | ✓ 无循环 |
| 行为契约断言(`scripts/_verify_director_service.py`) | ✓ 8/8 |
| 新旧逐字 diff(`scripts/_diff_director_service.py`) | ✓ 14/14 (签名/异常消息/trace 结构/fallback 链逐字节一致) |
| 外部引用面零破坏(删除原文件前 + 后) | ✓ 6 处代码引用 + 私有符号 `_collect_tts_segment_durations` 全部通过 |
| send2trash 删除原文件 | ✓ 已移至回收站 |

**逐字 diff 覆盖场景**: 6 函数签名、2 异常消息(mark/complete not-found)、append_trace 输出结构(step/status/detail/keys/ts)、get_trace 空、complete 全完成 → completed、replace_failed_slot 无管线 legacy 链 + enabled_pipelines 动态链。

## 外部引用面 (删除后验证零破坏)

| 引用方 | 符号 |
|---|---|
| `app/routers/director_routes/planning.py` | `create_director_plan` |
| `app/routers/director_routes/compose.py` | `append_trace` |
| `app/routers/director_routes/execution.py` | `mark_job_reviewed` / `append_trace` |
| `app/services/slot_executor.py` | `replace_failed_slot` / `append_trace` / `complete_job_if_slots_done` |
| `scripts/verify_timeline.py` | `_collect_tts_segment_durations`(私有, re-export) |

`get_trace` 无外部引用,但为公共 API 稳定性保留 re-export。

## 行为不变性

- 异常消息/类型逐字: `ValueError(f"AudioFile {id} not found")` / `ValueError(f"DirectorJob {id} not found")` / `f"alignment failed: {err}"` / `f"director agent failed: {type}: {exc}"`。
- 事件消息逐字: `alignment_progress` / `alignment_done` / `llm_start` / `llm_done` / `llm_error`。
- 日志消息逐字: `[director %s] fast path ...` / `[director %s] fast path unavailable ...` / `Slot %s replaced: %s -> %s` / `[director] appended references hf_title slot (%.0fs)`。
- trace step/status/detail 截断(400)/ 序号(n)逐字; prev_trace 保留逻辑逐字。
- fallback legacy 链 / 无管线链 / family_priorities 三组逐字。
