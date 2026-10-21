"""Visibility rules that keep unpublished tournament games out of public views."""
from services.auth_service import user_has_permission

TOURNAMENT_TABLES = ("tournaments", "tournament_rounds", "tournament_pairings")

# Correlated subquery: a game is hidden when its pairing belongs to an unpublished tournament.
UNPUBLISHED_GAME_EXCLUSION = """NOT EXISTS (
    SELECT 1
    FROM tournament_pairings p
    JOIN tournament_rounds r ON r.id = p.round_id
    JOIN tournaments t ON t.id = r.tournament_id
    WHERE p.id = {alias}.tournament_pairing_id
      AND t.status IN ('draft', 'canceled')
)"""


def _has_tournament_schema(conn):
    placeholders = ", ".join("?" for _ in TOURNAMENT_TABLES)
    tables = {
        row[0]
        for row in conn.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
            TOURNAMENT_TABLES,
        ).fetchall()
    }
    if tables != set(TOURNAMENT_TABLES):
        return False
    columns = {row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()}
    return "tournament_pairing_id" in columns


def public_match_condition(conn, alias="m"):
    """SQL condition for a ``matches`` alias that hides games from unpublished tournaments.

    Databases without the tournament schema cannot hold unpublished tournament
    games, so the condition is a tautology there.
    """
    if not _has_tournament_schema(conn):
        return "1 = 1"
    return UNPUBLISHED_GAME_EXCLUSION.format(alias=alias)


def rating_match_condition(conn, alias="m"):
    """SQL condition for standalone matches and rated games from completed tournaments."""
    if not _has_tournament_schema(conn):
        return "1 = 1"
    tournament_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
    }
    rated_condition = (
        "AND COALESCE(t.is_rated, 1) = 1"
        if "is_rated" in tournament_columns
        else ""
    )
    return f"""(
        {alias}.tournament_pairing_id IS NULL
        OR EXISTS (
            SELECT 1
            FROM tournament_pairings p
            JOIN tournament_rounds r ON r.id = p.round_id
            JOIN tournaments t ON t.id = r.tournament_id
            WHERE p.id = {alias}.tournament_pairing_id
              AND t.status = 'completed'
              {rated_condition}
        )
    )"""


def unrated_match_expression(conn, alias="m"):
    """SQL expression marking completed unrated tournament matches for public display."""
    if not _has_tournament_schema(conn):
        return "0 AS is_unrated"
    tournament_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
    }
    if "is_rated" not in tournament_columns:
        return "0 AS is_unrated"
    return f"""CASE WHEN EXISTS (
        SELECT 1
        FROM tournament_pairings p
        JOIN tournament_rounds r ON r.id = p.round_id
        JOIN tournaments t ON t.id = r.tournament_id
        WHERE p.id = {alias}.tournament_pairing_id
          AND t.status = 'completed'
          AND COALESCE(t.is_rated, 1) = 0
    ) THEN 1 ELSE 0 END AS is_unrated"""


def can_view_draft_tournaments():
    """Directors, operators and administrators may see drafts; everyone else may not."""
    return user_has_permission("operator")


def unpublished_tournament_sgf_filenames(conn):
    """SGF files linked only to unpublished tournament games, hidden from public pages."""
    if not _has_tournament_schema(conn):
        return set()
    unpublished_linked = {
        row[0]
        for row in conn.execute(
            """
            SELECT m.sgf_filename
            FROM matches m
            JOIN tournament_pairings p ON p.id = m.tournament_pairing_id
            JOIN tournament_rounds r ON r.id = p.round_id
            JOIN tournaments t ON t.id = r.tournament_id
            WHERE t.status IN ('draft', 'canceled') AND m.sgf_filename IS NOT NULL
            """
        ).fetchall()
    }
    public_linked = {
        row[0]
        for row in conn.execute(
            f"""
            SELECT m.sgf_filename
            FROM matches m
            WHERE m.sgf_filename IS NOT NULL AND {public_match_condition(conn, 'm')}
            """
        ).fetchall()
    }
    return unpublished_linked - public_linked
