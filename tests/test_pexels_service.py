"""Pexels resolve 服务真实 API 测试 (ID-003).

本测试**不**走 mock,直接调 Pexels API,但用小 per_page=2 控制流量.
运行前需设置环境变量 PEXELS_API_KEY.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import load_config, set_config
from app.database import init_db
from app.models import DownloadLog, MaterialAsset
from app.services.pexels_service import (
    PexelsAuthError,
    PexelsService,
    pexels_service,
)


def _init_test_db(tmp_path: Path) -> str:
    """创建独立测试 SQLite DB,并初始化配置."""
    db_path = tmp_path / "test_pipeline.db"
    db_url = f"sqlite:///{db_path}"
    cfg = load_config()
    # 复用 config 但改 materials_dir 到 tmp
    cfg.raw["defaults"]["materials_dir"] = str(tmp_path / "materials")
    cfg = load_config()  # reload from mutated raw 以便 defaults 同步
    # 上面 reload 无效因为 load_config 读文件;直接改对象字段
    os.environ["PEXELS_API_KEY"] = os.environ.get("PEXELS_API_KEY", "")
    # 手工替换 defaults dataclass(不可变),通过 load_config 读取 raw 已包含 materials_dir;
    # 但 raw 没变,所以这里直接注入环境变量,然后手动改 cfg
    cfg.__dict__["defaults"] = cfg.defaults.__class__(
        **{
            **{k: getattr(cfg.defaults, k) for k in cfg.defaults.__dataclass_fields__},
            "materials_dir": str(tmp_path / "materials"),
        }
    )
    set_config(cfg)
    init_db(db_url)
    return str(db_path)


@pytest.fixture
def svc(tmp_path: Path, monkeypatch) -> PexelsService:
    """每个测试独立 DB + 独立 materials 目录."""
    _init_test_db(tmp_path)
    # 强制用环境变量里的 key
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        pytest.skip("PEXELS_API_KEY not set in environment")
    monkeypatch.setenv("PEXELS_API_KEY", key)

    # 新建单例避免跨测试污染
    service = PexelsService()
    return service


@pytest.mark.real_api
def test_resolve_real_download(svc: PexelsService, tmp_path: Path) -> None:
    """第一次调用真实下载,返回本地路径."""
    results = svc.resolve(query="port", max_results=2, min_duration_sec=5)

    assert len(results) == 2, f"expected 2 results, got {len(results)}"
    assert all(r.local_path for r in results), "expected local paths after first download"
    assert all(Path(r.local_path).exists() for r in results if r.local_path)
    assert all(r.photographer for r in results)
    assert all(r.photographer_url for r in results)
    assert all(r.pexels_url for r in results)
    assert all(not r.degraded for r in results)

    # DB 验证
    db = svc._ensure_session()
    assets = db.query(MaterialAsset).all()
    assert len(assets) >= 2
    for a in assets:
        assert a.local_path
        assert a.pexels_id
        assert a.photographer
        assert a.photographer_url
        assert a.pexels_url


@pytest.mark.real_api
def test_resolve_cache_hit_no_re_download(svc: PexelsService, tmp_path: Path) -> None:
    """同一 query 第二次调用应零网络,全部命中本地."""
    svc.resolve(query="crane", max_results=2, min_duration_sec=5)
    before_logs = svc._get_quota_used_today(svc._ensure_session())

    results2 = svc.resolve(query="crane", max_results=2, min_duration_sec=5)
    after_logs = svc._get_quota_used_today(svc._ensure_session())

    assert len(results2) == 2
    assert all(r.local_path for r in results2)
    assert after_logs == before_logs, "cache hit should not consume quota"


@pytest.mark.real_api
def test_resolve_quota_exceeded_degraded(svc: PexelsService, tmp_path: Path) -> None:
    """quota 设为 0 时返回 degraded 元数据,不下载."""
    cfg = svc._cfg()
    # 通过 monkeypatch 属性方式临时改 quota:替换 defaults 对象
    from app.config import get_config, set_config

    old_defaults = cfg
    new_defaults = cfg.__class__(
        **{
            **{k: getattr(cfg, k) for k in cfg.__dataclass_fields__},
            "pexels_daily_download_quota": 0,
        }
    )
    full_cfg = get_config()
    full_cfg.__dict__["defaults"] = new_defaults
    set_config(full_cfg)

    try:
        results = svc.resolve(query="ocean", max_results=2, min_duration_sec=5)
        assert len(results) == 2, f"expected 2 degraded results, got {len(results)}"
        assert all(r.degraded for r in results), "expected degraded=True"
        assert all(r.local_path is None for r in results)
        assert all(r.reason == "quota_exceeded_or_unreachable" for r in results)
    finally:
        full_cfg.__dict__["defaults"] = old_defaults
        set_config(full_cfg)


@pytest.mark.real_api
def test_resolve_min_duration_filter(svc: PexelsService) -> None:
    """min_duration_sec=30 应过滤掉短视频."""
    results = svc.resolve(query="timelapse", max_results=3, min_duration_sec=30)
    assert results
    assert all(r.duration_sec >= 30 for r in results), "results below min duration"


def test_resolve_auth_error_without_key(monkeypatch) -> None:
    """缺少 API key 时抛 PexelsAuthError."""
    # ⚠️ 顺序: load_config 必须在 delenv 之前 — 其内部 _load_dotenv 会把 .env 的
    # key setdefault 回环境 (曾致本测试静默失效: key 永不缺失, 走到 DB 才炸).
    cfg = load_config()
    set_config(cfg)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    pexels_service._api_key = None  # 单例缓存 key, 须重置才会重读 env
    try:
        with pytest.raises(PexelsAuthError):
            pexels_service.resolve(query="test")
    finally:
        pexels_service._api_key = None  # 复原, 防真 key 缺失状态泄漏到后续测试


def test_download_log_table(svc: PexelsService) -> None:
    """DownloadLog 可正常写入/查询."""
    db = svc._ensure_session()
    log = DownloadLog(date=date.today(), pexels_id=12345, bytes=1024)
    db.add(log)
    db.commit()

    count = svc._get_quota_used_today(db)
    assert count >= 1


@pytest.mark.real_api
def test_resolve_invalid_api_key_degraded(svc: PexelsService, monkeypatch) -> None:
    """无效 key (API 返 401) 应抛 PexelsAuthError, 不静默降级.

    2026-09-01 改 mock 401: 原版真打 Pexels 曾在 pytest 进程内被某全局状态
    干扰 (invalid header 拿 200, 裸进程 10/10 全 401) — 本测试的意图是
    "401 → 抛 PexelsAuthError" 这条代码路径, 不依赖服务端行为.
    """
    monkeypatch.setenv("PEXELS_API_KEY", "invalid_key_for_test")
    svc._api_key = None  # 强制重新读取
    svc._requests_session = None

    class _Resp401:
        status_code = 401
        text = "unauthorized"

        def json(self):  # pragma: no cover - 401 不走 json
            raise ValueError("no json")

    class _FakeSession:
        def get(self, *args, **kwargs):
            return _Resp401()

    import app.services.pexels_service._http as pexels_http
    monkeypatch.setattr(pexels_http, "http_session", lambda _svc: _FakeSession())
    with pytest.raises(PexelsAuthError):
        svc.resolve(query="port", max_results=1)
