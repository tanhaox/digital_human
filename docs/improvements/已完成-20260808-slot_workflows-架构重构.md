# 已完成-20260808-slot_workflows-架构重构

**文件**: `app/services/slot_workflows.py`(845 行)+ 7 种视觉槽位 workflow → `app/services/slot_workflows/` 包(12 个模块,共 1211 行)
**日期**: 2026-08-08
**范围**: 第二批「核心服务层重构」第 2 个文件

## 目标

将 845 行的 `slot_workflows.py` 拆分为扁平、按 workflow 族分组的包结构,满足大文件重构硬性约束(文件 ≤250 行 / 函数 ≤40 行 / 类 ≤200 行 / 绝对导入 / 策略模式 / 无死代码 / 语义逐字不变 / 循环导入经 Protocol/DI 处理)。

## 拆分结果

| 模块 | 行数 | 职责 |
|---|---|---|
| `slot_workflows/__init__.py` | 50 | `WORKFLOW_HANDLERS` 7-key 分发表 + `__all__` 9 符号 |
| `slot_workflows/common.py` | 54 | `slot_root`/`ensure_slot_dir`/`audio_slice_path`/`_pick_hf_template` + HF 模板常量 |
| `slot_workflows/host.py` | 149 | C 线 `execute_host_slot` + `_resolve_host_role`/`_pick_storyboard_image`/`_prepare_host_inputs`/`_build_host_workflow` |
| `slot_workflows/host_qa.py` | 113 | C 线分镜图 QA `prepare_comfyui_storyboard` + `_qa_convert_rgb`/`_qa_resize`/`_qa_letterbox`/`_qa_finalize` |
| `slot_workflows/host_comfy.py` | 132 | C 线 `_submit_comfyui_workflow`/`_send_comfyui_interrupt`/`_emit_heartbeat`/`_find_comfy_output`/`_check_cancel`/`_maybe_emit_heartbeat`/`poll_comfyui_and_collect` |
| `slot_workflows/broll.py` | 155 | L 线 `execute_broll_local_slot` + 策略 1/2-3/4(`_try_exact_file`/`_build_local_keywords`/`_strategy_semantic_match`/`_render_broll_local`) |
| `slot_workflows/broll_pexels.py` | 140 | P 线 `_collect_used_pexels_ids`/`_resolve_pexels`/`_persist_chosen_pexels_id`/`execute_broll_pexels_slot` |

> **2026-08-09 注记（P 线本地碰撞落地，行数已变）**：`broll_pexels.py` 140 → **213**（新增 `_try_local_collision` 本地碰撞优先 + `register_asset_usage` 登记，见 [已完成-20260809-P线本地碰撞-ID034匹配修复.md](已完成-20260809-P线本地碰撞-ID034匹配修复.md)）；`broll.py` 155 → **168**（`_match_local` 传 `min_duration_sec` + 命中登记）。行为契约保持不变：7-key 分发表、降级语义逐字保留。
| `slot_workflows/fallback.py` | 69 | L 线 `_pick_fallback_material`/`execute_black_placeholder_slot` |
| `slot_workflows/hf.py` | 86 | H 线 `execute_hf_visual_slot` + `_merge_render_config`/`_ensure_metrics` |
| `slot_workflows/hf_extract.py` | 121 | H 线 `_extract_hf_content`/`_clean_fragment`/`_pick_title`/`_extract_numbers`/`_score_metrics` + `_EMOTION`/`_OPENING_PREFIX` |
| `slot_workflows/hf_chart.py` | 99 | H 线 `_normalize_chart_input`/`_coerce_items`/`_chart_fallback`/`_apply_chart_metadata` |
| `slot_workflows/mixed.py` | 43 | C 线 `execute_mixed_host_broll_slot` ffmpeg 合成 |

**已删除**(send2trash 回收站): `slot_workflows.py` 弃用 shim + `slot_workflows.py.bak`(845 行备份)。

## 关键设计决策

1. **按 workflow 族拆包**: 对应 `_EXECUTION_PHASES` 的 C(host/mixed)/P(broll_pexels)/L(broll_local/black)/H(hf_chart/hf_title) 四条线。公共辅助沉淀到 `common.py`。
2. **策略模式分发表**: `WORKFLOW_HANDLERS` 保持 7 key 映射不变(`host`/`broll_pexels`/`broll_local`/`hf_chart`/`hf_title`/`mixed_host_broll`/`black_placeholder`),由 slot_executor 按 `slot.workflow` 分发。
3. **shim 死代码(关键发现,违反原蓝图)**: 规划中"旧文件保留为弃用 shim"不可行 — Python 导入规则下同名**目录包永远优先于 `.py` 文件**,shim 是不可达死代码。验证 `from app.services import slot_workflows` 解析到 `__init__.py`(而非 shim)后,用 send2trash 删除 shim 与 `.bak`。外部引用点已直接指向包,无需 shim。
4. **循环导入防护(函数体内延迟导入)**: `_check_cancel` 内 `from app.services.slot_executor import _is_cancelled`;`_emit_heartbeat` 内 `from app.services.director_events import publish`;`execute_hf_visual_slot` 内 `from app.services.visual_render_service import execute_visual_render_job`;`_pick_fallback_material` 内 `import random`。模块级只 import 无反向依赖的模块。
5. **broll 降级语义保持**: `_strategy_semantic_match` 中 `hit_ratio < MIN_HIT_RATIO(75%)` → 返回 None → 调用方降级 `execute_broll_pexels_slot`,降级警告消息逐字保留。
6. **零搬运**: ComfyUI 输出直接返回 `cfg.defaults.comfyui_output_dir` 原始路径,不复制进 slots/。

## 行为契约(逐字验证通过)

- `WORKFLOW_HANDLERS` 7 个 key 与 handler 映射不变。
- `execute_hf_visual_slot` 签名 `(db, slot, workflow)` 不变 — slot_executor L71-72 特判传第三个参数。
- 所有错误消息逐字不变,包括:
  - ComfyUI 超时: `"ComfyUI timeout after {timeout_sec}s for prompt {prompt_id}"`
  - 用户取消: `"用户取消，ComfyUI 已发送 interrupt"`
  - HF: `"HF render failed"` / `"HF render output missing"`
  - Pexels: `"no usable Pexels material for {desc}"`
  - 降级警告: `"[broll_local] slot %d: no local material matching keywords/hard dims, degrading to pexels"`
- `execute_broll_local_slot` 降级顺序不变(策略 1 精确 → 策略 2-3 语义 → 策略 4 降级下载)。
- `_extract_hf_content` 返回 `{"title","subtitle","metrics","chart"}` 结构不变,空输入回退 `{"title": "数据展示", ...}`。
- `poll_comfyui_and_collect` 取消/心跳/超时语义不变。
- import 链路无循环: `import app.services.slot_executor` / `import app.services.composition_service` 冷启动成功(延迟导入宿主链路正常)。

## 验证命令

```bash
cd /f/AI-Agent-Local/digital_human
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m py_compile app/services/slot_workflows/*.py
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe  # + 契约断言脚本 (7 key / hf 签名 / 错误消息 / 导入链路)
```

- 12 个文件全部 AST_OK(物理行 ≤250,函数 ≤40 行)。
- 契约断言 `ALL_CONTRACT_CHECKS_PASSED`。

## 遗留说明

- 原诊断报告预估 920 行,实际 845 行(报告已更正)。
- shim 因同名包冲突删除是**全局模式**: 后续 `director.py` 等拆包时,若旧文件与包同名,同样不留 shim,直接删除(纯文件或纯包场景才可用 shim)。

## 下一步

第二批第 3 个文件: **director.py(864 行)** 诊断与拆分。
