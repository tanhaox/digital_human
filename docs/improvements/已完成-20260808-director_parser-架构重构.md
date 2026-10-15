# 已完成-20260808-director_parser-架构重构

**模块**: `app/services/director_parser.py` (420 行)
**方法**: 包化拆分 (单文件 → 5 模块包 `director_parser/`, 公共 API 经 `__init__.py` 汇总)
**日期**: 2026-08-08

## 变更摘要

| 新文件 | 行数 | 职责 | 关键内容 |
|---|---|---|---|
| `__init__.py` | 46 | 公共 API + 私有外部依赖 | re-export 15 符号 + `__all__` |
| `_json.py` | 41 | JSON 提取 | `_strip_code_fence` + `extract_json` |
| `_rows.py` | 153 | 行解析器 | `_VALID_WORKFLOWS`、`map_visual_type_to_workflow`、`map_intensity`/`map_emotion`、`_static_params`/`_dynamic_params`、`parse_legacy_row`、`_camera_angle`、`parse_new_row`、`is_new_schema` |
| `_rules.py` | 220 | 规则执行 | `_HOST_FAMILY`、`_chain_for`(数据化 fallback 链)、`_best_fallback_workflow`、`_downgrade_hosts_c_disabled`/`_force_host_boundaries`/`enforce_host_rules`、`_force_host_ends`/`_pick_fallback_wf`/`_sample_middle_hosts`/`_downgrade_excess_hosts`/`cap_host_count`、`_same_family`/`_would_conflict`/`_swap_away_conflict`/`enforce_adjacency_rules` |
| `_parse.py` | 78 | 编排入口 | `_extract_output`(title/rows 提取)、`_build_slots`(schema 分派+排序+重编号)、`parse_llm_plan` |

**删除**: `app/services/director_parser.py` (send2trash 至回收站)

## 硬约束达成表

| 约束 | 值 | 达成 |
|---|---|---|
| 新文件 ≤250 行 | max=220 (`_rules.py`) | ✅ |
| 函数 ≤40 行 | max=31 (`enforce_adjacency_rules`) | ✅ |
| 编排函数 ≤65-70 | 9 (`parse_llm_plan`) | ✅ |
| 类 ≤200 行 | 0 个类 (纯函数模块) | ✅ |
| 模块 ≤3 类 | 0 | ✅ |
| `__init__.py` 含 `__all__` | 15 符号 | ✅ |
| 绝对导入 (`from app.`) | 全部 | ✅ |
| 循环导入 | 无 (`get_config` 保持延迟导入) | ✅ |
| `from ... import *` | 无 | ✅ |
| if/elif 链 >4 分支 | 链降级 → `_chain_for` 数据表 | ✅ |
| 类型注解 | 全部函数 | ✅ |
| 行为不变性 | diff 37/37 | ✅ |

## 验证记录

| 步骤 | 命令/方法 | 结果 |
|---|---|---|
| AST 硬约束 | `scripts/_ast_check_director_parser.py` | ✅ 5 模块 46/41/153/220/78 行合规 |
| 行为契约 | `scripts/_verify_director_parser.py` | ✅ 81/81 通过 |
| 逐字 diff | `scripts/_diff_director_parser.py` | ✅ 37/37 一致 (新旧输入输出逐字相同) |
| 删除原文件 | send2trash 纯反斜杠路径 | ✅ |
| 引用方重验 | `_llm`/`_fallback`/`slot_executor`/`retry` 导入 | ✅ 符号全部解析到新包 |
| 引用方消费 | `_best_fallback_workflow` 调用 + `_HOST_FAMILY` | ✅ 行为正常 |
| 全量编译 | `compileall app/` | ✅ 无残留断链 |

## 外部引用面

| 引用方 | 导入符号 | 包化后解析 |
|---|---|---|
| `director_service/_llm.py:16` | `parse_llm_plan` | `_parse.py` |
| `director_service/_fallback.py:13` | `_HOST_FAMILY` | `_rules.py` |
| `slot_executor.py:17` | `_best_fallback_workflow` | `_rules.py` |
| `director_routes/retry.py:17` | `_best_fallback_workflow` | `_rules.py` |

4 处导入零改动 (`from app.services.director_parser import ...` 由 `__init__.py` 汇总)。

## 行为不变性列表

1. `extract_json`: 先剥 ``` 围栏 → `json.loads` → `{`/`[` 正则提取 → 裸 `json.loads` (末步异常原样上抛)
2. `map_visual_type_to_workflow`: `_VALID_WORKFLOWS` 直通 → `出镜`/`混合` → dynamic 标题/图表 → file → pexels
3. `map_intensity`/`map_emotion`: 越界值落默认 `medium`/`rising`
4. `parse_legacy_row`: `end<=start → +0.5`; static→file/fallback_file, dynamic→render_config; `visual_type` 默认 `"出镜"`; 无 timing → None
5. `parse_new_row`: 非法数字 → None; start/end 钳制 `[0,total]`, `end=max(start+0.1,...)`; camera 钳制 `[1,4]` 非 host/mixed 置 1; params setdefault intensity/emotion
6. `is_new_schema`: 仅看 rows[0] 含 `workflow`/`start_sec`
7. `parse_llm_plan`: sort by `(start_sec, slot_index)` + 重编号; 两个 ValueError 消息逐字保留
8. `_best_fallback_workflow`: 4 优先级链数据化 (`_chain_for`) 严格等价原 if/elif; `enabled_pipelines=None → 直接返回 prefer`
9. `enforce_host_rules`: `slots[-1].end_sec=min(...)` 先行; C线禁用/启用分支拆分, fallback_reason 三处 + `c_pipeline_disabled` 日志逐字
10. `cap_host_count`: 超限采样保留 (step 间距) + `host_cap_exceeded` 日志; `max_host` 默认 4
11. `enforce_adjacency_rules`: 两轮 `_same_family`/`_would_conflict`/`_swap_away`, 窗口 `[i+2, i+6)`
12. `get_config` 延迟导入保持 (避免循环); `max_host_slots` 从 config.defaults 读
13. 所有日志字符串 100% 一致

## 备注

- **同名包陷阱**: 原 `.py` 与包同名, Python 恒解析到包 → 原文件 send2trash 删除 (实测 `__init__.py` 胜出)。
- **`_best_fallback_workflow` 数据化**: 原 45 行 if/elif 链 → `_chain_for` 数据表 (4 分支各 1 行), 链末恒含 `(wf, None)` → `next()` 无 StopIteration。
- **内部符号**: `_HOST_FAMILY`/`_best_fallback_workflow` 由 `_fallback.py`/`slot_executor.py`/`retry.py` 直接引用 → 虽非公共 API 仍列入 `__all__` 保外部依赖零改动。
- **验证脚本**: 需 `set_config(load_config())` 前置 (enforce_host_rules 内 `get_config()` 依赖 lifespan 加载); diff 脚本在删除原文件后不可再跑 (load_old 依赖原文件, 预期内)。
- **adjacency 为尽力而为**: 特定构型 (如 `[host,host,broll,host,host]`) 无可消解解, 非重构引入。
