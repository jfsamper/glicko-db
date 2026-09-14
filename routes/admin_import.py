"""Administrative workbook, CSV, and OpenGotha import routes."""
import csv
import os
from pathlib import Path
import sqlite3

from flask import flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from services.i18n import TRANSLATIONS


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
    preview_file = request.form.get("preview_file") or request.args.get("preview_file")

    if request.method == "POST":
        action = request.form.get("action")
        file = request.files.get("file")

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
                    metadata_overrides["acceleration_scheme"] = admin.acceleration_scheme_from_form(request.form)
                    metadata_rounds = request.form.get("metadata_rounds", type=int) or 1
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
                    metadata_overrides["description"] = (request.form.get("metadata_description") or "").strip()
                conn = admin.get_db()
                try:
                    if request.form.get("metadata_decision") == "reject":
                        raise ValueError("Import rejected during metadata review")
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
        upload_path = os.path.join(admin.BASE_DIR, "uploads", filename)
        os.makedirs(os.path.dirname(upload_path), exist_ok=True)
        file.save(upload_path)

        try:
            extension = Path(filename).suffix.lower()
            if extension in (".xlsx", ".xls"):
                stats = admin.import_workbook_data(upload_path, reset=True)
                admin.run_post_import_replay()
                flash(
                    f"{TRANSLATIONS[lang]['success']} "
                    f"({stats['players']} players, {stats['matches']} matches)"
                )
                return redirect(url_for("import_matches", lang=lang))

            if extension == ".xml":
                conn = admin.get_db()
                try:
                    preview = admin.build_import_preview(conn, upload_path)
                finally:
                    conn.close()
                return render_template(
                    "admin/import.html",
                    lang=lang,
                    translations=TRANSLATIONS[lang],
                    preview=preview,
                    preview_file=filename,
                    acceleration_scheme_options=admin.ACCELERATION_SCHEMES,
                    acceleration_scheme_choice=admin.acceleration_scheme_choice(
                        preview["metadata"].get("acceleration_scheme")
                    ),
                )

            if extension == ".csv":
                with open(upload_path, newline="", encoding="utf-8-sig") as csv_file:
                    reader = csv.DictReader(csv_file)
                    required_columns = {"date", "white", "black", "result"}
                    columns = {col.strip().lower() for col in reader.fieldnames or []}
                    if not required_columns.issubset(columns):
                        raise ValueError(TRANSLATIONS[lang]["required_columns_missing"])

                    conn = admin.get_db()
                    try:
                        players = conn.execute("SELECT id, display_name FROM players").fetchall()
                        player_lookup = {
                            admin.normalize_key(row["display_name"]): row["id"] for row in players
                        }
                        imported_matches = 0
                        earliest_match_date = None
                        for row in reader:
                            white_id = player_lookup.get(admin.normalize_key(str(row.get("white", "")).strip()))
                            black_id = player_lookup.get(admin.normalize_key(str(row.get("black", "")).strip()))
                            if white_id is None or black_id is None:
                                continue

                            match_date = admin.parse_date_value(row.get("date", ""))
                            handicap_stones = admin.parse_handicap_stones(row.get("handicap"))
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
                                    admin.normalize_round_note(row.get("notes", row.get("round", ""))),
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
                flash(f"{TRANSLATIONS[lang]['success']} ({imported_matches} matches)")
                return redirect(url_for("import_matches", lang=lang))

            raise ValueError(TRANSLATIONS[lang]["unsupported_file_format"])
        except Exception as exc:
            flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
            return redirect(url_for("import_matches", lang=lang))

    return render_template(
        "admin/import.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        preview=preview,
        preview_file=preview_file,
        acceleration_scheme_options=admin.ACCELERATION_SCHEMES,
        acceleration_scheme_choice=admin.acceleration_scheme_choice(
            preview["metadata"].get("acceleration_scheme") if preview else None
        ),
    )
