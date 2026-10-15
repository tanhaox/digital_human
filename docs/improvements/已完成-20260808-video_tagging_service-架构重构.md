# 已完成-20260808-video_tagging_service-架构重构

**日期**: 2026-08-08
**范围**: `app/services/video_tagging_service.py`(828 行)→ `app/services/video_tagging_service/` 包(11 模块 1270 行)
**状态**: ✅ 已完成

## 背景

原单文件 `video_tagging_service.py` 828 行违反"文件 ≤250 行"硬性约束,且含 6 个 >40 行超限函数。按标准重构流程(诊断 → 蓝图 → 拆分 → 验证)拆为同名包。

## 变更摘要

| 模块 | 行数 | 职责 | 依赖 |
|---|---|---|---|
| `__init__.py` | 36 | 重新导出 4 个公共 API + `__all__` | jobs / single |
| `constants.py` | 115 | 标签枚举 (`ALL_SCENES` 等 6 组)、`SYSTEM_PROMPT`、llama 常量 (`_LLAMA_PORT`/`_LLAMA_HOST`/`_LLAMA_READY_TIMEOUT_SEC`) | — |
| `parse.py` | 142 | LLM JSON 解析 + 规则引擎回退 (`frame_tagging_result` 拆为 5 helper) | constants |
| `llama.py` | 146 | llama-server 生命周期 (`_ensure_llama_server` 拆为 6 helper) | — |
| `legacy_frames.py` | 67 | `_extract_frame_at`/`extract_keyframes`(废弃向后兼容) | — |
| `pipeline.py` | 204 | 4 通道流水线 (`_run_pipeline_for_asset` 编排 + 4 通道 helper) | constants / parse(延迟) / frame_extraction / motion_analysis / ocr_scan / local_llm_client / director_events |
| `store.py` | 57 | `_write_asset_tags`(DB 写回)/ `_rebuild_vocabulary_pack`(延迟 import director_prompt) | database / models / director_prompt(延迟) |
| `jobs_state.py` | 108 | `_jobs`/`_jobs_lock` 全局状态 + 状态机 helper(`_mark_running`/`_fail_startup`/`_update_progress`/`_check_cancelled`/`_mark_final`) | director_events(延迟) |
| `jobs.py` | 155 | 批量任务主循环 `_run_tagging_job_thread` + 公共 API (`start_tagging_job`/`get_job_status`/`cancel_job`) | jobs_state / llama / store / worker / config / database / models / local_llm_client |
| `worker.py` | 162 | 单素材处理 helper 群 (`_process_asset` 编排 + 5 helper) | pipeline / store / jobs_state / asset_tagging / database / models |
| `single.py` | 78 | 同步单素材打标 `tag_single_asset` | llama / pipeline / store / asset_tagging |
| **合计** | **1270** | | |

## 约束达成

- **文件行数**: 全部 ≤250 ✅(最大 204 = pipeline.py)
- **函数行数**: 全部 ≤40 物理行,编排函数例外 ≤70(`_run_tagging_job_thread` 47 / `_process_asset` 44)✅
- **类**: 无自定义类,全部函数式 ✅
- **`__all__`**: 11 模块全部包含 ✅
- **绝对导入**: 全部 `from app.` ✅
- **死代码**: 移除原文件未使用的 `get_session_maker` 导入 ✅
- **行为契约**: SSE 事件文本、状态机、计数语义、异常消息逐字保持(见诊断报告"行为契约"节)✅

## 循环导入检查

- `_jobs`/`_jobs_lock` 独立入 `jobs_state.py`,避免 API/线程间跨模块循环。
- `publish` 在 `jobs_state`/`_fail_startup`/`_check_cancelled`/`_mark_final` 中 lazy import。
- `director_prompt.rebuild_vocabulary_pack` 保持函数内延迟导入(原注释约束)。
- 冷启动 `from app.main import app` 通过,10 子模块全量导入通过,无循环。✅

## 最终文件树

```
app/services/video_tagging_service/
├── __init__.py        # re-export: start_tagging_job / tag_single_asset / get_job_status / cancel_job
├── constants.py       # 标签枚举 + SYSTEM_PROMPT + llama 常量
├── parse.py           # LLM JSON 解析 + 规则回退
├── llama.py           # llama-server 生命周期管理
├── legacy_frames.py   # 废弃抽帧逻辑(向后兼容)
├── pipeline.py        # 4 通道流水线
├── store.py           # DB 写回 + 词表包重建
├── jobs_state.py      # 全局状态 + 状态机 helper
├── jobs.py            # 批量任务主循环 + 公共 API
├── worker.py          # 单素材处理 helper
└── single.py          # 同步单素材打标
```

## 外部引用面(零改动)

`app/routers/tagging.py:17-22`:
```python
from ..services.video_tagging_service import (
    cancel_job, get_job_status, start_tagging_job, tag_single_asset,
)
```
同名包解析优先,原 `.py` 已删除不留 shim。验证 4-name import 通过。✅

## 验证记录

```
FUNCS: ALL OK
__all__: ['start_tagging_job', 'tag_single_asset', 'get_job_status', 'cancel_job']
__all__ match OK
worker/pipeline/store/jobs import OK
app.main cold start OK
all 10 submodules OK
exists before: True → exists after: False (send2trash)
module file: ...\video_tagging_service\__init__.py
tagging.py 4-name import OK
```
