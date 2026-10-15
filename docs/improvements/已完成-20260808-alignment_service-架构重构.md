# alignment_service 架构重构 — 变更摘要

> 日期：2026-08-08 ｜ 模块：`app/services/alignment_service.py` (460 行) → 包化 5 模块
> 方法：行为逐字迁移（字节级 diff 验证）＋ AST 硬约束 + 外部引用面零破坏

## 变更摘要

| 新文件 | 行数 | 职责 | 关键内容 |
|---|---|---|---|
| `alignment_service/__init__.py` | 36 | 公共 API + 模块副作用 | `HF_HUB_OFFLINE=1` + `apply_tokenizer_fix` 副作用（须先于 faster_whisper 生效）、re-export 4 符号 + `__all__` |
| `alignment_service/_models.py` | 99 | 模型加载 | faster_whisper lazy import、`_WHISPER_LOCAL_CACHE`、`_MODEL_CACHE`（`compute_type="float32"`）、`_ensure_model` + `_start_heartbeat`（5s 心跳） |
| `alignment_service/_transcribe.py` | 106 | 转录 | `WordSegment` dataclass、`_normalize`、`_transcribe_words`（`word_timestamps=False` + vad_parameters）+ `_collect_words`（每 10 段进度） |
| `alignment_service/_timings.py` | 227 | 时间轴铺排 | `SegmentTiming` dataclass、`_match_segments`（拆 `_empty_timings`/`_alloc_segment`/`_emit_match_progress`）、`align_from_tts_durations`（快路径）+ `_tts_fast_timings`/`_to_timing_dict` |
| `alignment_service/_align.py` | 136 | 对外主入口 | `align_script_segments` + `_validate_audio`/`_align_whisper`/`_success_result`/`_align_error`、`PlanCancelled` 透传 |

原文件 `app/services/alignment_service.py` 已送回收站（send2trash），不留 shim（`alignment_service.py` → 包同名，Python 始终解析到包，shim 为 unreachable dead code）。

## 硬约束达成

| 约束 | 达成 |
|---|---|
| 文件 ≤250 行 | ✓（max 227） |
| 函数 ≤40 行 | ✓ |
| 类 ≤200 行 | ✓（仅纯数据 dataclass） |
| 模块 ≤3 个类 | ✓（每模块 ≤1） |
| `__all__` 齐全 | ✓ 每个模块 |
| 绝对导入 | ✓ `from app.services.alignment_service.*` |
| 循环导入 | ✓ 依赖单向 `_models ← _transcribe ← _timings`, `_align → 三者` |
| `*` 导入 | ✓ 无 |
| 行为逐字 | ✓ 11/11 diff 检查 |

## 验证记录

| 检查 | 结果 |
|---|---|
| AST 硬约束（文件/函数/类/类数） | 0 问题，5/5 模块 ✓ |
| 行为契约断言 | 11/11 PASS |
| 逐字 diff（快路径/错误 dict/匹配分布/进度节流/PlanCancelled/成功路径） | 11/11 通过 |
| 删除原文件后外部引用方导入 | `director_service/_alignment.py` ✓ + 包 `__all__` 4 符号重验 |
| 语法 + 循环导入 | ✓ |

## 外部引用面

- `app/services/director_service/_alignment.py:83-85` — `align_from_tts_durations`、`align_script_segments`
- `app/services/director_events.py:24` — 仅 docstring 提及（`alignment_service -> director_service`），无导入
- 全部零改动（包化保持导入路径不变）

## 行为不变性（逐字保留）

- `word_timestamps=False` + **不传** `suppress_tokens`（空列表会崩 CTranslate2 4.6.0 generate）+ `vad_parameters`（threshold 0.5 / min_speech_duration_ms 250 / max_speech_duration_s 30.0）
- `_MODEL_CACHE` 键 `(size, dev)` 进程级单例；`compute_type="float32"`（CTranslate2 4.8.1 workaround）
- 心跳 `alignment_heartbeat`（5s 间隔，elapsed_sec 递增）；`model_load_start`/`model_load_done` 事件
- `_match_segments` 空输入 → 全零 timing；`total_chars==0` → 均分 fallback
- 进度回传节流：转录每 10 段、匹配每 20 段（两处 msg 措辞各自保留）
- `PlanCancelled` 透传（不吞）；其他异常 `logger.exception` + `{ok:False, error:f"{type}: {exc}"}`
- 返回值 dict 键序与形状完全一致（ok/error/word_segments/segment_timings/total_duration_sec/model/device）
- `align_from_tts_durations` 快路径：`model="tts-durations"`、`device="n/a"`
- 模块副作用顺序：`HF_HUB_OFFLINE=1` + `apply_tokenizer_fix` 先于 faster_whisper lazy import

## 备注

- **同名遮蔽陷阱**：`app/services/alignment_service.py` 与新建包 `app/services/alignment_service/` 同名 → 删除原文件后 `from app.services.alignment_service import ...` 才解析到包。原文件已 send2trash（纯反斜杠路径 `r'F:\AI-Agent-Local\...'`，正斜杠会被包装成 `\\?\F:/...` 混合分隔符导致 `GetShortPathNameW` 抛 FileNotFoundError）。
- **跨模块 dataclass `==` 恒 False**：不同模块的 `SegmentTiming`/`WordSegment` 类不同，dataclass 生成的 `__eq__` 有 `other.__class__ is self.__class__` 检查 → diff 脚本用 `flat()` 转纯 dict 比较。
- **绑定导入 patch 目标**：`_align.py`/`_models.py` 用 `from app.config import get_config` 绑定 → 契约断言须 `patch.object(_align, "get_config")`/`patch.object(mod, "get_config")`，不能 patch `app.config.get_config`。
- 验证脚本：`scripts/_ast_check_alignment.py`、`scripts/_verify_alignment.py`、`scripts/_diff_alignment.py`
