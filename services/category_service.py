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


def category_value(rating, k=None, m=None):
    """Continuous (unrounded) category value, e.g. 2.4 for "roughly 3 dan
    trending down" or -5.1 for "roughly 6 kyu". This is the same formula
    glicko_to_category() floors/rounds for display; handicap math needs
    the raw value.
    """
    if k is None or m is None:
        config = get_category_config()
        k = config["glicko_k"] if k is None else k
        m = config["glicko_m"] if m is None else m
    if not k or not m:
        raise ValueError("Category parameters must be non-zero")

    try:
        rating = float(rating)
    except (TypeError, ValueError):
        rating = DEFAULT_RATING
    if not math.isfinite(rating) or rating <= 0:
        rating = DEFAULT_RATING

    return (math.log(rating / m) * k) - 29


def handicap_points(rating_a, rating_b, handicap_stones, k=None, m=None):
    """Rating-point equivalent of a number of handicap stones.

    Uses the local slope of this app's own (logarithmic) category curve,
    evaluated at the midpoint of the two players' ratings, rather than a
    flat constant like the commonly cited ~100 points/stone convention.
    On a log category scale, raw points-per-stone scale with the rating
    itself (d(rating)/d(category) = rating/k), so a flat constant would
    over-adjust weak-kyu handicap games and under-adjust strong-dan ones
    relative to this app's own kyu/dan labels. See CODE_REVIEW.md notes
    on the 2026-08 handicap design discussion for the full rationale.

    Returns a non-negative number of raw Glicko rating points.
    """
    if k is None or m is None:
        config = get_category_config()
        k = config["glicko_k"] if k is None else k
        m = config["glicko_m"] if m is None else m
    if not k:
        raise ValueError("Category parameters must be non-zero")

    midpoint = (float(rating_a) + float(rating_b)) / 2.0
    points_per_stone = midpoint / k
    return abs(float(handicap_stones)) * points_per_stone


def suggested_handicap_stones(rating_stronger, rating_weaker, k=None, m=None, max_stones=9):
    """Auto-suggested handicap in stones from the category gap between two
    ratings, clamped to the conventional 0-9 stone range used in Go.

    This is a starting point for tournament pairing, not a final value --
    callers (admin UI, pairing generation) should let a tournament
    director override it per pairing.
    """
    if k is None or m is None:
        config = get_category_config()
        k = config["glicko_k"] if k is None else k
        m = config["glicko_m"] if m is None else m

    gap = category_value(rating_stronger, k=k, m=m) - category_value(rating_weaker, k=k, m=m)
    stones = round(gap)
    return max(0, min(max_stones, stones))


# Circular import workaround
# Consider moving `glicko_to_category()` to a shared utility module (e.g., `services/rating_utils.py`) 
# that neither `rating_service` nor `category_service` depends on deeply. Functional but hard to follow.
from services.rating_service import glicko_to_category  # noqa: E402, F401
