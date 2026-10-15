# 已完成-20260808-director_prompt-架构重构

**原文件**: `app/services/director_prompt.py`(783 行,无类,纯函数模块)
**重构为**: `app/services/director_prompt/` 包(6 模块)
**日期**: 2026-08-08

## 变更摘要

| 原符号 | 去向 | 说明 |
|---|---|---|
| 全部公共 API | `__init__.py` re-export | 7 API 原样导出,外部导入零改动 |
| 路径/维度/清洗/示例常量 | `_constants.py`(178 行) | `VISUAL_DIRECTOR_V2_PATH` / `HARD_DIMENSIONS` / `_CLEANUP_*` / `_NO_HOST_DEMO`(100 行静态示例逐字) |
| `_strip_host_mode`(136) | `_strip_host.py`(153 行) | 12 步裁剪拆 4 helper + 7 行编排 `strip_host_mode`;规则表常量 `_RULE1/2/3_TABLE` 逐字 |
| `build_director_prompt`(69 编排) | `_prompt.py`(212 行) | 拆 `_pipeline_substitution_lines` helper;管线约束块 47→24 行 |
| `load_director_prompt`(8) | `_prompt.py` | 逐字 |
| `build_vocabulary_pack`(131) | `_vocabulary.py`(231 行) | 拆 `_collect_enum_dims`(32) + `_collect_text_buckets`(12);62 行编排例外通过 |
| `rebuild_vocabulary_pack`/`load_vocabulary_pack` | `_vocabulary.py` | 逐字,logger 消息逐字 |
| `build_real_material_catalog`(73) | `_catalog.py`(145 行) | 拆 `_build_scene_catalog`(12) → 37 行 |
| `mock_material_catalog`(32) | `_catalog.py` | 逐字 5 节 |

## 验证记录

1. **AST 硬约束**(`scripts/_ast_check_director_prompt.py`):6 模块全部 ≤250 行;函数 ≤40(`--orch` 声明 `build_director_prompt`/`build_vocabulary_pack` 上限 70);无 `import *`;`__init__.py` 含 `__all__` — **0 问题**。
2. **行为契约断言**(`scripts/_verify_director_prompt.py`):7 API 导入 / mock 5 节 / DB 失败空 pack / DB 失败 mock fallback / load None|dict / strip_host 无 host 残留 / build_director_prompt 无出镜+全启用+全禁用 3 场景 — **8/8 通过**。
3. **新旧逐字 diff**(`scripts/_diff_director_prompt.py`):`spec_from_file_location` 从磁盘原文件加载旧模块,同输入对比 6 项函数 10 场景输出 — **全部逐字节一致**(含 build_director_prompt 5 管线场景)。
4. **循环导入**:6 模块逐个 `importlib.import_module` 全部成功;依赖方向 director_prompt → config/models/schemas,无反向引用。
5. **原文件删除**:send2trash(回收站)删除 783 行原文件,包名解析优先 → 验证 4 处外部引用面(`director_service.py` / `director_routes/planning.py` / `rebuild_vocabulary.py` / `video_tagging_service/store.py`)导入零破坏,7 API 齐备。

## 修复的隐藏 Bug

**`get_config` 未导入(NameError 被 except 吞掉)**:原 `build_director_prompt` L440 调用 `get_config()` 但从未 import → 每次调用抛 NameError 被内部 except 捕获,`director_catalog_mode="full"` 配置**永不生效**。重构 `_prompt.py` 中补 `from app.config import get_config`,行为修复(配置默认 `"vocabulary"`,默认路径行为不变)。

## 模块清单(全部 ≤250 行)

| 模块 | 行数 | 职责 |
|---|---|---|
| `__init__.py` | 31 | re-export 7 API + `__all__` |
| `_constants.py` | 178 | 常量集中 |
| `_strip_host.py` | 153 | 无出镜模式裁剪 |
| `_prompt.py` | 212 | 提示词装配 |
| `_vocabulary.py` | 231 | 词表包构建/重建/加载 |
| `_catalog.py` | 145 | 素材库目录 |

## 验证脚本

- `scripts/_ast_check_director_prompt.py`
- `scripts/_verify_director_prompt.py`
- `scripts/_diff_director_prompt.py`
