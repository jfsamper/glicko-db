"""Service for managing category configurations and Glicko-2 parameters."""
import math
import sqlite3

from config import DEFAULT_RATING, GLICKO_K, GLICKO_M
from services.common import current_timestamp, get_db


def _as_positive_float(value, field_name):
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive number") from exc

    if not math.isfinite(numeric_value) or numeric_value <= 0:
        raise ValueError(f"{field_name} must be a positive number")

    return numeric_value

def get_category_config(conn=None):
    owns_conn = conn is None
    if conn is None:
        conn = get_db()

    try:
        try:
            row = conn.execute(
                """
                SELECT
                    glicko_k,
                    glicko_m,
                    updated_at
                FROM category_config
                WHERE id = 1
                """
            ).fetchone()
        except sqlite3.Error:
            row = None
    finally:
        if owns_conn:
            conn.close()

    if row is None:
        return {
            "glicko_k": GLICKO_K,
            "glicko_m": GLICKO_M,
            "updated_at": None,
        }

    return dict(row)


def update_category_config(
    glicko_k,
    glicko_m,
):
    glicko_k = _as_positive_float(glicko_k, "glicko_k")
    glicko_m = _as_positive_float(glicko_m, "glicko_m")

    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS category_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            glicko_k REAL,
            glicko_m REAL,
            updated_at TEXT
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(category_config)").fetchall()}
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE category_config ADD COLUMN updated_at TEXT")

    conn.execute(
        """
        UPDATE category_config
        SET
            glicko_k = ?,
            glicko_m = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            glicko_k,
            glicko_m,
            current_timestamp(),
        ),
    )

    conn.commit()
    conn.close()


from services.rating_service import glicko_to_category  # noqa: E402, F401
