# 已完成-20260808-tts_client-架构重构

**文件**: `scripts/tts_client.py`(1140 行 → shim 62 行)+ `scripts/tts_lib/` 包(11 个模块,共 ~1470 行)
**日期**: 2026-08-08
**范围**: 第二批「核心服务层重构」第 1 个文件

## 目标

将 1140 行的 `scripts/tts_client.py` 拆分为扁平、类 DDD 的包结构,满足大文件重构硬性约束(文件 ≤250 行 / 函数 ≤40 行 / 类 ≤200 行 / 绝对导入 / 策略模式 / 无死代码 / 语义逐字不变)。

## 拆分结果

| 模块 | 行数 | 职责 |
|---|---|---|
| `tts_lib/constants.py` | 22 | URL / 输出目录常量 |
| `tts_lib/http.py` | 54 | msgpack 打包 + urllib POST 传输层 |
| `tts_lib/text.py` | 159 | 分段 / 净化 / 批量分组(纯字符串逻辑) |
| `tts_lib/audio.py` | 130 | WAV 拼接 + FFmpeg 参数滤波 |
| `tts_lib/silence_split.py` | 163 | 按静音切分逐行 WAV |
| `tts_lib/engines.py` | 204 | 三个引擎策略(fish / f5 / indextts) |
| `tts_lib/orchestrator.py` | 217 | 策略表回退调度 + `_synthesize_single` |
| `tts_lib/segments.py` | 55 | 长文分段合成 + 拼接(synth_fn 注入) |
| `tts_lib/lines.py` | 249 | 逐行批量合成 + manifest + 失败清理 |
| `tts_lib/cli.py` | 99 | argparse CLI 复刻 |
| `tts_lib/__init__.py` | 51 | 聚合导出(`__all__` 18 符号) |
| `tts_client.py` | 62 | **弃用 shim**:sys.path 注入 + 转发 |

**原文件备份**: `scripts/tts_client.py.bak`(1140 行,未删)。

## 关键设计决策

1. **策略模式回退**(orchestrator.py):
   - `_BACKEND_ORDER: dict[Backend, tuple[str, ...]]` = `auto→("fish","f5")`、`fish→("fish",)`、`f5→("f5",)`、`indextts→("indextts",)`。
   - `_make_engine_callbacks` 返回 `dict[str, Callable[[], Path]]` 零参数回调表,`_run_engine` 统一执行 + 应用 FFmpeg 参数。
   - **错误消息逐字一致**: 单后端失败重抛原始异常(非聚合);auto 全败 `RuntimeError("All TTS backends failed:\n" + fish:…\nf5:…)`。
2. **synth_fn 注入**(segments.py): `_synthesize_segments(synth_fn=…)` 接收 `_synthesize_single`,避免 segments ↔ orchestrator 循环导入。
3. **`_SynthesisParams` frozen dataclass**(lines.py): 打包 11 字段,压缩 `_skip_batch`/`_run_batch` 调用。
4. **`_single_kwargs` 参数打包**(orchestrator.py): `synthesize` → `_synthesize_single`/`_synthesize_segments` 共用同一 kwargs dict。
5. **死代码清理**: 原版 `apply_ffmpeg_params` 内 `import math` 从未使用,已去除。

## 行为契约(逐字验证通过)

- 8 项 test_tts_client.py 断言全部复现通过:
  - 6 个必需函数可调用;indextts_tts 签名含 master 三参数;
  - master_audio=None → `RuntimeError(match="master_audio")`;
  - synthesize / _synthesize_single / synthesize_lines 签名含 master 三参数;
  - `inspect.getsource(_synthesize_single)` 含 `'"indextts"'`(docstring 保留字面量);
  - CLI `--backend` choices 含 indextts + `--master-audio/-text/-style` flags;
  - `DEFAULT_INDEXTTS_URL == "http://127.0.0.1:7862"`;`DEFAULT_OUTPUT_DIR` 含 `"digital_human"`。
- 3 条运行时错误路径(手工综合脚本,连真实网络失败):
  - auto 双后端失败 → 聚合消息 `fish: …` → `f5: …` 顺序正确;
  - fish 单后端失败 → 重抛原始 `requests.ConnectionError`(非聚合);
  - indextts 缺 master → `"indextts: …master_audio…"` 透传。
- import 链路无循环: `tts_client`(shim)→ `app.main` / `app.services.tts_service` / `app.routers.voices` 全部通过(uvicorn 包模式)。
- 4 处 app 调用点(tts_service:100、voices:146/241/359)kwargs 全部匹配新签名。

## 验证命令

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m py_compile scripts/tts_lib/*.py scripts/tts_client.py
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe  # + 上述手工综合断言脚本
```

## 遗留说明

- `_synthesize_single` 比原版多一个 `voice_id` 位置参数(原版没有),函数内未直接使用,由参数打包链路透传并忽略。这是有意设计 — 保持 `_single_kwargs`/segments/synth_single 的转发简单,且不违反任何测试契约。
- 本会话顺带修正了第一批遗留: `audio.py` 原 282 行、`apply_ffmpeg_params` 79 行 / `_split_wav_by_silence` 127 行均超限,已拆出 `silence_split.py` 并细分函数,全包 11 模块现在全部满足硬性约束。

## 下一步

第二批第 2 个文件: **slot_workflows(920 行)** 诊断与拆分。
