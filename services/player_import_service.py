"""Validation and persistence for simple CSV and XLSX player imports."""
import csv
import math
from pathlib import Path
import re
import zipfile

import openpyxl

from config import DEFAULT_RD, DEFAULT_VOLATILITY
from services.category_service import get_category_config
from services.country_service import normalize_country_code
from services.display_name import (
    read_display_name_setting,
    render_display_name,
    split_display_name,
)
from services.helpers import normalize_key, normalize_text, slugify
from services.import_safety import read_limited_rows, validate_xlsx_archive
from services.pairing_service import parse_rank_category

MAX_PLAYER_IMPORT_ROWS = 1000
RANK_PATTERN = re.compile(r"^(\d+)\s*([dk])$", re.IGNORECASE)
REQUIRED_HEADERS = ("name", "rank", "country")


def _read_rows(file_path):
    extension = Path(file_path).suffix.lower()
    if extension == ".csv":
        try:
            with Path(file_path).open(
                "r", newline="", encoding="utf-8-sig"
            ) as csv_file:
                return read_limited_rows(
                    csv.reader(csv_file), max_rows=MAX_PLAYER_IMPORT_ROWS + 1
                )
        except ValueError as exc:
            if str(exc) == "import_too_many_rows":
                raise ValueError("player_import_too_many_rows") from exc
            raise
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise ValueError("player_import_read_error") from exc

    if extension == ".xlsx":
        try:
            validate_xlsx_archive(file_path)
            workbook = openpyxl.load_workbook(
                file_path, read_only=True, data_only=True)
        except (
            OSError,
            ValueError,
            EOFError,
            AttributeError,
            KeyError,
            zipfile.BadZipFile,
            openpyxl.utils.exceptions.InvalidFileException,
        ) as exc:
            raise ValueError("player_import_read_error") from exc
        try:
            return read_limited_rows(
                workbook.active.iter_rows(values_only=True),
                max_rows=MAX_PLAYER_IMPORT_ROWS + 1,
            )
        except ValueError as exc:
            if str(exc) in {
                "import_too_many_rows",
                "import_uncompressed_too_large",
            }:
                raise ValueError(
                    "player_import_too_many_rows"
                    if str(exc) == "import_too_many_rows"
                    else "import_uncompressed_too_large"
                ) from exc
            raise ValueError("player_import_read_error") from exc
        except (
            OSError,
            EOFError,
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            zipfile.BadZipFile,
            openpyxl.utils.exceptions.InvalidFileException,
        ) as exc:
            raise ValueError("player_import_read_error") from exc
        finally:
            workbook.close()

    raise ValueError("player_import_file_type")


def _rank_value(value):
    text = str(value or "").strip()
    short_rank = RANK_PATTERN.fullmatch(text)
    if short_rank:
        number, suffix = short_rank.groups()
        text = f"{number} {'dan' if suffix.lower() == 'd' else 'kyu'}"
    rank = parse_rank_category(text)
    if not -30 <= rank <= 8:
        raise ValueError("invalid_rank")
    return rank


def preview_player_import(file_path, conn):
    """Validate rows without writing to the database."""
    raw_rows = _read_rows(file_path)
    if not raw_rows:
        raise ValueError("player_import_invalid_headers")

    headers = [
        str(value or "").strip().casefold() if value is not None else ""
        for value in raw_rows[0]
    ]
    while headers and not headers[-1]:
        headers.pop()
    if len(headers) != len(set(headers)) or not set(REQUIRED_HEADERS).issubset(headers):
        raise ValueError("player_import_invalid_headers")
    indexes = {name: headers.index(name) for name in REQUIRED_HEADERS}

    data_rows = [
        (line_number, row)
        for line_number, row in enumerate(raw_rows[1:], start=2)
        if any(value is not None and str(value).strip() for value in row)
    ]
    if len(data_rows) > MAX_PLAYER_IMPORT_ROWS:
        raise ValueError("player_import_too_many_rows")
    if not data_rows:
        raise ValueError("player_import_no_rows")

    category_config = get_category_config(conn)
    display_format, display_template = read_display_name_setting(conn)
    existing_names = {
        normalize_key(row["display_name"])
        for row in conn.execute(
            "SELECT display_name FROM players_display"
        ).fetchall()
        if row["display_name"]
    }
    seen_names = set()
    rows = []
    for line_number, values in data_rows:
        row = {
            "line_number": line_number,
            "name": "",
            "rank": "",
            "country": "",
            "errors": [],
        }
        try:
            row["name"] = normalize_text(
                values[indexes["name"]] if indexes["name"] < len(values) else ""
            )
            row["rank"] = str(
                values[indexes["rank"]] if indexes["rank"] < len(values) else ""
            ).strip()
            row["country"] = str(
                values[indexes["country"]]
                if indexes["country"] < len(values) and values[indexes["country"]] is not None
                else ""
            ).strip()

            if not row["name"]:
                row["errors"].append("player_import_name_required")
            else:
                try:
                    first_name, last_name = split_display_name(row["name"])
                    if not first_name and not last_name:
                        raise ValueError
                except ValueError:
                    row["errors"].append("player_import_invalid_name")
                    first_name = last_name = ""

            if not row["rank"]:
                row["errors"].append("player_import_invalid_rank")
                rank_value = None
            else:
                try:
                    rank_value = _rank_value(row["rank"])
                except ValueError:
                    row["errors"].append("player_import_invalid_rank")
                    rank_value = None

            if row["country"]:
                try:
                    row["country"] = normalize_country_code(row["country"])
                except ValueError:
                    row["errors"].append("player_import_invalid_country")
            else:
                row["country"] = None

            if row["name"] and first_name + last_name:
                canonical_name = render_display_name(
                    first_name, last_name, display_format, display_template
                )
                name_key = normalize_key(canonical_name)
                if name_key in existing_names:
                    row["errors"].append("player_import_existing_player")
                elif name_key in seen_names:
                    row["errors"].append("player_import_duplicate_row")
                seen_names.add(name_key)

            if rank_value is not None:
                try:
                    row["rating"] = category_config["glicko_m"] * math.exp(
                        (rank_value + 29) / category_config["glicko_k"]
                    )
                except OverflowError:
                    row["errors"].append("player_import_invalid_rank")
        except (IndexError, TypeError):
            row["errors"].append("player_import_invalid_row")
        rows.append(row)

    return {
        "rows": rows,
        "valid_count": sum(not row["errors"] for row in rows),
        "error_count": sum(bool(row["errors"]) for row in rows),
    }


def insert_imported_players(conn, rows):
    """Insert all validated import rows. The caller owns the transaction."""
    used_slugs = {
        row["slug"]
        for row in conn.execute("SELECT slug FROM players WHERE slug IS NOT NULL").fetchall()
    }
    display_format, display_template = read_display_name_setting(conn)
    inserted_ids = []
    for row in rows:
        if row["errors"]:
            raise ValueError("Cannot insert invalid player import rows")
        first_name, last_name = split_display_name(row["name"])
        display_name = render_display_name(
            first_name, last_name, display_format, display_template
        )
        base_slug = slugify(display_name)
        slug = base_slug
        suffix = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slugs.add(slug)
        conn.execute(
            """
            INSERT INTO players (
                first_name, last_name, country, club, slug, active,
                rating, initial_rating, rd, volatility
            ) VALUES (?, ?, ?, '', ?, 1, ?, ?, ?, ?)
            """,
            (
                first_name,
                last_name,
                row["country"],
                slug,
                row["rating"],
                row["rating"],
                DEFAULT_RD,
                DEFAULT_VOLATILITY,
            ),
        )
        inserted_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return inserted_ids
