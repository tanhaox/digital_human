"""Pexels resolve — 配置 / DB / HTTP session 懒加载 helper。

全部接收 self(PexelsService), 作为类方法转发目标; 保持私有接口
_svc._api_key / _svc._requests_session / _svc._session 的语义不变。
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import get_config
from app.database import get_db
from app.services.pexels_service.types import PexelsAuthError

logger = logging.getLogger(__name__)

__all__ = [
    "session_factory",
    "close_session",
    "ensure_config",
    "ensure_session",
    "http_session",
    "defaults",
]


def session_factory() -> Session:
    """创建新 DB session (get_db generator 取第一个)."""
    return next(get_db())


def close_session(svc: Any) -> None:
    """关闭并置空懒加载的 DB session."""
    if svc._session is not None:
        try:
            svc._session.close()
        except Exception:  # noqa: BLE001
            pass
        svc._session = None


def ensure_config(svc: Any) -> None:
    """确保 API key 已加载; 缺失抛 PexelsAuthError."""
    if svc._api_key is not None:
        return
    cfg = get_config()
    key_env = cfg.defaults.pexels_api_key_env
    svc._api_key = os.environ.get(key_env)
    if not svc._api_key:
        raise PexelsAuthError(f"Missing Pexels API key in environment variable {key_env}")


def ensure_session(svc: Any) -> Session:
    """懒加载 DB session (首次创建, 之后复用)."""
    if svc._session is None:
        svc._session = session_factory()
    return svc._session


def http_session(svc: Any) -> requests.Session:
    """懒加载 requests.Session, 带 Authorization 头."""
    if svc._requests_session is None:
        svc._requests_session = requests.Session()
        svc._requests_session.headers.update({
            "Authorization": svc._api_key or "",
            "Accept": "application/json",
        })
    return svc._requests_session


def defaults() -> Any:
    """当前配置 defaults 节."""
    return get_config().defaults
