"""Owns the raw SQLite connection factory used across the application."""
import sqlite3

import config


def get_db():
    # Read config.DB_PATH by module attribute (not a bound import) so tests
    # that monkeypatch config.DB_PATH take effect without needing to
    # rebind this function.
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
