"""Administrative workbook, CSV, and OpenGotha import routes."""
import csv
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from services.i18n import TRANSLATIONS

WORKBOOK_PREVIEW_SESSION_KEY = "workbook_import_preview"
WORKBOOK_PREVIEW_TTL_SECONDS = 30 * 60
WORKBOOK_PREVIEW_FILENAME_RE = re.compile(
    r"^workbook-([a-f0-9]{32})\.(xlsx|xls)$"
)


def _render_import_page(
    admin,
    lang,
    preview=None,
    preview_file=None,
    workbook_preview=None,
    workbook_preview_token=None,
    workbook_preview_error=None,
):
    return render_template(
        "admin/import.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        preview=preview,
        preview_file=preview_file,
        workbook_preview=workbook_preview,
        workbook_preview_token=workbook_preview_token,
        workbook_preview_error=workbook_preview_error,
        acceleration_scheme_options=admin.ACCELERATION_SCHEMES,
        acceleration_scheme_choice=admin.acceleration_scheme_choice(
            preview["metadata"].get("acceleration_scheme") if preview else None
        ),
    )


def _discard_workbook_preview(admin):
    state = session.pop(WORKBOOK_PREVIEW_SESSION_KEY, None)
    if not isinstance(state, dict):
        return
    filename = state.get("filename", "")
    if WORKBOOK_PREVIEW_FILENAME_RE.fullmatch(filename):
        upload_path = Path(admin.BASE_DIR) / "uploads" / filename
        upload_path.unlink(missing_ok=True)


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as uploaded_file:
        for chunk in iter(lambda: uploaded_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get_workbook_preview(admin, token):
    state = session.get(WORKBOOK_PREVIEW_SESSION_KEY)
    if not isinstance(state, dict):
        raise ValueError("Workbook preview is missing or expired")
    if (
        not isinstance(token, str)
        or token != state.get("token")
        or state.get("user_id") != session.get("user_id")
        or time.time() - state.get("created_at", 0) > WORKBOOK_PREVIEW_TTL_SECONDS
    ):
        _discard_workbook_preview(admin)
        raise ValueError("Workbook preview is missing or expired")

    filename = state.get("filename", "")
    match = WORKBOOK_PREVIEW_FILENAME_RE.fullmatch(filename)
    if match is None or match.group(1) != token:
        _discard_workbook_preview(admin)
        raise ValueError("Workbook preview is invalid")

    upload_path = Path(admin.BASE_DIR) / "uploads" / filename
    if not upload_path.is_file():
        _discard_workbook_preview(admin)
        raise ValueError("Workbook preview file is missing")
    digest = _file_sha256(upload_path)
    if digest != state.get("sha256"):
        _discard_workbook_preview(admin)
        raise ValueError("Workbook preview file changed after review")
    return state, upload_path


def _create_verified_import_backup(admin):
    timestamp = admin.current_datetime().strftime("%Y-%m-%d-%H%M%S")
    filename = f"{timestamp}-pre-xlsx-import-{uuid.uuid4().hex[:8]}.db"
    admin.create_checked_backup(filename)
    return filename


def _admin_routes():
    from routes import admin

    return admin


def register_import_routes(admin_bp):
    admin_bp.add_url_rule(
        "/import",
        endpoint="import_matches",
        view_func=import_matches,
        methods=["GET"],
    )
    admin_bp.add_url_rule(
        "/admin/import",
        endpoint="admin_import",
        view_func=admin_import,
        methods=["GET", "POST"],
    )


def import_matches():
    admin = _admin_routes()
    lang = admin.get_language(request.args.get("lang"))
    return redirect(url_for("admin_import", lang=lang))


def admin_import():
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    preview = None
    preview_file = request.form.get(
        "preview_file") or request.args.get("preview_file")

    if request.method == "POST":
        action = request.form.get("action")
        file = request.files.get("file")

        if action == "cancel_workbook_preview":
            try:
                _get_workbook_preview(admin, request.form.get("preview_token"))
            except ValueError:
                pass
            _discard_workbook_preview(admin)
            return redirect(url_for("admin_import", lang=lang))

        if action == "commit_workbook":
            try:
                state, upload_path = _get_workbook_preview(
                    admin, request.form.get("preview_token")
                )
                workbook_preview = admin.preview_workbook_data(upload_path)
            except Exception as exc:
                flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
                return redirect(url_for("admin_import", lang=lang))

            if request.form.get("confirm_replacement") != "yes":
                return _render_import_page(
                    admin,
                    lang,
                    workbook_preview=workbook_preview,
                    workbook_preview_token=state["token"],
                    workbook_preview_error=TRANSLATIONS[lang][
                        "workbook_replacement_confirmation_required"
                    ],
                )

            try:
                backup_name = _create_verified_import_backup(admin)
                stats = admin.import_workbook_data(upload_path, reset=True)
            except Exception as exc:
                return _render_import_page(
                    admin,
                    lang,
                    workbook_preview=workbook_preview,
                    workbook_preview_token=state["token"],
                    workbook_preview_error=f"{TRANSLATIONS[lang]['error']}: {exc}",
                )

            _discard_workbook_preview(admin)
            try:
                admin.log_admin_action(
                    "workbook_replaced",
                    "database",
                    {
                        "backup": backup_name,
                        "players": stats["players"],
                        "matches": stats["matches"],
                    },
                    user_id=session.get("user_id"),
                )
                admin.run_post_import_replay()
            except Exception as exc:
                flash(
                    TRANSLATIONS[lang]["workbook_replacement_postprocess_warning"].format(
                        backup=backup_name,
                        error=exc,
                    )
                )

            flash(
                TRANSLATIONS[lang]["workbook_replacement_completed"].format(
                    players=stats["players"],
                    matches=stats["matches"],
                    backup=backup_name,
                )
            )
            return redirect(url_for("import_matches", lang=lang))

        if action == "commit_preview" and preview_file:
            upload_path = os.path.join(admin.BASE_DIR, "uploads", preview_file)
            try:
                player_decisions = {
                    key.removeprefix("player_decision_"): value
                    for key, value in request.form.items()
                    if key.startswith("player_decision_")
                }
                metadata_overrides = {
                    "name": (request.form.get("metadata_name") or "").strip(),
                    "description": (request.form.get("metadata_description") or "").strip(),
                    "short_name": (request.form.get("metadata_short_name") or "").strip(),
                    "location": (request.form.get("metadata_location") or "").strip(),
                    "begin_date": (request.form.get("metadata_begin_date") or "").strip(),
                    "end_date": (request.form.get("metadata_end_date") or "").strip(),
                    "rounds": request.form.get("metadata_rounds", type=int),
                    "pairing_system": admin.normalize_tournament_system(
                        request.form.get("metadata_tournament_type")
                        or request.form.get("metadata_pairing_system")
                    ),
                }
                if metadata_overrides["pairing_system"] == "accelerated_swiss":
                    metadata_overrides["acceleration_scheme"] = admin.acceleration_scheme_from_form(
                        request.form)
                    metadata_rounds = request.form.get(
                        "metadata_rounds", type=int) or 1
                    metadata_overrides["acceleration_rounds"] = request.form.get(
                        "metadata_acceleration_rounds",
                        admin.default_acceleration_rounds(metadata_rounds),
                        type=int,
                    )
                if metadata_overrides["pairing_system"] == "swiss_cat":
                    metadata_overrides["category_rounds"] = request.form.get(
                        "metadata_category_rounds", admin.DEFAULT_CATEGORY_ROUNDS, type=int
                    )
                if metadata_overrides["pairing_system"] == "mcmahon":
                    metadata_overrides["mm_bar"], metadata_overrides["mm_floor"], metadata_overrides["mm_zero"] = admin.validate_mcmahon_settings(
                        request.form.get("metadata_mm_bar"),
                        request.form.get("metadata_mm_floor"),
                        request.form.get("metadata_mm_zero"),
                    )
                metadata_overrides = {
                    key: value for key, value in metadata_overrides.items() if value not in (None, "")
                }
                if "metadata_description" in request.form:
                    metadata_overrides["description"] = (
                        request.form.get("metadata_description") or "").strip()
                conn = admin.get_db()
                try:
                    if request.form.get("metadata_decision") == "reject":
                        raise ValueError(
                            "Import rejected during metadata review")
                    tournament_id, metadata, matched = admin.create_tournament_from_gotha(
                        conn,
                        upload_path,
                        player_decisions=player_decisions,
                        metadata_overrides=metadata_overrides,
                    )
                    conn.commit()
                finally:
                    conn.close()
                flash(f"{TRANSLATIONS[lang]['success']} ({matched} players)")
                return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
            except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
                return redirect(url_for("admin_import", lang=lang))

        if not file or file.filename == "":
            flash(TRANSLATIONS[lang]["no_file"])
            return redirect(url_for("import_matches", lang=lang))

        filename = secure_filename(file.filename or "")
        extension = Path(filename).suffix.lower()
        workbook_preview_token = None
        if extension in (".xlsx", ".xls"):
            _discard_workbook_preview(admin)
            workbook_preview_token = uuid.uuid4().hex
            filename = f"workbook-{workbook_preview_token}{extension}"
        upload_path = os.path.join(admin.BASE_DIR, "uploads", filename)
        os.makedirs(os.path.dirname(upload_path), exist_ok=True)
        file.save(upload_path)

        try:
            if extension in (".xlsx", ".xls"):
                workbook_preview = admin.preview_workbook_data(upload_path)
                session[WORKBOOK_PREVIEW_SESSION_KEY] = {
                    "token": workbook_preview_token,
                    "filename": filename,
                    "sha256": _file_sha256(upload_path),
                    "created_at": time.time(),
                    "user_id": session.get("user_id"),
                }
                return _render_import_page(
                    admin,
                    lang,
                    workbook_preview=workbook_preview,
                    workbook_preview_token=workbook_preview_token,
                )

            if extension == ".xml":
                conn = admin.get_db()
                try:
                    preview = admin.build_import_preview(conn, upload_path)
                finally:
                    conn.close()
                return _render_import_page(
                    admin, lang, preview=preview, preview_file=filename
                )

            if extension == ".csv":
                with open(upload_path, newline="", encoding="utf-8-sig") as csv_file:
                    reader = csv.DictReader(csv_file)
                    required_columns = {"date", "white", "black", "result"}
                    headers = [
                        (column or "").strip().casefold()
                        for column in reader.fieldnames or []
                    ]
                    if len(headers) != len(set(headers)):
                        raise ValueError(TRANSLATIONS[lang]["duplicate_csv_columns"])
                    reader.fieldnames = headers
                    columns = set(headers)
                    if not required_columns.issubset(columns):
                        raise ValueError(
                            TRANSLATIONS[lang]["required_columns_missing"])

                    conn = admin.get_db()
                    try:
                        players = conn.execute(
                            "SELECT id, display_name FROM players_display").fetchall()
                        player_lookup = {
                            admin.normalize_key(row["display_name"]): row["id"] for row in players
                        }
                        imported_matches = 0
                        earliest_match_date = None
                        for row in reader:
                            white_id = player_lookup.get(
                                admin.normalize_key(str(row.get("white", "")).strip()))
                            black_id = player_lookup.get(
                                admin.normalize_key(str(row.get("black", "")).strip()))
                            if white_id is None or black_id is None:
                                continue

                            match_date = admin.parse_date_value(
                                row.get("date", ""))
                            handicap_stones = admin.parse_handicap_stones(
                                row.get("handicap"))
                            conn.execute(
                                """
                                INSERT INTO matches
                                    (match_date, white_player_id, black_player_id, result, event,
                                     notes, round_number, handicap_stones)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    match_date,
                                    white_id,
                                    black_id,
                                    row.get("result", ""),
                                    "Imported",
                                    admin.normalize_round_note_for_storage(
                                        row.get("notes", row.get("round", ""))
                                    ),
                                    admin.normalize_round_note(
                                        row.get("notes", row.get("round", ""))),
                                    handicap_stones,
                                ),
                            )
                            imported_matches += 1
                            if earliest_match_date is None or match_date < earliest_match_date:
                                earliest_match_date = match_date
                        conn.commit()
                    finally:
                        conn.close()

                if earliest_match_date:
                    admin.mark_dirty(earliest_match_date)
                    admin.run_post_import_replay()
                else:
                    admin.refresh_stats()
                flash(
                    f"{TRANSLATIONS[lang]['success']} ({imported_matches} matches)")
                return redirect(url_for("import_matches", lang=lang))

            raise ValueError(TRANSLATIONS[lang]["unsupported_file_format"])
        except Exception as exc:
            if extension in (".xlsx", ".xls"):
                Path(upload_path).unlink(missing_ok=True)
            flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
            return redirect(url_for("import_matches", lang=lang))

    workbook_preview = None
    workbook_preview_token = None
    if request.method == "GET" and session.get(WORKBOOK_PREVIEW_SESSION_KEY):
        try:
            state, upload_path = _get_workbook_preview(
                admin, session[WORKBOOK_PREVIEW_SESSION_KEY].get("token")
            )
            workbook_preview = admin.preview_workbook_data(upload_path)
            workbook_preview_token = state["token"]
        except (OSError, ValueError, sqlite3.DatabaseError):
            _discard_workbook_preview(admin)

    return _render_import_page(
        admin,
        lang,
        preview=preview,
        preview_file=preview_file,
        workbook_preview=workbook_preview,
        workbook_preview_token=workbook_preview_token,
    )
