"""Migration: add title column to director_jobs and backfill from articles.

Run from repo root with the project venv:
    F:/AI-Agent-Local/digital_human/.venv/Scripts/python digital_human/migrations/add_director_job_title.py
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
        if not column_exists(conn, "director_jobs", "title"):
            conn.execute(
                text("ALTER TABLE director_jobs ADD COLUMN title VARCHAR(256)")
            )
            print("Added director_jobs.title")
        else:
            print("director_jobs.title already exists")

        # Backfill from script.article.title where currently NULL/empty
        result = conn.execute(
            text("""
                UPDATE director_jobs
                SET title = COALESCE(
                    (SELECT articles.title
                     FROM scripts
                     JOIN articles ON articles.id = scripts.article_id
                     WHERE scripts.id = director_jobs.script_id),
                    ''
                )
                WHERE title IS NULL OR title = ''
            """)
        )
        print(f"Backfilled {result.rowcount} director_jobs.title rows")


if __name__ == "__main__":
    main()
