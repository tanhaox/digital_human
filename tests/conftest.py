"""conftest.py for digital_human pytest discovery.

Adds the scripts/ directory to sys.path so `import tts_client` works
without installing the package. Also exposes shared fixtures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# digital_human/scripts/tts_client.py is importable as `tts_client`
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@pytest.fixture(scope="session", autouse=True)
def _load_app_config():
    """会话级加载 app config (config/app.yaml)。

    get_config() 未加载即 RuntimeError — 全量跑时靠前序测试的导入副作用碰巧
    加载, 单跑子集即炸 (2026-09-04 实证)。此处显式加载, 测试不再依赖顺序。
    """
    try:
        from app.config import load_config, set_config

        set_config(load_config())
    except Exception:
        pass  # 配置异常不阻断收集; 需要配置的测试自会暴露
    yield