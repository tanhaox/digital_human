# ADR-001：slot 族的运行时依赖注入（环#5）

## 状态

已接受（2026-10-16，B12 用户裁决选 A 案）

## 背景

审计（2026-10-16）发现 `slot_executor` / `slot_workflows` / `director_service`
三个模块之间存在 6 处相互引用，构成**两个闭环**：

- 二元环：`slot_executor → slot_workflows → slot_executor`
- 三元环：`slot_executor → director_service → slot_workflows → slot_executor`

逐条（复扫 2026-10-16，行号以当日为准）：

| 编号 | 方向 | 位置 | 形式 | 用途 |
|---|---|---|---|---|
| 1 | slot_workflows → slot_executor | host_comfy.py:83 | 函数内延迟 | 引 `_is_cancelled` 做取消检查（取消时向 ComfyUI 发 interrupt 并中断轮询） |
| 2 | slot_executor → slot_workflows | slot_executor.py:19 | 顶层 | 引 `WORKFLOW_HANDLERS` 分派注册表（slot 类型 → workflow 执行器） |
| 3 | director_service → slot_workflows | _fallback.py:14 | 顶层 | 引 `hf_chart` 的 `_chart_has_data`/`_normalize_chart_input`（HF 图表兜底判定） |
| 4 | director_service → slot_workflows | _fallback.py:15 | 顶层 | 引 `hf_extract` 的 `_extract_hf_content`（HF 内容提取兜底） |
| 5 | slot_executor → director_service | slot_executor.py:17 | 顶层 | 引 `append_trace`（执行轨迹落库）与 `replace_failed_slot`（失败槽降级替换） |
| 6 | slot_executor → director_service | slot_executor.py:428 | 函数内延迟 | 引 `complete_job_if_slots_done`（全部槽执行完毕后的终态收口） |

其中边 1 与边 2 互为因果：`host_comfy.py` 文件头 docstring 自证
「`_is_cancelled` 从 slot_executor 延迟导入（slot_executor 顶层 import
WORKFLOW_HANDLERS，模块级导入会成环），`publish` 从 director_events 延迟导入」
——**延迟导入在这里是刻意设计，不是疏漏**。

## 决策

接受现状，不重构消环。

## 理由

1. slot 是 GPU 执行核心（HF/J 两条产线的运行时底座），动它风险最高
2. 这不是单个二元环而是两个闭环叠加，重构需同时改 3 个模块的签名/注册时序，
   半路不一致的风险大于收益
3. host_comfy.py 文件头已自证延迟导入是刻意设计
4. 函数内延迟 import（运行时依赖注入）是行业常见做法，非反模式；
   B07（GPU 锁）、B08（feeds 供给注册）证明需要真解的环可以单独小步解，
   而本环的 6 条边全部在"执行核心"上，不具备小步条件

## 后果

- 环#5 永远留在代码里（除非将来发生 slot 执行器大重构）
- 读到 slot 三模块互相 import 的人会疑惑——本文档就是为了消掉这个疑惑
- 以后动这块代码时，以下**不能碰**：
  * `WORKFLOW_HANDLERS` 的注册时机（边 2 顶层依赖：slot_workflows 包内各
    handler 模块在包 `__init__` 装配时注册，slot_executor 顶层读它分派——
    改成延迟会改变首次分派语义，改成注入要动全部 handler 签名）
  * `_is_cancelled` 的签名（边 1 延迟依赖：host_comfy 轮询热路径每次调用，
    签名/语义变化会破坏取消中断）
  * slot 执行器的调用约定（`append_trace`/`replace_failed_slot`/
    `complete_job_if_slots_done` 的接口稳定性——边 5/6 依赖）
  * 顶层级与延迟级的分层不要互换（边 2/3/4/5 顶层 + 边 1/6 延迟的现状
    是能启动的最小组合，随意把顶层改延迟或反之都可能引入启动顺序问题）

## 相关决策

- B07：GPU 锁抽离 anim_pipeline/gpu_lock.py（环#1，单实例）
- B08：feeds 供给注册 anim_pipeline/feeds.py（环#2，回调注入）
- B11：job 底座中立化 app/services/ppt_registry.py（环#3）
- B13 前半：环#6 随 B11 自动破解
