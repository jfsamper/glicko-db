# services/helpers.py
"""Service for providing utility functions and helpers for tournament data processing."""
from datetime import datetime 
import hashlib
import re

from config import SKIP_SHEETS

def normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text

def normalize_key(value):
    text = normalize_text(value).lower()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"[^a-z0-9]+", "", text)

def header_index(headers, candidates):
    """Return the index for a canonical header match.

    Exact matches win. If multiple headers share the same substring-based hit,
    the helper intentionally rejects the result instead of guessing.
    """
    exact_matches = []
    substring_matches = []
    normalized_candidates = []

    for candidate in candidates:
        candidate_key = normalize_key(candidate)
        if candidate_key:
            normalized_candidates.append((candidate, candidate_key))

    for index, header in enumerate(headers):
        if header is None:
            continue
        key = normalize_key(header)
        if not key:
            continue

        for _, candidate_key in normalized_candidates:
            if key == candidate_key:
                exact_matches.append(index)
                break

            if candidate_key in key or key in candidate_key:
                substring_matches.append((index, candidate_key))
                break

    if exact_matches:
        return exact_matches[0]

    if not substring_matches:
        return None

    match_indexes = {index for index, _ in substring_matches}
    if len(match_indexes) > 1:
        return None

    return next(iter(match_indexes))

def parse_date_value(value, date_format=None):
    if value is None or value == "":
        raise ValueError("Date value is required")
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = normalize_text(value)
    if not text:
        raise ValueError("Date value is required")

    if date_format is None and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", text):
        slash_dates = []
        year_digits = len(text.split("/")[-1])
        candidate_formats = ["%d/%m/%Y", "%m/%d/%Y"] if year_digits == 4 else ["%d/%m/%y", "%m/%d/%y"]
        for fmt in candidate_formats:
            try:
                slash_dates.append(datetime.strptime(text, fmt).date().isoformat())
            except ValueError:
                continue
        if len(set(slash_dates)) > 1:
            raise ValueError(
                f"Ambiguous date value: {value!r}; provide date_format"
            )
        if slash_dates:
            return slash_dates[0]

    formats = (date_format,) if date_format else (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%d/%m/%y",
        "%m/%d/%y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date value: {value!r}")

def normalize_round_note(value):
    """Normalize match round metadata to an integer round number.

    Supported inputs include explicit round labels ("Round 2", "Ronda 2"),
    plain numeric values ("2"), and time-style values that encode the round in
    local tournament notation ("14:00:00", "2 p.m."). Unknown values are
    treated as round 0 so ordering and display remain deterministic.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return int(value) if value == int(value) else 0

    text = normalize_text(value)
    if not text:
        return 0

    lower = text.lower()

    time_match = re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text)
    if time_match:
        hour = int(text.split(":", 1)[0])
        if hour == 0:
            return 0
        return hour % 12 or 12

    am_pm_match = re.fullmatch(r"\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)", lower)
    if am_pm_match:
        return int(re.search(r"\d{1,2}", text).group())

    match = re.search(r"(?:round|ronda|turno|rounds)\s*[:\-]?\s*(\d{1,3})", lower)
    if match:
        return int(match.group(1))

    match = re.search(r"(\d{1,3})\s*(?:a\.?m\.?|p\.?m\.?)", lower)
    if match:
        return int(match.group(1))

    if re.fullmatch(r"\d{1,3}", text):
        return int(text)

    return 0


def normalize_round_note_sql(value):
    """SQLite-friendly wrapper for round normalization while preserving raw note text in display code."""
    return normalize_round_note(value)


def normalize_round_note_for_storage(value):
    """Store canonical integer round values when possible, otherwise preserve the raw text for display."""
    if value is None:
        return ""

    text = normalize_text(value)
    if not text:
        return ""

    normalized = normalize_round_note(value)
    if normalized == 0 and not re.fullmatch(r"\d{1,3}", text):
        return text
    if normalized == 0 and re.fullmatch(r"\d{1,3}", text):
        return 0
    return normalized


def sort_match_rows(rows):
    """Order rows by date descending and round number ascending while preserving raw note text for display."""
    def _match_date(row):
        if isinstance(row, dict):
            return row.get("match_date") or ""
        return getattr(row, "match_date", "") or ""

    def _notes_value(row):
        if isinstance(row, dict):
            return row.get("notes")
        return getattr(row, "notes", None)

    ordered = sorted(
        list(rows),
        key=lambda row: normalize_round_note(_notes_value(row)),
    )
    return sorted(ordered, key=lambda row: _match_date(row), reverse=True)


def looks_like_player_name(value):
    text = normalize_text(value)
    if not text:
        return False
    lowered = text.lower()
    if lowered in {"hora/ronda", "hora", "ronda", "fecha", "fecha/hora", "time", "round", "resultado", "resultado/ronda", "score"}:
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        return False
    if re.fullmatch(r"\d{1,2}[-:/]\d{1,2}(?:[-:/]\d{1,2})?", text):
        return False
    if re.fullmatch(r"[\d:\-/\.]+", text):
        return False
    if len(text) < 2:
        return False
    if re.search(r"[a-záéíóúñ]", text, re.IGNORECASE):
        return True
    return False

def should_skip_sheet(sheet_name):
    return normalize_key(sheet_name) in {
        normalize_key(x)
        for x in SKIP_SHEETS
    }

def slugify(value):
    raw = str(value or "")
    sanitized = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if sanitized:
        return sanitized

    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"player-{digest}"

def split_name(display_name):
    text = normalize_text(display_name)
    if "," in text:
        last_name, first_name = text.split(",", 1)
        return first_name.strip(), last_name.strip()
    parts = text.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return text, ""