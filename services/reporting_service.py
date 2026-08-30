"""Date-bounded reporting and CSV export services."""
import csv
import math
import re
from datetime import date, datetime
from io import BytesIO, StringIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph

from config import GLICKO_K, GLICKO_M
from services.common import server_date as configured_server_date
from services.rating_service import glicko_to_category

VALID_RESULTS = {"1-0", "0-1", "1/2-1/2"}
REPORT_PERIODS = {"all_time", "year", "quarter", "month", "custom"}


def server_today():
    """Return today's date in the application's fixed server timezone."""
    return configured_server_date()


def parse_report_date(value, field_name="date"):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}; use YYYY-MM-DD") from exc


def _period_dates(period, today):
    if period == "all_time":
        return None, None
    if period == "year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if period == "quarter":
        start_month = ((today.month - 1) // 3) * 3 + 1
        end_month = start_month + 2
        next_month = date(today.year + (end_month == 12), (end_month % 12) + 1, 1)
        return date(today.year, start_month, 1), next_month.fromordinal(next_month.toordinal() - 1)
    if period == "month":
        next_month = date(today.year + (today.month == 12), (today.month % 12) + 1, 1)
        return date(today.year, today.month, 1), next_month.fromordinal(next_month.toordinal() - 1)
    if period == "custom":
        return None, None
    raise ValueError("Invalid report period")


def resolve_report_range(period="year", start_value=None, end_value=None, today=None):
    period = (period or "year").strip().lower()
    if period not in REPORT_PERIODS:
        raise ValueError("Invalid report period")

    today = today or server_today()
    start_date, end_date = _period_dates(period, today)
    explicit_start = parse_report_date(start_value, "start date")
    explicit_end = parse_report_date(end_value, "end date")
    if explicit_start is not None or explicit_end is not None or period == "custom":
        start_date = explicit_start
        end_date = explicit_end

    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("Start date must not be after end date")
    return start_date, end_date


def ensure_tournament_match_identity(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(matches)").fetchall()}
    if "tournament_pairing_id" not in columns:
        conn.execute("ALTER TABLE matches ADD COLUMN tournament_pairing_id INTEGER")
    conn.execute(
        """
        DELETE FROM matches
        WHERE tournament_pairing_id IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id)
              FROM matches
              WHERE tournament_pairing_id IS NOT NULL
              GROUP BY tournament_pairing_id
          )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_tournament_pairing_unique
        ON matches (tournament_pairing_id)
        WHERE tournament_pairing_id IS NOT NULL
        """
    )


def _normalized_date(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def list_report_seasons(conn):
    """Return calendar years represented by valid match dates."""
    seasons = set()
    for row in conn.execute("SELECT match_date FROM matches").fetchall():
        match_date = _normalized_date(row["match_date"])
        if match_date is not None:
            seasons.add(match_date.year)
    return sorted(seasons, reverse=True)


def _result_for_player(result, is_white):
    if result == "1/2-1/2":
        return "draw"
    if (result == "1-0") == is_white:
        return "win"
    return "loss"


def _empty_record(name=""):
    return {
        "display_name": name,
        "games": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
    }


def _finish_record(record):
    games = record["games"]
    record["win_percentage"] = round(record["wins"] * 100.0 / games, 1) if games else None
    return record


def _category_rank(rating, k, m):
    value = (math.log(float(rating) / m) * k) - 29
    return math.floor(value)


def _rating_change(conn, player, start_date, end_date, category_config):
    snapshots = conn.execute(
        "SELECT snapshot_date, rating FROM rating_snapshots WHERE player_id = ? ORDER BY snapshot_date, id",
        (player["id"],),
    ).fetchall()
    parsed = [
        (_normalized_date(row["snapshot_date"]), float(row["rating"]))
        for row in snapshots
        if _normalized_date(row["snapshot_date"]) is not None
    ]
    if start_date is None:
        before_start = []
        if player["initial_rating"] is not None:
            before_start.append(float(player["initial_rating"]))
        elif parsed:
            before_start.append(parsed[0][1])
    else:
        before_start = [rating for snapshot_date, rating in parsed if snapshot_date <= start_date]
        if not before_start and player["initial_rating"] is not None:
            before_start.append(float(player["initial_rating"]))
    through_end = [rating for snapshot_date, rating in parsed if end_date is None or snapshot_date <= end_date]
    if not before_start or not through_end:
        return {
            "start_rating": None,
            "end_rating": None,
            "rating_change_points": None,
            "rating_change_percentage": None,
            "category_change": None,
            "start_category": None,
            "end_category": None,
        }

    start_rating = before_start[-1]
    end_rating = through_end[-1]
    delta = end_rating - start_rating
    k = category_config["glicko_k"]
    m = category_config["glicko_m"]
    return {
        "start_rating": round(start_rating, 2),
        "end_rating": round(end_rating, 2),
        "rating_change_points": round(delta, 2),
        "rating_change_percentage": round(delta * 100.0 / start_rating, 1) if start_rating else None,
        "category_change": _category_rank(end_rating, k, m) - _category_rank(start_rating, k, m),
        "start_category": glicko_to_category(start_rating, k=k, m=m),
        "end_category": glicko_to_category(end_rating, k=k, m=m),
    }


def _load_category_config(conn):
    try:
        row = conn.execute("SELECT glicko_k, glicko_m FROM category_config WHERE id = 1").fetchone()
    except Exception:
        row = None
    if row is None:
        return {"glicko_k": GLICKO_K, "glicko_m": GLICKO_M}
    return {"glicko_k": float(row["glicko_k"]), "glicko_m": float(row["glicko_m"])}


def build_date_report(conn, start_date=None, end_date=None, selected_player_id=None):
    """Build one server-side report object used by both HTML and CSV output."""
    if start_date is not None:
        start_date = parse_report_date(start_date, "start date")
    if end_date is not None:
        end_date = parse_report_date(end_date, "end date")
    if start_date and end_date and start_date > end_date:
        raise ValueError("Start date must not be after end date")

    ensure_tournament_match_identity(conn)
    match_rows = conn.execute(
        """
        SELECT m.id, m.match_date, m.white_player_id, m.black_player_id, m.result,
               m.tournament_pairing_id,
               white.display_name AS white_name, white.country AS white_country, white.club AS white_club,
               black.display_name AS black_name, black.country AS black_country, black.club AS black_club
        FROM matches m
        JOIN players white ON white.id = m.white_player_id
        JOIN players black ON black.id = m.black_player_id
        ORDER BY m.match_date, m.id
        """
    ).fetchall()

    seen_pairings = set()
    matches = []
    excluded_games = 0
    for row in match_rows:
        pairing_id = row["tournament_pairing_id"]
        if pairing_id is not None:
            if pairing_id in seen_pairings:
                excluded_games += 1
                continue
            seen_pairings.add(pairing_id)
        match_date = _normalized_date(row["match_date"])
        if match_date is None or (start_date and match_date < start_date) or (end_date and match_date > end_date):
            excluded_games += 1
            continue
        if row["result"] not in VALID_RESULTS:
            excluded_games += 1
            continue
        matches.append((row, match_date))

    players = conn.execute(
        "SELECT id, display_name, initial_rating, rating FROM players"
    ).fetchall()
    stats = {
        row["id"]: _empty_record(row["display_name"])
        for row in players
    }
    opponent_records = {row["id"]: {} for row in players}
    country_records = {}
    club_records = {}
    summary = {"games": 0}

    def apply_result(record, result):
        record["games"] += 1
        outcome = _result_for_player(result, record["is_white"])
        record[{"win": "wins", "loss": "losses", "draw": "draws"}[outcome]] += 1

    for row, _match_date in matches:
        summary["games"] += 1
        for player_id, opponent_id, is_white, opponent_name, opponent_country, opponent_club in (
            (row["white_player_id"], row["black_player_id"], True, row["black_name"], row["black_country"], row["black_club"]),
            (row["black_player_id"], row["white_player_id"], False, row["white_name"], row["white_country"], row["white_club"]),
        ):
            record = stats.get(player_id)
            if record is None:
                continue
            result_record = {"is_white": is_white, **record}
            apply_result(result_record, row["result"])
            record.update({key: value for key, value in result_record.items() if key != "is_white"})
            opponent = opponent_records[player_id].setdefault(opponent_id, _empty_record(opponent_name))
            opponent_result = {"is_white": is_white, **opponent}
            apply_result(opponent_result, row["result"])
            opponent.update({key: value for key, value in opponent_result.items() if key != "is_white"})

            for group, value in ((country_records, opponent_country), (club_records, opponent_club)):
                label = value or "(unknown)"
                grouped = group.setdefault(label, _empty_record(label))
                grouped_result = {"is_white": is_white, **grouped}
                apply_result(grouped_result, row["result"])
                grouped.update({key: value for key, value in grouped_result.items() if key != "is_white"})

    for record in stats.values():
        _finish_record(record)
    for records in opponent_records.values():
        for record in records.values():
            _finish_record(record)
    for groups in (country_records, club_records):
        for record in groups.values():
            _finish_record(record)

    category_config = _load_category_config(conn)
    player_rows = []
    for player in players:
        player_id = player["id"]
        if stats[player_id]["games"] == 0:
            continue
        row = {"player_id": player_id, **stats[player_id]}
        row.update(_rating_change(conn, player, start_date, end_date, category_config))
        row["opponents"] = sorted(
            ({"player_id": opponent_id, **record} for opponent_id, record in opponent_records[player_id].items()),
            key=lambda item: (-item["games"], item["display_name"].casefold()),
        )
        player_rows.append(row)

    player_rows.sort(key=lambda row: (-row["games"], -(row["win_percentage"] or 0), -row["wins"], row["display_name"].casefold()))
    for rank, row in enumerate(player_rows, 1):
        row["rank"] = rank
    selector_players = sorted(
        player_rows,
        key=lambda row: (-row["games"], row["display_name"].casefold()),
    )

    selected_player_id = int(selected_player_id) if selected_player_id not in (None, "") else None
    selected_player = next((row for row in player_rows if row["player_id"] == selected_player_id), None)
    visible_player_rows = [selected_player] if selected_player is not None else player_rows
    summary["players"] = len(player_rows)
    if selected_player is not None:
        summary["players"] = 1
        summary["games"] = selected_player["games"]
        summary.update(
            {
                "wins": selected_player["wins"],
                "losses": selected_player["losses"],
                "draws": selected_player["draws"],
                "win_percentage": selected_player["win_percentage"],
            }
        )
    return {
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "summary": summary,
        "players": visible_player_rows,
        "selector_players": selector_players,
        "opponents": selected_player["opponents"] if selected_player else [],
        "countries": sorted(country_records.values(), key=lambda item: (-item["games"], item["display_name"].casefold())),
        "clubs": sorted(club_records.values(), key=lambda item: (-item["games"], item["display_name"].casefold())),
        "selected_player_id": selected_player_id,
        "excluded_games": excluded_games,
    }


def export_report_csv(report):
    output = StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["report_start_date", report["start_date"] or "", "report_end_date", report["end_date"] or ""])
    summary = report["summary"]
    writer.writerow(["summary", "games", summary["games"], "players", summary["players"], "wins", summary.get("wins", ""), "losses", summary.get("losses", ""), "draws", summary.get("draws", ""), "win_percentage", summary.get("win_percentage", "") if summary.get("win_percentage") is not None else ""])
    writer.writerow([])
    writer.writerow(["rank", "player", "games", "wins", "losses", "draws", "win_percentage", "rating_change_points", "rating_change_percentage", "category_change", "start_category", "end_category"])
    for row in report["players"]:
        writer.writerow([row["rank"], row["display_name"], row["games"], row["wins"], row["losses"], row["draws"], row["win_percentage"] if row["win_percentage"] is not None else "", row["rating_change_points"] if row["rating_change_points"] is not None else "", row["rating_change_percentage"] if row["rating_change_percentage"] is not None else "", row["category_change"] if row["category_change"] is not None else "", row["start_category"] or "", row["end_category"] or ""])
    writer.writerow([])
    writer.writerow(["countries"])
    writer.writerow(["country", "games", "wins", "losses", "draws", "win_percentage"])
    for row in report["countries"]:
        writer.writerow([row["display_name"], row["games"], row["wins"], row["losses"], row["draws"], row["win_percentage"] if row["win_percentage"] is not None else ""])
    writer.writerow([])
    writer.writerow(["clubs"])
    writer.writerow(["club", "games", "wins", "losses", "draws", "win_percentage"])
    for row in report["clubs"]:
        writer.writerow([row["display_name"], row["games"], row["wins"], row["losses"], row["draws"], row["win_percentage"] if row["win_percentage"] is not None else ""])
    if report["selected_player_id"] is not None:
        writer.writerow([])
        writer.writerow(["opponents", report["selected_player_id"]])
        writer.writerow(["opponent", "games", "wins", "losses", "draws", "win_percentage"])
        for row in report["opponents"]:
            writer.writerow([row["display_name"], row["games"], row["wins"], row["losses"], row["draws"], row["win_percentage"] if row["win_percentage"] is not None else ""])
    return output.getvalue()


def export_report_pdf(report, translations=None, period_label=None):
    translations = translations or {}
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    title = translations.get("reports_title", "Reports")
    period = period_label or report["start_date"] or translations.get("all_time", "All time")
    if period_label is None and report["end_date"]:
        period = f"{period} - {report['end_date']}"
    summary = report["summary"]
    labels = {
        "games": translations.get("games", "Games"),
        "players": translations.get("players", "Players"),
        "wins": translations.get("wins", "Wins"),
        "losses": translations.get("losses", "Losses"),
        "draws": translations.get("draws", "Draws"),
        "win_percentage": translations.get("win_pct", "Win percentage"),
        "rank": translations.get("position", "Rank"),
        "player": translations.get("player", "Player"),
        "opponent": translations.get("opponent", "Opponent"),
    }
    story = [
        Paragraph(title, _pdf_text_style(styles["Title"])),
        Paragraph(period, _pdf_text_style(styles["Normal"])),
        Spacer(1, 6 * mm),
        _pdf_table(
            [[labels["games"], labels["players"], labels["wins"], labels["losses"], labels["draws"], labels["win_percentage"]], [
                summary["games"], summary["players"], summary.get("wins", ""),
                summary.get("losses", ""), summary.get("draws", ""),
                summary.get("win_percentage", ""),
            ]
            ]
        ),
        Spacer(1, 6 * mm),
        Paragraph(translations.get("report_players", "Player performance"), _pdf_text_style(styles["Heading2"])),
        _pdf_table(
            [[labels["rank"], labels["player"], labels["games"], labels["wins"], labels["losses"], labels["draws"], labels["win_percentage"]]]
            + [[row["rank"], row["display_name"], row["games"], row["wins"], row["losses"], row["draws"], row["win_percentage"]] for row in report["players"]]
        ),
    ]
    if report["selected_player_id"] is not None:
        story.extend(
            [
                Spacer(1, 6 * mm),
                Paragraph(translations.get("opponent_records", "Results vs opponents"), _pdf_text_style(styles["Heading2"])),
                _pdf_table(
                    [[labels["opponent"], labels["games"], labels["wins"], labels["losses"], labels["draws"], labels["win_percentage"]]]
                    + [[row["display_name"], row["games"], row["wins"], row["losses"], row["draws"], row["win_percentage"]] for row in report["opponents"]]
                ),
            ]
        )
    document.build(story)
    return output.getvalue()


def _pdf_text_style(style):
    style.alignment = 1
    return style


def _pdf_table(rows):
    table = Table(rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e5f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b8c4c8")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table
