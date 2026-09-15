"""Administrative match listing and CRUD routes."""
import sqlite3
from flask import Response, flash, redirect, render_template, request, url_for


def _admin_routes():
    from routes import admin
    return admin


def register_match_routes(admin_bp):
    routes = (
        ("/admin/matches", "admin_matches", ("GET",)),
        ("/admin/matches/add", "admin_add_match", ("GET", "POST")),
        ("/admin/matches/edit", "admin_edit_match", ("GET", "POST")),
        ("/admin/matches/delete", "admin_delete_match", ("POST",)),
    )
    for route, endpoint, methods in routes:
        admin_bp.add_url_rule(route, endpoint=endpoint, view_func=globals()[
                              endpoint], methods=list(methods))


def admin_matches():
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    try:
        page = int(request.args.get("page")) if request.args.get(
            "page") is not None else 1
        page_size = int(request.args.get("page_size")) if request.args.get(
            "page_size") is not None else 25
    except (TypeError, ValueError):
        return Response("Invalid page parameter", status=400)
    if page <= 0:
        return Response("page must be positive", status=400)
    if page_size <= 0:
        return Response("page_size must be positive", status=400)
    page_size = min(page_size, 100)
    sort_key = admin._parse_match_sort(request.args.get("sort"))
    sort_order = admin._parse_match_order(request.args.get("order"))
    date_from, date_to, player_id = admin._parse_match_filters(request.args)
    filter_sql, filter_params = admin._match_filter_sql(
        date_from, date_to, player_id)
    conn = admin.get_db()
    admin.ensure_sgf_schema(conn)
    admin.clear_missing_sgf_links(conn)
    total_count = conn.execute(
        f"SELECT COUNT(*) FROM matches m {filter_sql}", filter_params).fetchone()[0]
    page_details = admin.pagination_details(total_count, page, page_size)
    page = page_details["page"]
    page_size = page_details["page_size"]
    order_sql = f"ORDER BY {admin.MATCH_SORT_FIELDS[sort_key]} {sort_order.upper()}, m.match_date DESC, m.round_number DESC, m.id DESC"
    match_rows = conn.execute(
        f"""
        SELECT m.id, m.match_date, m.notes, m.round_number, m.event,
               p_white.id AS white_id, p_black.id AS black_id,
               p_white.display_name AS white_name, p_black.display_name AS black_name,
               m.result, m.sgf_filename
        FROM matches m
        JOIN players p_white ON p_white.id = m.white_player_id
        JOIN players p_black ON p_black.id = m.black_player_id
        {filter_sql} {order_sql} LIMIT ? OFFSET ?
        """,
        (*filter_params, page_size, (page - 1) * page_size),
    ).fetchall()
    match_players = conn.execute(
        "SELECT id, display_name FROM players ORDER BY display_name").fetchall()
    conn.close()
    return render_template(
        "admin/matches.html", matches=match_rows, lang=lang, translations=admin.TRANSLATIONS[lang],
        total_count=total_count, sort=sort_key, order=sort_order, date_from=date_from,
        date_to=date_to, player_id=player_id, match_players=match_players, **page_details,
    )


def _match_form_data(admin, conn, lang):
    match_date = request.form.get("match_date", "").strip()
    white_player_id = request.form.get("white_player_id")
    black_player_id = request.form.get("black_player_id")
    result = request.form.get("result", "").strip()
    event = request.form.get("event", "").strip()
    location = request.form.get("location", "").strip()
    raw_notes = request.form.get("notes", "")
    notes = admin.normalize_round_note_for_storage(raw_notes)
    valid, message = admin.validate_match_form_data(
        conn, match_date, white_player_id, black_player_id, result, lang)
    handicap_stones = None
    sgf_filename = None
    if valid:
        try:
            handicap_stones = admin.parse_handicap_stones(
                request.form.get("handicap_stones"))
        except ValueError:
            message = f"{admin.TRANSLATIONS[lang]['error']}: {admin.TRANSLATIONS[lang].get('invalid_handicap', 'Invalid handicap')}"
            return False, message, None
        try:
            sgf_file = request.files.get("sgf_file")
            if sgf_file and sgf_file.filename:
                sgf_filename = admin.save_sgf_upload(
                    sgf_file,
                    admin.match_sgf_metadata(
                        conn, white_player_id, black_player_id, match_date, result, event, location=location),
                )
            return valid, message, (match_date, int(white_player_id), int(black_player_id), result, event, location, notes, raw_notes, handicap_stones, sgf_filename)
        except ValueError:
            message = f"{admin.TRANSLATIONS[lang]['error']}: {admin.TRANSLATIONS[lang].get('invalid_sgf', 'Invalid SGF file')}"
            return False, message, None
    return False, message, None


def admin_add_match():
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    admin.ensure_sgf_schema(conn)
    if request.method == "POST":
        valid, message, data = _match_form_data(admin, conn, lang)
        if valid:
            match_date, white_id, black_id, result, event, location, notes, raw_notes, handicap, sgf = data
            conn.execute("INSERT INTO matches (match_date, white_player_id, black_player_id, result, event, location, notes, round_number, handicap_stones, sgf_filename) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (match_date, white_id, black_id, result, event, location, notes, admin.normalize_round_note(raw_notes), handicap, sgf))
            conn.commit()
            admin.refresh_stats()
            admin.mark_dirty(match_date)
            admin.update_from_latest_snapshot()
            admin.log_admin_action("match_created", "match", {"match_id": conn.execute(
                "SELECT last_insert_rowid()").fetchone()[0], "event": event or None}, user_id=admin.session.get("user_id"))
            flash(admin.TRANSLATIONS[lang]["success"])
            conn.close()
            return redirect(url_for("admin_matches", lang=lang))
        flash(message or admin.TRANSLATIONS[lang]["error"])
    players = conn.execute(
        "SELECT id, display_name FROM players ORDER BY display_name").fetchall()
    conn.close()
    return render_template("admin/match_form.html", lang=lang, translations=admin.TRANSLATIONS[lang], players=players, match=None, form_action=url_for("admin_add_match", lang=lang))


def admin_edit_match():
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    match_id = request.args.get("id")
    if not match_id:
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_matches", lang=lang))
    conn = admin.get_db()
    admin.ensure_sgf_schema(conn)
    admin.clear_missing_sgf_links(conn)
    match = conn.execute(
        "SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not match:
        conn.close()
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_matches", lang=lang))
    if request.method == "POST":
        valid, message, data = _match_form_data(admin, conn, lang)
        if valid:
            match_date, white_id, black_id, result, event, location, notes, raw_notes, handicap, new_sgf = data
            old_sgf = match["sgf_filename"]
            if not new_sgf:
                new_sgf = None if request.form.get(
                    "remove_sgf") == "1" else old_sgf
            try:
                conn.execute("UPDATE matches SET match_date = ?, white_player_id = ?, black_player_id = ?, result = ?, event = ?, location = ?, notes = ?, round_number = ?, handicap_stones = ?, sgf_filename = ? WHERE id = ?",
                             (match_date, white_id, black_id, result, event, location, notes, admin.normalize_round_note(raw_notes), handicap, new_sgf, match_id))
                admin.sync_match_pairing(
                    conn, match_id, white_id, black_id, result, handicap)
                conn.commit()
                if new_sgf:
                    admin.update_sgf_metadata(new_sgf, admin.match_sgf_metadata(
                        conn, white_id, black_id, match_date, result, event))
                admin.refresh_stats()
                admin.mark_dirty(match["match_date"])
                admin.mark_dirty(match_date)
                admin.update_from_latest_snapshot()
                admin.log_admin_action("match_updated", "match", {
                                       "match_id": match_id}, user_id=admin.session.get("user_id"))
                flash(admin.TRANSLATIONS[lang]["success"])
            except ValueError as exc:
                conn.rollback()
                flash(f"{admin.TRANSLATIONS[lang]['error']}: {exc}")
            except sqlite3.DatabaseError as exc:
                conn.rollback()
                admin.logger.exception(
                    "Match %s was saved, but post-save statistics refresh failed; restoring backup.", match_id)
                backup_path = admin.get_latest_valid_backup_path()
                if backup_path is not None and admin.restore_db_from_backup(backup_path):
                    flash(
                        "Database recovered from backup after a malformed SQLite image. Please review the restored data before continuing.")
                else:
                    flash(f"{admin.TRANSLATIONS[lang]['error']}: {exc}")
            finally:
                conn.close()
            return redirect(url_for("admin_matches", lang=lang))
        flash(message or admin.TRANSLATIONS[lang]["error"])
    players = conn.execute(
        "SELECT id, display_name FROM players ORDER BY display_name").fetchall()
    conn.close()
    return render_template("admin/match_form.html", lang=lang, translations=admin.TRANSLATIONS[lang], players=players, match=match, form_action=url_for("admin_edit_match", id=match_id, lang=lang))


def admin_delete_match():
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    match_id = request.args.get("id")
    if not match_id:
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_matches", lang=lang))
    conn = admin.get_db()
    admin.ensure_sgf_schema(conn)
    admin.clear_missing_sgf_links(conn)
    row = conn.execute(
        "SELECT match_date, sgf_filename FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not row:
        conn.close()
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_matches", lang=lang))
    conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
    conn.commit()
    conn.close()
    admin.log_admin_action("match_deleted", "match", {
                           "match_id": match_id}, user_id=admin.session.get("user_id"))
    try:
        admin.refresh_stats()
        admin.mark_dirty(row["match_date"])
        admin.update_from_latest_snapshot()
    except sqlite3.DatabaseError as exc:
        admin.logger.exception(
            "Match deletion for %s left the database in a malformed state; restoring a valid backup.", match_id)
        backup_path = admin.get_latest_valid_backup_path()
        if backup_path is not None and admin.restore_db_from_backup(backup_path):
            flash("Database recovered from backup after a malformed SQLite image. Please review the restored data before continuing.")
        else:
            flash(f"{admin.TRANSLATIONS[lang]['error']}: {exc}")
    else:
        flash(admin.TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_matches", lang=lang))
