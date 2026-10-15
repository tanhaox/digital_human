# 已完成-20260809-video_tagging-抽帧修复与并发加速

**日期**: 2026-08-09
**范围**: `app/services/video_tagging_service/` (llama / jobs / worker / jobs_state) + `app/services/frame_extraction.py`
**状态**: ✅ 已完成

## 背景

AI 打标流水线跑全量素材时速度慢（llama-server 单 slot 串行），且固定 `MAX_FRAMES=12` 抽帧在低镜头切换素材上浪费 LLM token。本改动分两块：

- **Task A (并发加速)**: llama-server `--parallel 4` + 素材级 4 线程并发
- **Task B (自适应抽帧)**: 按 scdet 场景切换强度自适应帧数档位 (6/8/12)，替代固定 12 帧
- **Task C (400 上下文修复)**: `--parallel 4` 未同步放大 `-c` → 每 slot 仅 8192, 视觉打标请求爆上下文返回 400, 批量失败
- **Task D (残留抽帧清理)**: 进程被强杀时 `TemporaryDirectory` 未删除 → 临时帧 PNG 孤儿残留; 后端启动时自动回收

## Task A — 并发加速

| 文件 | 改动 |
|---|---|
| `llama.py` L84 | `--parallel 1` → `--parallel 4` (4 个 slot, 验证 `/slots` 返回 4) |
| `jobs.py` L95 | `ThreadPoolExecutor(max_workers=4, thread_name_prefix="tagging")` 并发处理素材 |
| `jobs_state.py` | 新增原子 helper `_inc_progress` / `_set_current_asset` / `_get_counts` (全部 `_jobs_lock` 保护, 杜绝多线程丢计数) |
| `worker.py` L110 | 逐条完成日志 `[tagging-job] %s 已完成 %d/%d 条` (后台实时可见进度) |
| `jobs.py` L93/L103 | 起止日志 `ai打标开始: llama-server 就绪, 4 线程并发启动` / `ai打标结束: %d 完成 / %d 失败` |

**关键点**:
- 计数不再靠调用方传快照，改由 worker 内 `_inc_progress` 原子递增，主线程只读终值 — 修复了并发下 done/failed 覆盖丢失。
- 每个素材独立帧前缀 `frame_{aid[:8]}_NNN.png`，并发时互不覆盖/误删 (worker.py `_cleanup_frames` 按前缀清理)。
- SSE msg 契约更新为 `开始 AI 打标 (4通道流水线, 4线程并发), 共 {total} 个素材`。
- 取消: 提交前检查一次，进行中置位不中断在飞 worker，等待自然结束后落 cancelled 终态。

## Task B — 自适应抽帧档位

| 档位 | 触发条件 (scdet 场景计数) | 帧数 | 典型场景 |
|---|---|---|---|
| `single` | `scene_count < 3` | 6 | P 线下载, 长镜头直出 |
| `medium` | `3 ≤ scene_count < 10` | 8 | 一般剪辑素材 |
| `high` | `scene_count ≥ 10` | 12 | 抖音/手动添加的文件夹素材, 镜头狂摇 |

实现 (`frame_extraction.py`):
- `_resolve_frame_tier(scene_count)` 判定档位；`smart_extract_frames(max_frames=None)` → 自动档位。
- scdet 本来就是流水线通道 1 的输入，档位判定复用其结果，**零额外开销**。
- 档位日志 `[抽帧:%s] 场景 %d 次 → %s 档 %d 帧`。

### 顺带修复的 2 个漏帧 bug（真实 bug，本次前已存在）

1. **scdet 重复上报** — 同一场景切点在 0.5s 窗口内重复上报 (4 帧窗口)，提前触发 `max_frames` 截断导致尾部帧丢失。修复: 入口去重规范化 (0.5s 窗口去重后再进 `_build_extraction_timestamps`)。
2. **中间帧补充失效** — `too_close < 0.8` 过滤掉全部候选点，无法填满档位帧数。修复: 双层窗口 (0.8/0.5) 补充段中间帧，保证满帧且全片覆盖。

## 分界说明（重要）

**以 2026-08-09 凌晨 3:00 为界**：
- 该时间点**之前**打的标 (旧逻辑) 可能有轻微漏帧 — 中等/高切换素材漏尾部 1~2 个镜头；单镜头/fallback 素材无影响。
- 该时间点**之后**打的标用新逻辑，**无需重跑**。

用户决策：不重跑旧标，时间当天然分界，之后新增素材自动用修复后逻辑。

## 验证记录

```
Task A: llama-server /slots 返回 4 确认; 旧 server (PID 25676) 按精确 PID 杀后自动拉起新进程
Task B: 档位单元断言通过 (0/2→single 6, 3/9→medium 8, 10/50→high 12)
        4 段视频 → medium 8/8 帧; 12 段不同图案 → high 12/12 帧; 100% 提取率
        前缀隔离确认 (frame_{aid[:8]}_)
        py_compile + 全模块 import 校验通过
```

**测试陷阱备忘**: `testsrc` 静态图案 scdet 检测不到 → 掉 fallback 分支；测试必须用不同图案拼接 (testsrc/smptebars/testsrc2/gradients)。

## Task C — 400 上下文溢出修复

**现象**: 打标批量返回 `400 Client Error`，后台无逐条 `xx/yy` 完成日志，大量素材打标失败。

**根因（实证）**: Task A 把 `--parallel 1` → `4`，但 `-c 32768` 未同步放大。llama-server 将上下文均分给 4 个 slot → 每 slot 仅 **8192**。打标请求携带 6-12 帧 base64 图（~500-800 tokens/帧），prompt 即 6-10k tokens，加 `max_tokens=4096` 输出 → **必然超过 8192** → `exceed_context_size_error` 400。

复现原始错误体:
```
400: request (10312 tokens) exceeds the available context size (8192 tokens)
```

**修复**: `llama.py` `-c 32768` → `-c 131072`（每 slot 8192 → 32768），保持 4 路并发。

**验证**:
```
/slots: 4 slot 全部 n_ctx=32768
6 帧 → 200, 8 帧 → 200 (8277+516=8793 tokens), 12 帧 → 200
GPU1 free ~35GB (llama-server 占 ~14GB), 显存充足
```

**前车之鉴**: 改 `--parallel` 必须同步核算 `-c / parallel` 是否 ≥ (最大帧 prompt + max_tokens)。此 bug 是并发加速引入的回归，当时 `/slots` 只验证了 slot 数量没验证每 slot 上下文。

## Task D — 残留抽帧目录启动清理

**现象**: 系统临时目录累积 `tagging_frames_*` 残留目录（6 个共 95MB），含 8/3-8/9 各次任务。

**根因**: 正常任务在 `jobs.py` `TemporaryDirectory` with 块退出时删除临时目录，worker 按前缀清帧。但进程被 `taskkill //F` 强杀时 daemon 线程中断，finally 未执行 → 临时目录 + 帧全部泄漏。

**修复**:
- 新建 `app/services/video_tagging_service/cleanup.py` — `cleanup_orphan_tagging_frames()` 扫描 `<tempdir>/tagging_frames_*` 送回收站 (`safe_trash`/send2trash)。
- `main.py` lifespan 接入 — 后端每次启动自动清理残留。安全前提: 刚启动不可能有打标在跑，存在的目录必为孤儿。
- `requirements.txt` 显式加入 `send2trash`（file_utils 一直在用但从未声明，防 fallback 落到 `shutil.rmtree` 永久删除）。

**验证**: 伪造 `tagging_frames_*` 目录 → cleanup 识别并送回收站 ✓；`app.main` import 通过（无循环 import）✓。
