# 已完成：composition_service(593) 架构重构

**日期**: 2026-08-08
**原文件**: `app/services/composition_service.py` (593 行 → 已回收站删除)

## 变更摘要

| 旧文件 | 新文件 | 职责 | 行数 |
|---|---|---|---|
| `composition_service.py` | `composition_service/__init__.py` | 对外唯一导出 `compose_director_job` | 18 |
| — | `composition_service/common.py` | 纯工具: 时间戳/路径/清理/ffmpeg 薄包装/质量层级 | ~110 |
| — | `composition_service/ffmpeg_steps.py` | concat / 音频检测 / loudnorm(两遍+静音兜底) | ~180 |
| — | `composition_service/audio.py` | 主音轨混音 + 分段 TTS timeline + 静音 fallback | ~200 |
| — | `composition_service/slots.py` | Step 1: slot 校验 + 去重 + 缺失 REPAIR | ~120 |
| — | `composition_service/output.py` | Step 5: ffprobe 校验 + manifest + 写库; Step 6 清理 | ~120 |
| — | `composition_service/pipeline.py` | 主编排: compose_director_job | ~230 |

**全部 7 模块 ≤250 行, 所有函数 ≤40 行, 通过 AST 硬约束。**

## 拆分映射

- `_now` / `_job_root` / `_ensure_root` / `_cleanup_intermediates` / `_run_ffmpeg` / `_WF_TIER` / `_HOST_WF` → `common.py`
- `_concat_demuxer_concat` / `_has_audio_stream` / `_loudnorm` / `_loudnorm_measure` / `_is_silent` / `_loudnorm_pass2` / `_copy_fallback` → `ffmpeg_steps.py`
- `_build_slot_segments` / `_resolve_master_audio` / `_audio_status_msg` / `_mix_master_audio` / `_gen_silence_fallback` / `_build_segment_filters` / `_mix_master_track` → `audio.py`
- `_collect_slot_sources` / `_repair_slot_output` / `_dedupe_completed_slots` → `slots.py`
- `_finalize_output` / `_build_manifest` / `_write_manifest_file` / `_finalize_cleanup` → `output.py`
- `_detect_timeline_gaps` / `_maybe_idempotent_return` / `_prepare_slots` / `_run_build_pipeline` / `_compose_failed` / `compose_director_job` → `pipeline.py`

## 行为契约(逐字核验)

- `compose_director_job` 签名/返回值/SSE 事件序列完全不变。
- 幂等守卫: 仅按 `status=="completed"` + 成片存在 + 无 slot `updated_at > completed_at` 判断, 与旧版一致。
- dedup 质量层级 `_WF_TIER` 字典逐字保留。
- REPAIR: `from app.services.slot_workflows import WORKFLOW_HANDLERS` 改为绝对导入(原 `..services` 相对导入在包化后失效); hf_chart/hf_title 传 3 参, 其余 2 参; 异常消息逐字一致。
- `_loudnorm`: 无音频流 → os.replace 复制; 静音检测(input_i < -70 或 "-inf") → 复制; pass1 无 JSON → 复制; 否则 pass2 应用 measured 参数。日志消息逐字一致。
- `_mix_master_audio`(及分段 slot_segments 分支)ffmpeg 命令逐字一致。
- `_finalize_output`: ffprobe 校验 → os.replace → manifest 写 `composition_manifest.json` → job completed 写库。
- `_cleanup_intermediates` patterns 含 `silence_fallback.wav`(素材生命周期策略保留)。
- Step 7 不再删除 slots/(策略升级, 与旧版一致)。
- 失败路径 `_compose_failed`: job failed + 保留旧成片 + 返回 `old_output`。

## 外部调用面(零改动)

- `app/routers/director_routes/compose.py:102` — `from app.services.composition_service import compose_director_job`
- `scripts/verify_retention.py:26` — 同上

同名包解析优先于同名模块 → 调用面自动解析到包, 无需改动。

## 验证

- ✅ py_compile 全部 7 模块通过
- ✅ AST 硬约束: 文件 ≤250 行 / 函数 ≤40 行 全部通过
- ✅ 冷启动 `from app.main import app` 成功
- ✅ `_compose_in_background` / `compose_job` 调用链导入成功
- ✅ 原 `composition_service.py` send2trash 进回收站(遵守红线)
