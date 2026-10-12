"""One SQLite file per project.

Metadata index, semantic (embedding) index and instructions used to live in
three separate files under .system/. They share one file now:
    <project>/.system/project.db
Table names do not overlap, so each class keeps managing its own tables.
Legacy files are merged in automatically the first time they are seen.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_NAME = "project.db"
LEGACY = ("metadata.db", "semantic_index.db", "instructions.db")


def project_db_path(store) -> Path:
    path = Path(store.root) / ".system" / DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def merge_legacy_dbs(path: Path) -> None:
    """Copy rows from old per-feature DB files into project.db (idempotent)."""
    path = Path(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS _migrated(name TEXT PRIMARY KEY)")
        done = {r[0] for r in conn.execute("SELECT name FROM _migrated")}
        for name in LEGACY:
            legacy = path.parent / name
            if name in done or not legacy.exists():
                continue
            try:
                conn.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))
                tables = conn.execute(
                    "SELECT name, sql FROM legacy.sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                for tname, sql in tables:
                    if not sql:
                        continue
                    conn.execute(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
                    conn.execute(f'INSERT OR IGNORE INTO main."{tname}" SELECT * FROM legacy."{tname}"')
                conn.execute("INSERT OR IGNORE INTO _migrated VALUES(?)", (name,))
                conn.commit()
            finally:
                try:
                    conn.execute("DETACH DATABASE legacy")
                except sqlite3.Error:
                    pass
            try:  # keep a backup instead of deleting
                legacy.replace(legacy.with_suffix(".db.migrated"))
            except OSError:
                pass  # file still locked (Windows) - marker table prevents re-import
    finally:
        conn.close()