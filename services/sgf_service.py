"""Secure storage helpers for optional SGF match records."""
from pathlib import Path
from datetime import datetime
import math
import re
import shutil
import uuid

from config import BASE_DIR
from services.category_service import category_value, get_category_config
from werkzeug.utils import secure_filename

SGF_UPLOAD_DIR = Path(BASE_DIR) / "uploads" / "sgf"
MAX_SGF_BYTES = 500 * 1024
SGF_METADATA_KEYS = ("PW", "PB", "WR", "BR", "EV", "DT", "RE", "PC")


def ensure_sgf_schema(conn):
    """Add optional SGF and location columns without changing existing matches."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()
    }
    if not columns:
        return

    if "sgf_filename" not in columns:
        conn.execute("ALTER TABLE matches ADD COLUMN sgf_filename TEXT")
    if "location" not in columns:
        conn.execute("ALTER TABLE matches ADD COLUMN location TEXT")

    # Keep the oldest existing link before enforcing the one-SGF-per-match rule.
    conn.execute(
        """
        UPDATE matches
        SET sgf_filename = NULL
        WHERE sgf_filename IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id)
              FROM matches
              WHERE sgf_filename IS NOT NULL
              GROUP BY sgf_filename
          )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_sgf_filename_unique
        ON matches (sgf_filename)
        WHERE sgf_filename IS NOT NULL
        """
    )
    conn.commit()


def has_sgf_column(conn):
    return "sgf_filename" in {
        row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()
    }


def _safe_sgf_path(filename):
    if not filename or Path(filename).name != filename:
        return None
    if Path(filename).suffix.lower() != ".sgf":
        return None
    root = SGF_UPLOAD_DIR.resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def get_sgf_path(filename):
    path = _safe_sgf_path(filename)
    if path is None or not path.is_file():
        return None
    return path


def _decode_sgf_content(content):
    if len(content) > MAX_SGF_BYTES:
        raise ValueError("SGF file is too large")
    if not content:
        raise ValueError("SGF file is empty")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("SGF file must be UTF-8 text") from exc
    if not text.lstrip().startswith("(;"):
        raise ValueError("Invalid SGF file")
    return text


def _unescape_sgf_value(value):
    result = []
    escaped = False
    for character in str(value or ""):
        if escaped:
            result.append("\n" if character == "n" else character)
            escaped = False
        elif character == "\\":
            escaped = True
        else:
            result.append(character)
    if escaped:
        result.append("\\")
    return "".join(result)


def _sgf_metadata_for_path(path):
    try:
        stat = path.stat()
    except OSError:
        return None

    metadata = {
        "filename": path.name,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        text = path.read_text(encoding="utf-8-sig")
        properties = _root_properties(text[text.find("(;") + 2:]) if "(;" in text else {}
    except (OSError, UnicodeDecodeError):
        properties = {}

    for key in SGF_METADATA_KEYS:
        values = properties.get(key, [])
        metadata[key] = _unescape_sgf_value(values[0]) if values else ""
    return metadata


def get_sgf_metadata(filename):
    """Return file and root-property metadata for an existing SGF file."""
    path = get_sgf_path(filename)
    return _sgf_metadata_for_path(path) if path is not None else None


def list_sgf_files():
    """List every safely stored SGF file, newest first."""
    if not SGF_UPLOAD_DIR.is_dir():
        return []

    records = []
    for path in SGF_UPLOAD_DIR.glob("*.sgf"):
        safe_path = _safe_sgf_path(path.name)
        if safe_path is None or safe_path != path.resolve():
            continue
        metadata = _sgf_metadata_for_path(safe_path)
        if metadata is not None:
            records.append(metadata)
    return sorted(records, key=lambda record: (record["modified_at"], record["filename"]), reverse=True)


def clear_missing_sgf_links(conn):
    """Clear match links whose SGF file is no longer present on disk."""
    if not has_sgf_column(conn):
        return 0

    rows = conn.execute(
        "SELECT id, sgf_filename FROM matches WHERE sgf_filename IS NOT NULL"
    ).fetchall()
    missing_ids = [
        row["id"]
        for row in rows
        if get_sgf_path(row["sgf_filename"]) is None
    ]
    if missing_ids:
        conn.executemany(
            "UPDATE matches SET sgf_filename = NULL WHERE id = ?",
            ((match_id,) for match_id in missing_ids),
        )
        conn.commit()
    return len(missing_ids)


def backup_sgf_files(backup_path):
    """Copy the SGF library beside a database backup."""
    target = Path(backup_path).with_suffix(".sgf")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for record in list_sgf_files():
        source = get_sgf_path(record["filename"])
        if source is not None:
            shutil.copy2(source, target / source.name)
    return target


def restore_sgf_files(backup_path):
    """Restore SGFs from a backup sidecar without removing newer library files."""
    source_dir = Path(backup_path).with_suffix(".sgf")
    if not source_dir.is_dir():
        return False

    SGF_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    restored = False
    for source in source_dir.glob("*.sgf"):
        if _safe_sgf_path(source.name) is None:
            continue
        shutil.copy2(source, SGF_UPLOAD_DIR / source.name)
        restored = True
    return restored


def _escape_sgf_value(value):
    return str(value or "").replace("\\", "\\\\").replace("]", "\\]").replace("\n", "\\n")


def _root_properties(root_body):
    properties = {}
    for match in re.finditer(r"([A-Z]{1,3})((?:\[(?:\\.|[^]])*\])+)", root_body):
        properties.setdefault(match.group(1), []).extend(
            re.findall(r"\[((?:\\.|[^]])*)\]", match.group(2))
        )
    return properties


def _result_kind(value):
    normalized = str(value or "").strip().upper()
    if normalized.startswith("W+"):
        return "white"
    if normalized.startswith("B+"):
        return "black"
    if normalized in {"0", "JIGO", "DRAW"}:
        return "draw"
    return None


def rewrite_sgf_root(text, metadata):
    start = text.find("(;" )
    if start < 0:
        raise ValueError("Invalid SGF file")
    root_start = start + 2
    index = root_start
    in_value = False
    escaped = False
    while index < len(text):
        character = text[index]
        if in_value:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "]":
                in_value = False
        elif character == "[":
            in_value = True
        elif character in ";)":
            break
        index += 1
    if index >= len(text):
        raise ValueError("Invalid SGF root")

    original_root_body = text[root_start:index]
    original_properties = _root_properties(original_root_body)
    replace_keys = {key for key in metadata if metadata[key] not in (None, "")}
    if "RE" in replace_keys and any(
        _result_kind(value) == _result_kind(metadata["RE"])
        for value in original_properties.get("RE", [])
    ):
        replace_keys.remove("RE")
    if "PC" in replace_keys and not metadata["PC"]:
        replace_keys.remove("PC")
    property_pattern = re.compile(
        r"(?:" + "|".join(sorted(replace_keys)) + r")(?:\[(?:\\.|[^]])*\])+"
    ) if replace_keys else None
    root_body = property_pattern.sub("", original_root_body) if property_pattern else original_root_body
    additions = "".join(
        f"{key}[{_escape_sgf_value(value)}]"
        for key, value in metadata.items()
        if value not in (None, "") and key in replace_keys
    )
    return text[:root_start] + root_body + additions + text[index:]


def _rank_label(rating, config):
    value = math.floor(category_value(rating, k=config["glicko_k"], m=config["glicko_m"]))
    return f"{value + 1}d" if value >= 0 else f"{abs(value)}k"


def match_sgf_metadata(conn, white_player_id, black_player_id, match_date, result, event="", location=None):
    players = conn.execute(
        "SELECT id, display_name, rating FROM players WHERE id IN (?, ?)",
        (white_player_id, black_player_id),
    ).fetchall()
    by_id = {row["id"]: row for row in players}
    white = by_id.get(int(white_player_id))
    black = by_id.get(int(black_player_id))
    if white is None or black is None:
        raise ValueError("Players not found for SGF metadata")
    result_value = {"1-0": "W+R", "0-1": "B+R", "1/2-1/2": "0"}.get(result, result)
    category_config = get_category_config(conn=conn)
    return {
        "PW": white["display_name"],
        "PB": black["display_name"],
        "WR": _rank_label(white["rating"], category_config),
        "BR": _rank_label(black["rating"], category_config),
        "PC": location or event,
        "EV": event,
        "DT": match_date,
        "RE": result_value,
    }


def save_sgf_upload(file_storage, metadata=None):
    original_name = secure_filename(file_storage.filename or "")
    if not original_name or Path(original_name).suffix.lower() != ".sgf":
        raise ValueError("Only .sgf files are supported")

    content = file_storage.read(MAX_SGF_BYTES + 1)
    text = _decode_sgf_content(content)
    if metadata:
        text = rewrite_sgf_root(text, metadata)

    SGF_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.sgf"
    path = _safe_sgf_path(stored_name)
    if path is None:
        raise ValueError("Invalid SGF storage path")
    path.write_text(text, encoding="utf-8")
    return stored_name


def update_sgf_metadata(filename, metadata):
    path = get_sgf_path(filename)
    if path is None:
        raise ValueError("Invalid SGF file")
    text = _decode_sgf_content(path.read_bytes())
    path.write_text(rewrite_sgf_root(text, metadata), encoding="utf-8")


def delete_sgf_file(filename):
    path = _safe_sgf_path(filename)
    if path is not None and path.is_file():
        path.unlink()
