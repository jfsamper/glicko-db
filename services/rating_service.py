# services/rating_service.py
"""Service for managing player ratings and Glicko-2 calculations."""
from config import DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY, TAU

import logging
import math
import sqlite3

from services.common import current_timestamp, get_db
from services.glicko2 import Player

logger = logging.getLogger(__name__)


def _match_order_clause(conn):
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(matches)").fetchall()
    }
    if "round_number" not in columns:
        return "1, id"

    return "CASE WHEN COALESCE(CAST(round_number AS INTEGER), 0) <= 0 THEN 1 ELSE CAST(round_number AS INTEGER) END, id"


def clear_rating_state():

    conn = get_db()

    conn.execute(
        "DELETE FROM rating_state"
    )

    conn.commit()
    conn.close()


def player_state_from_row(row, conn=None, cfg=None):

    if cfg is None:
        cfg = get_rating_config(conn=conn)

    return {
        "id": row["id"],
        "rating": (
            row["initial_rating"]
            if row["initial_rating"] is not None
            else cfg["default_rating"]
        ),
        "rd": cfg["default_rd"],
        "volatility": cfg["default_volatility"],
    }


def parse_result(result):
    result = str(result).strip().lower()
    if result in {"1-0", "white"}:
        return 1.0
    if result in {"0-1", "black"}:
        return 0.0
    if result in {"1/2-1/2", "½-½", "draw", "d", "0.5-0.5"}:
        return 0.5
    return None

def glicko2_update(
    rating,
    rd,
    vol,
    opponent_rating,
    opponent_rd,
    opponent_vol,
    score,
    conn=None,
    tau=None,
):

    if tau is None:
        tau = get_rating_config(conn=conn)["tau"]
    Player._tau = tau
    player = Player(
        rating=rating,
        rd=rd,
        vol=vol,
    )

    player.update_player(
        [opponent_rating],
        [opponent_rd],
        [score],
    )

    return {
        "rating": round(player.rating, 2),
        "rd": round(player.rd, 2),
        "volatility": round(player.vol, 6),
    }

def recompute_ratings(conn=None):
    owns_conn = conn is None
    if conn is None:
        conn = get_db()

    try:
        conn.execute("DELETE FROM rating_snapshots")
        rows = conn.execute("SELECT * FROM players").fetchall()
        cfg = get_rating_config(conn=conn)
        Player._tau = cfg["tau"]

        for row in rows:
            conn.execute(
                """
                UPDATE players
                SET
                    rating = ?,
                    rd = ?,
                    volatility = ?
                WHERE id = ?
                """,
                (
                    row["initial_rating"]
                    if row["initial_rating"] is not None
                    else cfg["default_rating"],
                    cfg["default_rd"],
                    cfg["default_volatility"],
                    row["id"],
                ),
            )

        states = {
            row["id"]: player_state_from_row(row, conn=conn, cfg=cfg)
            for row in rows
        }
        matches = conn.execute(
            f"SELECT * FROM matches ORDER BY match_date, {_match_order_clause(conn)}"
        ).fetchall()

        for match in matches:
            white_state = states[match["white_player_id"]]
            black_state = states[match["black_player_id"]]
            score = parse_result(match["result"])
            if score is None:
                continue

            white_score = score
            black_score = 1.0 - score if score != 0.5 else 0.5

            white_update = glicko2_update(
                white_state["rating"],
                white_state["rd"],
                white_state["volatility"],
                black_state["rating"],
                black_state["rd"],
                black_state["volatility"],
                white_score,
                conn=conn,
                tau=cfg["tau"],
            )
            black_update = glicko2_update(
                black_state["rating"],
                black_state["rd"],
                black_state["volatility"],
                white_state["rating"],
                white_state["rd"],
                white_state["volatility"],
                black_score,
                conn=conn,
                tau=cfg["tau"],
            )

            states[match["white_player_id"]] = {
                "id": match["white_player_id"],
                "rating": white_update["rating"],
                "rd": white_update["rd"],
                "volatility": white_update["volatility"],
            }
            states[match["black_player_id"]] = {
                "id": match["black_player_id"],
                "rating": black_update["rating"],
                "rd": black_update["rd"],
                "volatility": black_update["volatility"],
            }
            conn.execute(
                "UPDATE players SET rating = ?, rd = ?, volatility = ? WHERE id = ?",
                (states[match["white_player_id"]]["rating"], states[match["white_player_id"]]["rd"], states[match["white_player_id"]]["volatility"], match["white_player_id"]),
            )
            conn.execute(
                "UPDATE players SET rating = ?, rd = ?, volatility = ? WHERE id = ?",
                (states[match["black_player_id"]]["rating"], states[match["black_player_id"]]["rd"], states[match["black_player_id"]]["volatility"], match["black_player_id"]),
            )
            conn.execute(
                "INSERT INTO rating_snapshots (player_id, snapshot_date, rating, rd, volatility) VALUES (?, ?, ?, ?, ?)",
                (match["white_player_id"], match["match_date"], states[match["white_player_id"]]["rating"], states[match["white_player_id"]]["rd"], states[match["white_player_id"]]["volatility"]),
            )
            conn.execute(
                "INSERT INTO rating_snapshots (player_id, snapshot_date, rating, rd, volatility) VALUES (?, ?, ?, ?, ?)",
                (match["black_player_id"], match["match_date"], states[match["black_player_id"]]["rating"], states[match["black_player_id"]]["rd"], states[match["black_player_id"]]["volatility"]),
            )

        if owns_conn:
            conn.commit()
        clear_dirty_date(conn=conn)
        if owns_conn:
            conn.close()
    finally:
        if owns_conn:
            conn.close()

    logger.debug("Ratings recomputed.")

def get_rating_config(conn=None):
    owns_conn = conn is None
    if conn is None:
        conn = get_db()

    try:
        try:
            row = conn.execute(
                """
                SELECT *
                FROM rating_config
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
            "tau": TAU,
            "default_rating": DEFAULT_RATING,
            "default_rd": DEFAULT_RD,
            "default_volatility": DEFAULT_VOLATILITY,
            "updated_at": None,
        }

    return dict(row)

def update_rating_config(
    tau,
    default_rating,
    default_rd,
    default_volatility,
):

    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rating_config (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            tau REAL,
            default_rating REAL,
            default_rd REAL,
            default_volatility REAL,
            updated_at TEXT
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(rating_config)").fetchall()}
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE rating_config ADD COLUMN updated_at TEXT")

    conn.execute(
        """
        UPDATE rating_config
        SET
            tau = ?,
            default_rating = ?,
            default_rd = ?,
            default_volatility = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            tau,
            default_rating,
            default_rd,
            default_volatility,
            current_timestamp(),
        ),
    )

    conn.commit()
    conn.close()

def players_needing_update():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT DISTINCT p.id
        FROM players p
        JOIN matches m
            ON m.white_player_id = p.id
            OR m.black_player_id = p.id
        LEFT JOIN (
            SELECT
                player_id,
                MAX(snapshot_date) AS last_snapshot
            FROM rating_snapshots
            GROUP BY player_id
        ) s
            ON s.player_id = p.id
        WHERE
            s.last_snapshot IS NULL
            OR m.match_date > s.last_snapshot
        """
    ).fetchall()

    conn.close()

    return [row["id"] for row in rows]

def mark_dirty(match_date, conn=None):
    owns_conn = conn is None
    if conn is None:
        conn = get_db()

    try:
        current = conn.execute(
            """
            SELECT earliest_dirty_date
            FROM rating_state
            WHERE id = 1
            """
        ).fetchone()

        current_date = (
            current["earliest_dirty_date"]
            if current else None
        )

        if (
            current_date is None
            or match_date < current_date
        ):
            conn.execute(
                """
                UPDATE rating_state
                SET earliest_dirty_date = ?
                WHERE id = 1
                """,
                (match_date,)
            )

        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()

def get_dirty_date(conn=None):
    owns_conn = conn is None
    if conn is None:
        conn = get_db()

    try:
        row = conn.execute(
            """
            SELECT earliest_dirty_date
            FROM rating_state
            WHERE id = 1
            """
        ).fetchone()
        return (
            row["earliest_dirty_date"]
            if row
            else None
        )
    finally:
        if owns_conn:
            conn.close()

def clear_dirty_date(conn=None):
    owns_conn = conn is None
    if conn is None:
        conn = get_db()

    try:
        conn.execute(
            """
            UPDATE rating_state
            SET earliest_dirty_date = NULL
            WHERE id = 1
            """
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()

def update_from_latest_snapshot():
    conn = get_db()

    try:
        # Serialize incremental replays so concurrent imports cannot both
        # consume the same dirty marker and overwrite each other's ratings.
        conn.execute("BEGIN IMMEDIATE")
        dirty_date = get_dirty_date(conn=conn)

        if dirty_date is None:
            conn.commit()
            logger.debug("Ratings are already up to date.")
            return

        _replay_from_dirty_date(conn, dirty_date)
        conn.execute(
            """
            UPDATE rating_state
            SET earliest_dirty_date = NULL
            WHERE id = 1
            """
        )
        conn.commit()
        logger.debug("Incremental rating update complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def glicko_to_category(glicko, decimals=0, k=None, m=None):
    """Convert a rating using the persisted category scale by default.

    Callers that are rendering many values can pass ``k`` and ``m`` from one
    ``get_category_config`` call to avoid repeated database reads.
    """
    if k is None or m is None:
        from services.category_service import get_category_config
        config = get_category_config()
        k = config["glicko_k"] if k is None else k
        m = config["glicko_m"] if m is None else m

    if not k or not m:
        raise ValueError("Category parameters must be non-zero")

    try:
        glicko = float(glicko)
    except (TypeError, ValueError):
        glicko = DEFAULT_RATING
    if not math.isfinite(glicko) or glicko <= 0:
        glicko = DEFAULT_RATING

    value = (math.log(glicko / m) * k) - 29

    if decimals == 0:
        r = math.floor(value)

        if r < 0:
            return f"{abs(r)} kyu"

        return f"{r + 1} dan"

    r = round(value, decimals)

    if r < 0:
        return f"{1 - r:.{decimals}f} kyu"

    return f"{r + 1:.{decimals}f} dan"


def _replay_from_dirty_date(conn, dirty_date):
    matches = conn.execute(
        f"""
        SELECT *
        FROM matches
        WHERE match_date >= ?
        ORDER BY match_date, {_match_order_clause(conn)}
        """,
        (dirty_date,),
    ).fetchall()

    affected_players = set()
    for match in matches:
        affected_players.add(match["white_player_id"])
        affected_players.add(match["black_player_id"])

    #
    # Reset affected players to the
    # last known snapshot BEFORE dirty_date
    #
    states = {}
    cfg = get_rating_config(conn=conn)
    Player._tau = cfg["tau"]

    for player_id in affected_players:
        snapshot = conn.execute(
            """
            SELECT
                rating,
                rd,
                volatility
            FROM rating_snapshots
            WHERE player_id = ?
            AND snapshot_date < ?
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            (
                player_id,
                dirty_date,
            ),
        ).fetchone()

        if snapshot:
            states[player_id] = {
                "rating": snapshot["rating"],
                "rd": snapshot["rd"],
                "volatility": snapshot["volatility"],
            }

        else:
            player = conn.execute(
                """
                SELECT *
                FROM players
                WHERE id = ?
                """,
                (player_id,),
            ).fetchone()

            states[player_id] = {
                "rating": (
                    player["initial_rating"]
                    if player["initial_rating"] is not None
                    else cfg["default_rating"]
                ),
                "rd": cfg["default_rd"],
                "volatility": cfg["default_volatility"],
            }

        #
        # remove old snapshots at/after dirty date
        #
        conn.execute(
            """
            DELETE FROM rating_snapshots
            WHERE player_id = ?
              AND snapshot_date >= ?
            """,
            (
                player_id,
                dirty_date,
            ),
        )

    #
    # Replay matches
    #
    for match in matches:

        white_id = match["white_player_id"]
        black_id = match["black_player_id"]

        white_state = states[white_id]
        black_state = states[black_id]

        score = parse_result(
            match["result"]
        )

        if score is None:
            continue

        white_update = glicko2_update(
            white_state["rating"],
            white_state["rd"],
            white_state["volatility"],
            black_state["rating"],
            black_state["rd"],
            black_state["volatility"],
            score,
            conn=conn,
            tau=cfg["tau"],
        )

        black_update = glicko2_update(
            black_state["rating"],
            black_state["rd"],
            black_state["volatility"],
            white_state["rating"],
            white_state["rd"],
            white_state["volatility"],
            1.0 - score if score != 0.5 else 0.5,
            conn=conn,
            tau=cfg["tau"],
        )

        states[white_id] = white_update
        states[black_id] = black_update

        #
        # snapshots
        #
        conn.execute(
            """
            INSERT INTO rating_snapshots
            (
                player_id,
                snapshot_date,
                rating,
                rd,
                volatility
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                white_id,
                match["match_date"],
                white_update["rating"],
                white_update["rd"],
                white_update["volatility"],
            ),
        )

        conn.execute(
            """
            INSERT INTO rating_snapshots
            (
                player_id,
                snapshot_date,
                rating,
                rd,
                volatility
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                black_id,
                match["match_date"],
                black_update["rating"],
                black_update["rd"],
                black_update["volatility"],
            ),
        )

    #
    # persist final ratings
    #
    for player_id, state in states.items():

        conn.execute(
            """
            UPDATE players
            SET
                rating = ?,
                rd = ?,
                volatility = ?
            WHERE id = ?
            """,
            (
                state["rating"],
                state["rd"],
                state["volatility"],
                player_id,
            ),
        )


