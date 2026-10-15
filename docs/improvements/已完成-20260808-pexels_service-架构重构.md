# 已完成 · 2026-08-08 · pexels_service 架构重构

> 原 `app/services/pexels_service.py`(552 行,类 `PexelsService` 466 行)拆分为 9 模块包,外部导入零改动。

## 变更摘要

| 模块 | 行数 | 职责 | 依赖 |
|---|---|---|---|
| `__init__.py` | 26 | 公共 API 导出 + `__all__` | service, types |
| `types.py` | 52 | `PEXELS_API_BASE` + 异常 + `ResolveItem` dataclass | — |
| `_session.py` | 77 | DB/HTTP session + config 懒加载 helper | config, database, types |
| `_local.py` | 61 | 本地缓存查询 (`query_local` / `asset_to_item`) | models, types |
| `_http.py` | 80 | Pexels API 搜索 + 流式下载 | requests, types, pexels_utils |
| `_db.py` | 138 | DB 写入 (MaterialAsset/VideoAsset/DownloadLog + quota/黑名单) | models, asset_tagging, pexels_utils |
| `_degraded.py` | 62 | 降级补齐 (`merge_with_degraded` / `fill_with_degraded`) | types, pexels_utils |
| `_candidates.py` | 128 | 候选过滤 / 下载 / 入库编排 (`process_candidates`) | models, _db, _http, _local, types |
| `service.py` | 193 | `PexelsService` 主类 (编排 + 私有接口转发) + 单例 | 上述各模块 |

**合计 9 模块 / 817 行**(原 552 行,拆包后模块化程度提升,拆分辅助代码 + 类型注解使总量上升,但单文件均 ≤250 行)。

## 架构要点

- **`service.py` 保留类 + 模块级 helper**:类超限(466>200)时方法拆为模块级函数(接收 `svc`=self),类保留同名转发方法。测试直接使用的私有接口(`svc._ensure_session` / `svc._get_quota_used_today` / `svc._cfg` / `svc._api_key` / `svc._requests_session`)零破坏。
- **`resolve` 编排函数 70 行** — 压在编排上限 70,不拆。
- **`resolve_descending` 46 行** — 拆出 `_descending_attempt` helper 压至 ≤40。
- **`register_video_asset` 移除死参数 `source_url`** — 原函数体未使用,按死代码约束移除。
- 异常类型与消息逐字保留(`PexelsAuthError` / `PexelsResolveError`)。

## 约束达成

- ✅ 新文件全部 ≤250 行(最大 `_db.py` 138)
- ✅ 函数 ≤40 行(`resolve` 编排例外 ≤70)
- ✅ 类 ≤200 行(service.py 类 <200)
- ✅ 模块 ≤3 个类(每模块 ≤2)
- ✅ `__init__.py` 含 `__all__`
- ✅ 绝对导入(`from app.`)
- ✅ 无 `from module import *`
- ✅ 无死代码、无循环导入(依赖方向单向:config/database/models/asset_tagging/pexels_utils 均不反向引用)
- ✅ 全部类型注解
- ✅ 行为契约逐字核对(8 条异常消息 + 流程结构)

## 循环导入检查

依赖链: `service` → `_candidates`/`_db`/`_degraded`/`_http`/`_local`/`_session`/`types`, `_http` → `_session`, `_candidates` → `_db`/`_http`/`_local`。均为单向 DAG,无循环。

## 最终文件树

```
app/services/pexels_service/
├── __init__.py      (26 行)
├── types.py         (52 行)
├── _session.py      (77 行)
├── _local.py        (61 行)
├── _http.py         (80 行)
├── _db.py           (138 行)
├── _degraded.py     (62 行)
├── _candidates.py   (128 行)
└── service.py       (193 行)
```

## 外部引用面(已验证零破坏)

| 引用方 | 引用方式 | 验证 |
|---|---|---|
| `app/services/slot_workflows/broll_pexels.py:17` | `from app.services.pexels_service import pexels_service` | ✅ import 链 OK |
| `tests/test_pexels_service.py` | `from app.services.pexels_service import PexelsAuthError, PexelsService, pexels_service` | ✅ 包解析 OK |
| 测试私有接口 | `svc._ensure_session()` / `_get_quota_used_today(db)` / `_cfg()` / `_api_key` / `_requests_session` 赋值 | ✅ 均保留 |
| `PEXELS_API_BASE` / `ResolveItem` | 无外部代码引用 | ✅ |

## 验证记录

1. AST 硬约束检查全绿(文件/类/函数行数)。
2. `py_compile` 全部通过。
3. 包导出 + `PexelsService()` 无参构造 + 私有接口冒烟通过。
4. 行为契约: 缺 key 抛 `PexelsAuthError("Missing Pexels API key...")` ✅;DownloadLog quota 计数 ✅。
5. 完整 resolve 链路(隔离 sqlite + tmp materials): 真实下载成功,返回本地路径,`degraded=False`;`resolve_descending(['china','street','busy'])` 命中。
6. 原 `pexels_service.py` 已 send2trash 入回收站(遵守 CLAUDE.md 红线),删除后包解析 + broll_pexels + 测试 import 面全通。
7. 全量应用模块导入无循环。
