"""SQLite connection handling for the auth store."""

import sqlite3
from pathlib import Path

from ragapp import settings

AUTH_DB = Path(settings.AUTH_DB_PATH)


def get_connection():
    return sqlite3.connect(AUTH_DB)


def initialize_database():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # Lightweight migration for databases created before is_admin existed.
        existing_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }

        if "is_admin" not in existing_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
            )

        connection.commit()
