"""Participant lookup and OpenGotha name reconciliation helpers."""
from difflib import SequenceMatcher

from services.helpers import normalize_key, normalize_text


def player_lookup(conn):
    rows = conn.execute(
        "SELECT id, display_name, first_name, last_name, rating FROM players"
    ).fetchall()
    lookup = {}
    for row in rows:
        for value in (
            row["display_name"],
            f"{row['first_name']} {row['last_name']}",
            f"{row['last_name']}{row['first_name']}",
        ):
            if value:
                lookup[normalize_key(value)] = row
    return lookup


def _name_tokens(value):
    text = normalize_text(value).lower()
    if not text:
        return []
    tokens = []
    for raw in text.replace("-", " ").split():
        token = normalize_key(raw)
        if token:
            tokens.append(token)
    return tokens


def name_similarity_score(target_name, candidate_name):
    target_tokens = _name_tokens(target_name)
    candidate_tokens = _name_tokens(candidate_name)
    if not target_tokens or not candidate_tokens:
        return SequenceMatcher(
            None, normalize_key(target_name), normalize_key(candidate_name)
        ).ratio()

    target_set = set(target_tokens)
    candidate_set = set(candidate_tokens)
    if target_set.issubset(candidate_set) or candidate_set.issubset(target_set):
        return 0.95

    common_tokens = len(target_set & candidate_set)
    if common_tokens:
        overlap = common_tokens / max(len(target_set), len(candidate_set))
        ordered_ratio = SequenceMatcher(
            None,
            " ".join(sorted(target_tokens)),
            " ".join(sorted(candidate_tokens)),
        ).ratio()
        return max(overlap, ordered_ratio)

    return SequenceMatcher(
        None,
        " ".join(sorted(target_tokens)),
        " ".join(sorted(candidate_tokens)),
    ).ratio()


def suggest_player_name(name, conn):
    """Return a close active player name when exact lookup fails."""
    if not name:
        return None
    rows = conn.execute(
        "SELECT display_name, first_name, last_name FROM players WHERE active = 1"
    ).fetchall()
    best_match = None
    best_score = 0.0
    for row in rows:
        for candidate in (row["display_name"], f"{row['first_name']} {row['last_name']}"):
            if not candidate:
                continue
            score = name_similarity_score(name, candidate)
            if score > best_score:
                best_score = score
                best_match = candidate
    return best_match if best_score >= 0.82 else None


def list_pending_players(conn, tournament_id):
    """Return pending, not-yet-created players for a tournament."""
    return conn.execute(
        """
        SELECT *
        FROM tournament_pending_players
        WHERE tournament_id = ?
        ORDER BY rank, display_name
        """,
        (tournament_id,),
    ).fetchall()
