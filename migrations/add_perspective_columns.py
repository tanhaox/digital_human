"""Migration: add perspective columns to articles and scripts.

Run from repo root with the project venv:
    F:/AI-Agent-Local/digital_human/.venv/Scripts/python digital_human/migrations/add_perspective_columns.py
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
            "articles.perspective_1": "TEXT",
            "scripts.perspective_2": "TEXT",
        }
        for col_full, col_type in columns.items():
            table, col_name = col_full.split(".")
            if not column_exists(conn, table, col_name):
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                )
                print(f"Added {table}.{col_name}")
            else:
                print(f"{table}.{col_name} already exists")

    print("Migration complete.")


if __name__ == "__main__":
    main()
