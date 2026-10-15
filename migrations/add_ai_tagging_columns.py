"""Migration: add AI tagging columns to video_assets.

Run from repo root with the project venv:
    F:/AI-Agent-Local/digital_human/.venv/Scripts/python digital_human/migrations/add_ai_tagging_columns.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from app.config import get_config, load_config, set_config
from app.database import get_engine, init_db


def column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def main() -> None:
    set_config(load_config())
    cfg = get_config()
    init_db(cfg.app.database_url)
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Database engine not initialized")

    with engine.begin() as conn:
        columns = {
            "ai_tagged_at": "DATETIME",
            "ai_tag_model": "VARCHAR(64)",
            "ai_confidence": "JSON",
            "ai_tags_extra": "JSON",
        }
        for col_name, col_type in columns.items():
            if not column_exists(conn, "video_assets", col_name):
                conn.execute(
                    text(f"ALTER TABLE video_assets ADD COLUMN {col_name} {col_type}")
                )
                print(f"Added video_assets.{col_name}")
            else:
                print(f"video_assets.{col_name} already exists")

    print("Migration complete.")


if __name__ == "__main__":
    main()
