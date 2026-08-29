"""Service for calculating home page statistics and player badges."""
from calendar import monthrange
from datetime import date, datetime

from services.common import TRANSLATIONS, get_db, server_date


def _coerce_date(value):
    if not value:
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _period_bounds(period):
    today = server_date()

    if period == "year":
        return date(today.year, 1, 1), date(today.year, 12, 31)

    if period == "quarter":
        quarter = (today.month - 1) // 3 + 1
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        last_day = monthrange(today.year, end_month)[1]
        return date(today.year, start_month, 1), date(today.year, end_month, last_day)

    return None, None


def _normalized_sql_date(column_name):
    return (
        "CASE "
        f"WHEN {column_name} IS NULL THEN NULL "
        f"WHEN {column_name} GLOB '????-??-??' THEN {column_name} "
        f"WHEN {column_name} GLOB '????/??/??' THEN REPLACE({column_name}, '/', '-') "
        f"WHEN {column_name} GLOB '??/??/????' THEN substr({column_name}, 7, 4) || '-' || substr({column_name}, 4, 2) || '-' || substr({column_name}, 1, 2) "
        f"WHEN {column_name} GLOB '??-??-????' THEN substr({column_name}, 7, 4) || '-' || substr({column_name}, 4, 2) || '-' || substr({column_name}, 1, 2) "
        "ELSE NULL END"
    )


def _matches_in_period(conn, start_date=None, end_date=None):
    normalized_match_date = _normalized_sql_date("match_date")
    if start_date is None and end_date is None:
        return conn.execute(
            "SELECT id, match_date, white_player_id, black_player_id, result FROM matches ORDER BY match_date, id"
        ).fetchall()

    query = (
        "SELECT id, match_date, white_player_id, black_player_id, result "
        "FROM matches WHERE "
    )
    params = []
    clauses = []
    if start_date is not None:
        clauses.append(f"{normalized_match_date} >= ?")
        params.append(start_date.isoformat())
    if end_date is not None:
        clauses.append(f"{normalized_match_date} <= ?")
        params.append(end_date.isoformat())
    query += " AND ".join(clauses)
    query += " ORDER BY match_date, id"
    return conn.execute(query, params).fetchall()


def _snapshots_in_period(conn, player_id, start_date=None, end_date=None):
    normalized_snapshot_date = _normalized_sql_date("snapshot_date")
    if start_date is None and end_date is None:
        return conn.execute(
            "SELECT snapshot_date, rating FROM rating_snapshots WHERE player_id = ? ORDER BY snapshot_date, id",
            (player_id,),
        ).fetchall()

    query = "SELECT snapshot_date, rating FROM rating_snapshots WHERE player_id = ?"
    params = [player_id]
    if start_date is not None:
        query += f" AND {normalized_snapshot_date} >= ?"
        params.append(start_date.isoformat())
    if end_date is not None:
        query += f" AND {normalized_snapshot_date} <= ?"
        params.append(end_date.isoformat())
    query += " ORDER BY snapshot_date, id"
    return conn.execute(query, params).fetchall()


def _build_metric_entries(players, metric_values, min_value=1):
    ranked = []

    for player in players:

        if player["player_id"] not in metric_values:
            continue

        value = metric_values[player["player_id"]]

        if value is None or value < min_value:
            continue

        ranked.append({
            "player_id": player["player_id"],
            "display_name": player["display_name"],
            "value": value,
            "display_value": str(value),
        })

    ranked.sort(
        key=lambda entry: (
            -entry["value"],
            entry["display_name"].lower()
        )
    )

    return ranked[:5]


def _period_stats(conn, period):
    start_date, end_date = _period_bounds(period)
    matches = _matches_in_period(conn, start_date, end_date)
    players = conn.execute(
        "SELECT id AS player_id, display_name, initial_rating, rating FROM players"
    ).fetchall()

    player_rows = [dict(player) for player in players]
    player_stats = {
        row["player_id"]: {
            "games": 0,
            "wins": 0,
            "wins_as_white": 0,
            "wins_as_black": 0,
        }
        for row in player_rows
    }

    for match in matches:
        white_id = match["white_player_id"]
        black_id = match["black_player_id"]
        result = match["result"]

        if white_id in player_stats:
            player_stats[white_id]["games"] += 1
            if result == "1-0":
                player_stats[white_id]["wins"] += 1
                player_stats[white_id]["wins_as_white"] += 1

        if black_id in player_stats:
            player_stats[black_id]["games"] += 1
            if result == "0-1":
                player_stats[black_id]["wins"] += 1
                player_stats[black_id]["wins_as_black"] += 1

    metric_values = {
        "most_active": {},
        "most_wins": {},
        "most_wins_as_white": {},
        "most_wins_as_black": {},
        "relative_glicko_gain": {},
    }

    for player in player_rows:
        player_id = player["player_id"]
        stats = player_stats.get(player_id, {"games": 0, "wins": 0, "wins_as_white": 0, "wins_as_black": 0})
        metric_values["most_active"][player_id] = stats["games"]
        metric_values["most_wins"][player_id] = stats["wins"]
        metric_values["most_wins_as_white"][player_id] = stats["wins_as_white"]
        metric_values["most_wins_as_black"][player_id] = stats["wins_as_black"]

        snapshots = _snapshots_in_period(conn, player_id, start_date, end_date)
        if not snapshots:
            metric_values["relative_glicko_gain"][player_id] = 0
        else:
            start_rating = float(snapshots[0]["rating"])
            end_rating = float(snapshots[-1]["rating"])

            if start_rating == 0:
                metric_values["relative_glicko_gain"][player_id] = 0
            else:
                if len(snapshots) < 2:
                    metric_values["relative_glicko_gain"][player_id] = 0
                else:
                    delta = ((end_rating - start_rating) / start_rating) * 100.0
                    metric_values["relative_glicko_gain"][player_id] = round(delta, 1)

    return {
        "most_active": _build_metric_entries(player_rows, metric_values["most_active"], min_value=1),
        "most_wins": _build_metric_entries(player_rows, metric_values["most_wins"], min_value=1),
        "most_wins_as_white": _build_metric_entries(player_rows, metric_values["most_wins_as_white"], min_value=1),
        "most_wins_as_black": _build_metric_entries(player_rows, metric_values["most_wins_as_black"], min_value=1),
        "relative_glicko_gain": [
            {
                "player_id": entry["player_id"],
                "display_name": entry["display_name"],
                "value": entry["value"],
                "display_value": f"{entry['value']:+.1f}%",
            }
            for entry in _build_metric_entries(player_rows, metric_values["relative_glicko_gain"], min_value=0)
        ],
    }


def build_home_stats(conn=None):
    if conn is None:
        conn = get_db()

    stats = {}
    for period in ("all_time", "year", "quarter"):
        stats[period] = _period_stats(conn, period)

    return stats


def build_player_badges(player_id, translations=None, conn=None):
    if translations is None:
        translations = TRANSLATIONS["en"]

    if conn is None:
        conn = get_db()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
    if "games_played" in columns:
        player_row = conn.execute(
            "SELECT games_played FROM players WHERE id = ?",
            (player_id,),
        ).fetchone()
        if player_row is None:
            return []
        try:
            games_played = int(player_row["games_played"])
        except (TypeError, ValueError):
            return []
        if games_played <= 0:
            return []
    # consider caching `_period_stats()` results per request if profiles become a bottleneck.
    all_time_stats = _period_stats(conn, "all_time")
    year_stats = _period_stats(conn, "year")
    quarter_stats = _period_stats(conn, "quarter")

    yearly_metrics = {}
    yearly_matches = conn.execute(
        """
        SELECT substr(match_date, 1, 4) AS year, white_player_id, black_player_id, result
        FROM matches
        WHERE match_date IS NOT NULL
          AND substr(match_date, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'
        """
    ).fetchall()
    for match in yearly_matches:
        year = match["year"]
        metrics = yearly_metrics.setdefault(year, {})
        for participant_id in (match["white_player_id"], match["black_player_id"]):
            player_metrics = metrics.setdefault(participant_id, {"games": 0, "wins": 0})
            player_metrics["games"] += 1
        winner_id = (
            match["white_player_id"] if match["result"] == "1-0"
            else match["black_player_id"] if match["result"] == "0-1"
            else None
        )
        if winner_id is not None:
            metrics[winner_id]["wins"] += 1

    yearly_snapshots = conn.execute(
        """
        SELECT player_id, substr(snapshot_date, 1, 4) AS year, rating
        FROM rating_snapshots
        WHERE snapshot_date IS NOT NULL
          AND substr(snapshot_date, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'
        ORDER BY snapshot_date, id
        """
    ).fetchall()
    snapshot_ranges = {}
    for snapshot in yearly_snapshots:
        key = (snapshot["year"], snapshot["player_id"])
        values = snapshot_ranges.setdefault(key, [])
        values.append(float(snapshot["rating"]))
    for (year, snapshot_player_id), values in snapshot_ranges.items():
        if len(values) > 1:
            yearly_metrics.setdefault(year, {}).setdefault(
                snapshot_player_id, {"games": 0, "wins": 0}
            )["rating_increase"] = round(values[-1] - values[0], 1)

    def add_yearly_badge(metric_key, label):
        for year in sorted(yearly_metrics, reverse=True):
            entries = [
                (metrics.get(metric_key, 0), candidate_id)
                for candidate_id, metrics in yearly_metrics[year].items()
                if metrics.get(metric_key, 0) > 0
            ]
            if not entries:
                continue
            top_value, top_player_id = sorted(entries, key=lambda item: (-item[0], item[1]))[0]
            if top_player_id == player_id:
                badges.append({
                    "label": label,
                    "value": top_value,
                    "period": year,
                })

    badges = []

    def add_badge(metric_key, label, period_key, period_label):
        bucket = all_time_stats if period_key == "all_time" else year_stats if period_key == "year" else quarter_stats
        entries = bucket.get(metric_key, [])
        if not entries:
            return False

        top_entry = entries[0] if entries else None
        if top_entry is None or top_entry.get("player_id") != player_id:
            return False

        value = top_entry.get("value")
        if value is None or value <= 0:
            return False
        badges.append({
            "label": label,
            "value": top_entry.get("display_value") or top_entry.get("value"),
            "period": period_label,
        })
        return True

    add_badge("most_active", translations["stats_metric_active"], "all_time", translations["stats_period_all_time"])
    add_badge("most_wins", translations["stats_metric_wins"], "all_time", translations["stats_period_all_time"])
    add_yearly_badge("games", translations["stats_metric_active"])
    add_yearly_badge("wins", translations["stats_metric_wins"])
    add_yearly_badge("rating_increase", translations["stats_metric_glicko"])
    add_badge("most_active", translations["stats_metric_active"], "quarter", translations["stats_period_quarter"])
    add_badge("most_wins", translations["stats_metric_wins"], "quarter", translations["stats_period_quarter"])

    add_badge("relative_glicko_gain", translations["stats_metric_glicko"], "all_time", translations["stats_period_all_time"])
    add_badge("relative_glicko_gain", translations["stats_metric_glicko"], "year", translations["stats_period_year"])
    add_badge("relative_glicko_gain", translations["stats_metric_glicko"], "quarter", translations["stats_period_quarter"])

    top_player = conn.execute(
        "SELECT id, rating FROM players WHERE rating IS NOT NULL ORDER BY rating DESC, id ASC LIMIT 1"
    ).fetchone()
    if top_player is not None and top_player["id"] == player_id:
        badges.append({
            "label": translations["stats_badge_top_rated"],
            "value": f"{float(top_player['rating']):.1f}",
            "period": f"{translations['period_current']}",
        })

    return badges
