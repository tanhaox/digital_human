# -*- coding: utf-8 -*-
"""SQLAlchemy 模型基础设施 — 声明式基类与公共 helper."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base", "_now", "_new_uuid"]


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())
