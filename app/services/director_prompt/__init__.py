"""Director prompt — 导演 LLM 提示词构建服务 (ID-034 / ID-003).

包化拆分自原单文件 director_prompt.py (783 行):
  - _constants.py   词表包路径 / 维度枚举 / 清洗词表 / _NO_HOST_DEMO
  - _strip_host.py  无出镜模式裁剪 (C 线禁用)
  - _prompt.py      提示词装配 (build_director_prompt / load_director_prompt)
  - _vocabulary.py  词表包构建/重建/加载 (ID-034)
  - _catalog.py     素材库目录 (真实 DB / mock 兜底)

公共 API 与原模块完全一致, 外部导入零改动.
"""
from app.services.director_prompt._catalog import (
    build_real_material_catalog,
    mock_material_catalog,
)
from app.services.director_prompt._prompt import build_director_prompt, load_director_prompt
from app.services.director_prompt._vocabulary import (
    build_vocabulary_pack,
    load_vocabulary_pack,
    rebuild_vocabulary_pack,
)

__all__ = [
    "build_director_prompt",
    "build_real_material_catalog",
    "build_vocabulary_pack",
    "load_director_prompt",
    "load_vocabulary_pack",
    "mock_material_catalog",
    "rebuild_vocabulary_pack",
]
