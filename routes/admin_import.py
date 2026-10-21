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
from services.import_safety import (
    MAX_IMPORT_ROWS,
    save_upload_bounded,
    validate_xml_limits,
)

WORKBOOK_PREVIEW_SESSION_KEY = "workbook_import_preview"
WORKBOOK_PREVIEW_TTL_SECONDS = 30 * 60
WORKBOOK_PREVIEW_FILENAME_RE = re.compile(
    r"^workbook-([a-f0-9]{32})\.(xlsx|xls)$"
)
XML_PREVIEW_FILENAME_RE = re.compile(r"^import-([a-f0-9]{32})\.xml$")


def _render_import_page(
    admin,
    lang,
    preview=None,
    preview_file=None,
    workbook_preview=None,
    workbook_preview_token=None,
    workbook_preview_error=None,
    import_diagnostics=None,
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
        import_diagnostics=import_diagnostics,
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


def _validate_match_csv(path, admin, lang):
    required_columns = {"date", "white", "black", "result"}
    diagnostics = {"row_count": 0, "error_count": 0, "issues": []}
    records = []

    def add_issue(row_number, field, message):
        diagnostics["error_count"] += 1
        if len(diagnostics["issues"]) < 100:
            diagnostics["issues"].append(
                {"row": row_number, "field": field, "message": message}
            )

    try:
        with open(path, newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            headers = [
                (column or "").strip().casefold()
                for column in reader.fieldnames or []
            ]
            if len(headers) != len(set(headers)):
                raise ValueError(TRANSLATIONS[lang]["duplicate_csv_columns"])
            if not required_columns.issubset(headers):
                raise ValueError(TRANSLATIONS[lang]["required_columns_missing"])
            reader.fieldnames = headers

            conn = admin.get_db()
            try:
                players = conn.execute(
                    "SELECT id, display_name FROM players_display"
                ).fetchall()
            finally:
                conn.close()
            player_lookup = {
                admin.normalize_key(row["display_name"]): row["id"]
                for row in players
            }

            for row in reader:
                row_number = reader.line_num
                diagnostics["row_count"] += 1
                if diagnostics["row_count"] > MAX_IMPORT_ROWS:
                    raise ValueError("import_too_many_rows")
                row_error_count = diagnostics["error_count"]
                if None in row:
                    add_issue(
                        row_number, "row",
                        TRANSLATIONS[lang]["import_row_extra_columns"],
                    )

                normalized = {
                    key: (value or "").strip() if value is not None else ""
                    for key, value in row.items()
                    if key is not None
                }
                for field in ("date", "white", "black", "result"):
                    if not normalized.get(field):
                        add_issue(
                            row_number, field,
                            TRANSLATIONS[lang]["import_field_required"],
                        )

                white_id = player_lookup.get(
                    admin.normalize_key(normalized.get("white", ""))
                )
                black_id = player_lookup.get(
                    admin.normalize_key(normalized.get("black", ""))
                )
                if normalized.get("white") and white_id is None:
                    add_issue(
                        row_number, "white",
                        TRANSLATIONS[lang]["import_player_not_found"],
                    )
                if normalized.get("black") and black_id is None:
                    add_issue(
                        row_number, "black",
                        TRANSLATIONS[lang]["import_player_not_found"],
                    )

                match_date = None
                if normalized.get("date"):
                    try:
                        match_date = admin.parse_date_value(normalized["date"])
                    except ValueError:
                        add_issue(
                            row_number, "date",
                            TRANSLATIONS[lang]["invalid_date_format"],
                        )

                result = normalized.get("result", "")
                if result not in {"1-0", "0-1", "1/2-1/2"}:
                    add_issue(
                        row_number, "result",
                        TRANSLATIONS[lang]["import_invalid_result"],
                    )

                try:
                    handicap_stones = admin.parse_handicap_stones(
                        normalized.get("handicap")
                    )
                except ValueError:
                    add_issue(
                        row_number, "handicap",
                        TRANSLATIONS[lang]["import_invalid_handicap"],
                    )
                    handicap_stones = 0

                if diagnostics["error_count"] == row_error_count:
                    notes = normalized.get("notes", normalized.get("round", ""))
                    records.append(
                        {
                            "date": match_date,
                            "white_id": white_id,
                            "black_id": black_id,
                            "result": result,
                            "notes": notes,
                            "handicap": handicap_stones,
                        }
                    )
    except UnicodeDecodeError as exc:
        raise ValueError("import_read_error") from exc
    except csv.Error as exc:
        raise ValueError("import_read_error") from exc

    if diagnostics["row_count"] == 0:
        add_issue(1, "row", TRANSLATIONS[lang]["import_empty_csv"])
    diagnostics["valid_count"] = len(records)
    diagnostics["omitted_issue_count"] = max(
        0, diagnostics["error_count"] - len(diagnostics["issues"])
    )
    return records, diagnostics


def _rating_dirty_state(admin):
    conn = admin.get_db()
    try:
        row = conn.execute(
            "SELECT earliest_dirty_date FROM rating_state WHERE id = 1"
        ).fetchone()
        return row["earliest_dirty_date"] if row else None
    finally:
        conn.close()


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
            replay_deferred = False
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
                replay_deferred = admin.run_post_import_replay() is False
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
            rating_state = _rating_dirty_state(admin)
            flash(
                TRANSLATIONS[lang]["import_recovery_receipt"].format(
                    players=stats["players"],
                    matches=stats["matches"],
                    warnings=workbook_preview.get("warning_count", 0),
                    backup=backup_name,
                    rating_state=rating_state or TRANSLATIONS[lang]["import_ratings_current"],
                )
            )
            if replay_deferred:
                flash(TRANSLATIONS[lang]["rating_replay_deferred"])
            return redirect(url_for("import_matches", lang=lang))

        if action == "commit_preview" and preview_file:
            if not XML_PREVIEW_FILENAME_RE.fullmatch(preview_file):
                flash(f"{TRANSLATIONS[lang]['error']}: Invalid import preview.")
                return redirect(url_for("admin_import", lang=lang))
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
                    preview_summary = admin.build_import_preview(
                        conn, upload_path
                    )["summary"]
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
                rating_state = _rating_dirty_state(admin)
                flash(
                    TRANSLATIONS[lang]["import_recovery_receipt"].format(
                        players=matched,
                        matches=preview_summary["matches_total"],
                        warnings=preview_summary["warnings"],
                        backup=TRANSLATIONS[lang]["import_no_backup_transactional"],
                        rating_state=rating_state or TRANSLATIONS[lang]["import_ratings_current"],
                    )
                )
                return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
            except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
                return redirect(url_for("admin_import", lang=lang))
            finally:
                Path(upload_path).unlink(missing_ok=True)

        if not file or file.filename == "":
            flash(TRANSLATIONS[lang]["no_file"])
            return redirect(url_for("import_matches", lang=lang))

        filename = secure_filename(file.filename or "")
        extension = Path(filename).suffix.lower()
        if extension not in {".csv", ".xlsx", ".xml"}:
            flash(TRANSLATIONS[lang]["unsupported_file_format"])
            return redirect(url_for("import_matches", lang=lang))
        workbook_preview_token = None
        if extension == ".xlsx":
            _discard_workbook_preview(admin)
            workbook_preview_token = uuid.uuid4().hex
            filename = f"workbook-{workbook_preview_token}{extension}"
        elif extension == ".xml":
            filename = f"import-{uuid.uuid4().hex}.xml"
        upload_path = os.path.join(admin.BASE_DIR, "uploads", filename)
        os.makedirs(os.path.dirname(upload_path), exist_ok=True)

        try:
            save_upload_bounded(file, upload_path)
            if extension == ".xlsx":
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
                validate_xml_limits(upload_path)
                conn = admin.get_db()
                try:
                    preview = admin.build_import_preview(conn, upload_path)
                finally:
                    conn.close()
                return _render_import_page(
                    admin, lang, preview=preview, preview_file=filename
                )

            if extension == ".csv":
                records, diagnostics = _validate_match_csv(
                    upload_path, admin, lang
                )
                if diagnostics["error_count"]:
                    Path(upload_path).unlink(missing_ok=True)
                    return _render_import_page(
                        admin, lang, import_diagnostics=diagnostics
                    )

                conn = admin.get_db()
                earliest_match_date = None
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    for row in records:
                        conn.execute(
                            """
                            INSERT INTO matches
                                (match_date, white_player_id, black_player_id, result, event,
                                 notes, round_number, handicap_stones)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row["date"],
                                row["white_id"],
                                row["black_id"],
                                row["result"],
                                "Imported",
                                admin.normalize_round_note_for_storage(row["notes"]),
                                admin.normalize_round_note(row["notes"]),
                                row["handicap"],
                            ),
                        )
                        if earliest_match_date is None or row["date"] < earliest_match_date:
                            earliest_match_date = row["date"]
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

                replay_deferred = False
                postprocess_error = None
                try:
                    if earliest_match_date:
                        admin.mark_dirty(earliest_match_date)
                        replay_deferred = admin.run_post_import_replay() is False
                    else:
                        admin.refresh_stats()
                except Exception as exc:
                    postprocess_error = exc
                flash(
                    f"{TRANSLATIONS[lang]['success']} ({len(records)} matches)")
                rating_state = _rating_dirty_state(admin)
                flash(
                    TRANSLATIONS[lang]["import_recovery_receipt"].format(
                        players=0,
                        matches=len(records),
                        warnings=diagnostics["error_count"] + int(postprocess_error is not None),
                        backup=TRANSLATIONS[lang]["import_no_backup_transactional"],
                        rating_state=rating_state or TRANSLATIONS[lang]["import_ratings_current"],
                    )
                )
                if postprocess_error is not None:
                    flash(
                        TRANSLATIONS[lang]["import_postprocess_warning"].format(
                            error=postprocess_error
                        )
                    )
                if replay_deferred:
                    flash(TRANSLATIONS[lang]["rating_replay_deferred"])
                Path(upload_path).unlink(missing_ok=True)
                return redirect(url_for("import_matches", lang=lang))

            raise ValueError(TRANSLATIONS[lang]["unsupported_file_format"])
        except Exception as exc:
            if not (
                extension == ".xlsx"
                and session.get(WORKBOOK_PREVIEW_SESSION_KEY, {}).get("filename")
                == filename
            ):
                Path(upload_path).unlink(missing_ok=True)
            message = TRANSLATIONS[lang].get(str(exc), str(exc))
            flash(f"{TRANSLATIONS[lang]['error']}: {message}")
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
