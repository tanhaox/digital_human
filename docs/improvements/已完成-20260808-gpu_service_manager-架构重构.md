# gpu_service_manager 架构重构 — 变更摘要

> 日期：2026-08-08 ｜ 模块：`app/services/gpu_service_manager.py` (495 行) → 包化 5 模块
> 方法：行为逐字迁移（字节级 diff 验证）＋ AST 硬约束 + 外部引用面零破坏

## 变更摘要

| 新文件 | 行数 | 职责 | 关键内容 |
|---|---|---|---|
| `gpu_service_manager/__init__.py` | 51 | 公共 API + 惰性单例 | 全局 `_manager`/`_manager_lock` + `get_gpu_service_manager()`（`main.py` 以 `gpu_service_manager._manager` 直接访问同一全局） |
| `gpu_service_manager/_specs.py` | 112 | 部署事实表 | `_BUILTIN_SPECS`（fish/f5/indextts/comfyui）、`ServiceSpec` dataclass（含 `health_url` property）、`build_specs(raw)` 内置+override 合并、`NO_LOCAL_SERVICE` |
| `gpu_service_manager/_http.py` | 50 | 健康探测/端口工具 | `http_ok`、`port_of`、`pids_listening_on` |
| `gpu_service_manager/_lifecycle.py` | 226 | 生命周期 Mixin | `ServiceLifecycleMixin`：`_ensure_running`/`_await_ready`/`_launch`/`_stop_service`/`_force_free_port`/`_kill_proc_tree`/`_stop_if_idle`/`_start_watchdog` |
| `gpu_service_manager/_manager.py` | 139 | 对外主类 | `GPUServiceManager(ServiceLifecycleMixin)` + `_ServiceState` dataclass：`session`/`_touch`/`status`/`stop_all`/`shutdown` |

原文件 `app/services/gpu_service_manager.py` 已送回收站（send2trash），不留 shim（同名包解析优先，shim 为 unreachable dead code）。

## 硬约束达成

| 约束 | 达成 |
|---|---|
| 文件 ≤250 行 | ✓（max 226） |
| 函数 ≤40 行 | ✓ |
| 类 ≤200 行 | ✓（`GPUServiceManager` 287→139） |
| 模块 ≤3 个类 | ✓ |
| `__all__` 齐全 | ✓ 每个模块 |
| 绝对导入 | ✓ `from app.services.gpu_service_manager.*` |
| 循环导入 | ✓ 全部子模块可独立加载（验证通过） |
| `*` 导入 | ✓ 无 |
| 行为逐字 | ✓ 22/22 diff 检查 |

## 验证记录

| 检查 | 结果 |
|---|---|
| AST 硬约束（文件/函数/类/类数） | 0 问题，5/5 模块 ✓ |
| 行为契约断言 | 7/7 PASS |
| 逐字 diff（公共 API/签名/字段/内置表/放行路径/status 结构） | 22/22 通过 |
| 删除原文件后外部引用方导入 | 6 个引用方 + `main.py` ✓ |
| 单例命名空间（`_manager`/`_manager_lock`） | ✓ |
| 语法 + 循环导入 | ✓ |

## 外部引用面

- `app/main.py:53-54` — 直接访问 `gpu_service_manager._manager.shutdown()`
- `app/routers/audio.py:16`、`voices.py:22`、`tts_services.py:6`、`slot_executor.py:18`、`retry.py:106` — `get_gpu_service_manager`
- 全部零改动（包化保持导入路径不变）

## 行为不变性（逐字保留）

- `session(backend)`：非本地/不在 specs/auto_manage=False 直接 `yield` 放行；本地托管走 排队→`_ensure_running`→`_touch`→执行→释放；`idle_timeout_sec<=0` 立即 `_stop_if_idle(force=True)`，否则 `_start_watchdog`
- `_ensure_running`：外部已启动复用（日志 `"[gpu_svc] %s 已由外部启动, 直接复用"`）；僵死先杀；腾显存（`"正在关闭 %s 以腾出显存…"`）
- `_await_ready`：启动即退出 `RuntimeError`（exit code + 日志路径）；就绪 `"[gpu_svc] %s ready at %s"`；超时 `TimeoutError`（`f"{name} 启动超时 ({timeout:.0f}s), 日志: …"`）
- `_force_free_port`：保护端口 `(54321, 7861, 8188)` 永不强杀；5 轮 taskkill
- `status()`：逐字段一致（含 `http_ok(health_url, timeout=1.5)`、`idle_sec`）
- 异常类型/消息、日志字符串 100% 一致

## 备注

- **同名遮蔽陷阱**：子模块 `_manager.py` 与 `__init__.py` 全局单例 `_manager=None` 同名 → `import pkg._manager as nm` 绑定到 `None`（属性查找，非子模块解析）。正常 `from ..._manager import ...` 不受影响；工具脚本用 `importlib.import_module` 访问。`main.py` 依赖 `gpu_service_manager._manager`（单例），命名保留。
- 验证脚本：`scripts/_ast_check_gpu_mgr.py`、`scripts/_verify_gpu_service_manager.py`、`scripts/_diff_gpu_service_manager.py`
