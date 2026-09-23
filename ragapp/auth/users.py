"""User account creation and authentication."""

import sqlite3

from ragapp.auth.db import get_connection
from ragapp.auth.passwords import hash_password, verify_password


def _normalize_username(username):
    """
    Canonicalize a username for both auth and storage.

    Usernames are treated as case-insensitive: this must match
    exactly the normalization used by
    ragapp.storage.collections / ragapp.workspace.manager, or two
    differently-cased accounts can end up sharing one user's private
    document store and vector collection.
    """
    return username.strip().lower()


def create_user(username, password, is_admin=False):
    username = _normalize_username(username)

    if not username:
        raise ValueError("Username cannot be empty.")

    if not password:
        raise ValueError("Password cannot be empty.")

    password_hash, salt = hash_password(password)

    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    username,
                    password_hash,
                    salt,
                    is_admin
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    username,
                    password_hash,
                    salt,
                    1 if is_admin else 0,
                ),
            )
            connection.commit()
    except sqlite3.IntegrityError:
        raise ValueError(
            f"User '{username}' already exists."
        )


def is_admin_user(username):
    """Return True if the given (already-authenticated) username has admin rights."""

    username = _normalize_username(username)

    with get_connection() as connection:
        row = connection.execute(
            "SELECT is_admin FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    return bool(row and row[0])


def authenticate_user(username, password):
    username = _normalize_username(username)

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT username, password_hash, salt
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()

    if row is None:
        return None

    stored_username, stored_hash, stored_salt = row

    if verify_password(
        password,
        stored_hash,
        stored_salt,
    ):
        return stored_username

    return None
