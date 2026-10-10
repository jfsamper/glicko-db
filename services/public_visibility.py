"""Visibility rules that keep draft tournaments and their games out of public views."""
from services.auth_service import user_has_permission

TOURNAMENT_TABLES = ("tournaments", "tournament_rounds", "tournament_pairings")

# Correlated subquery: a game is hidden when its pairing belongs to a draft tournament.
DRAFT_GAME_EXCLUSION = """NOT EXISTS (
    SELECT 1
    FROM tournament_pairings p
    JOIN tournament_rounds r ON r.id = p.round_id
    JOIN tournaments t ON t.id = r.tournament_id
    WHERE p.id = {alias}.tournament_pairing_id AND t.status = 'draft'
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
    """SQL condition for a ``matches`` alias that hides games from draft tournaments.

    Databases without the tournament schema cannot hold draft games, so the condition
    is a tautology there.
    """
    if not _has_tournament_schema(conn):
        return "1 = 1"
    return DRAFT_GAME_EXCLUSION.format(alias=alias)


def can_view_draft_tournaments():
    """Directors, operators and administrators may see drafts; everyone else may not."""
    return user_has_permission("operator")


def draft_only_sgf_filenames(conn):
    """SGF files linked only to games of draft tournaments, which public pages must hide."""
    if not _has_tournament_schema(conn):
        return set()
    draft_linked = {
        row[0]
        for row in conn.execute(
            """
            SELECT m.sgf_filename
            FROM matches m
            JOIN tournament_pairings p ON p.id = m.tournament_pairing_id
            JOIN tournament_rounds r ON r.id = p.round_id
            JOIN tournaments t ON t.id = r.tournament_id
            WHERE t.status = 'draft' AND m.sgf_filename IS NOT NULL
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
    return draft_linked - public_linked
