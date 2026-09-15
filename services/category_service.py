"""Service for managing category configurations and Glicko-2 parameters."""
import math
import sqlite3

from config import DEFAULT_RATING, GLICKO_K, GLICKO_M
from services.category_utils import format_glicko_category
from services.db import get_db
from services.timezone_service import current_timestamp


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
    columns = {row["name"] for row in conn.execute(
        "PRAGMA table_info(category_config)").fetchall()}
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


def glicko_to_category(glicko, decimals=0, k=None, m=None):
    """Convert a rating using the persisted category scale by default."""
    if k is None or m is None:
        config = get_category_config()
        k = config["glicko_k"] if k is None else k
        m = config["glicko_m"] if m is None else m

    return format_glicko_category(glicko, decimals=decimals, k=k, m=m)


def handicap_points(rating_a, rating_b, handicap_stones, k=None, m=None):
    """Return the raw-rating shift for Black's effective handicap strength."""
    return handicap_rating_adjustments(
        rating_a,
        rating_b,
        handicap_stones,
        k=k,
        m=m,
    )[0]


def handicap_rating_adjustments(white_rating, black_rating, handicap_stones, k=None, m=None):
    """Return opponent-rating shifts for White's and Black's calculations."""
    if k is None or m is None:
        config = get_category_config()
        k = config["glicko_k"] if k is None else k
        m = config["glicko_m"] if m is None else m
    if not k or not m:
        raise ValueError("Category parameters must be non-zero")

    try:
        white_rating = float(white_rating)
    except (TypeError, ValueError):
        white_rating = DEFAULT_RATING
    try:
        black_rating = float(black_rating)
    except (TypeError, ValueError):
        black_rating = DEFAULT_RATING
    if not math.isfinite(white_rating) or white_rating <= 0:
        white_rating = DEFAULT_RATING
    if not math.isfinite(black_rating) or black_rating <= 0:
        black_rating = DEFAULT_RATING

    category_factor = math.exp(abs(float(handicap_stones)) / float(k))
    return (
        black_rating * (category_factor - 1.0),
        white_rating * (1.0 / category_factor - 1.0),
    )


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

    gap = category_value(rating_stronger, k=k, m=m) - \
        category_value(rating_weaker, k=k, m=m)
    stones = round(gap)
    return max(0, min(max_stones, stones))
