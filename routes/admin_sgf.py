"""Administrative SGF library linking routes."""
import sqlite3

from flask import flash, redirect, request, url_for


def _admin_routes():
    from routes import admin

    return admin


def register_sgf_routes(admin_bp):
    for route, endpoint, view_func in (
        ("/admin/sgf/link", "admin_link_sgf", admin_link_sgf),
        ("/admin/sgf-library/link", "admin_link_sgf_alias", admin_link_sgf),
        ("/admin/sgf/unlink", "admin_unlink_sgf", admin_unlink_sgf),
        ("/admin/sgf-library/unlink", "admin_unlink_sgf_alias", admin_unlink_sgf),
        ("/admin/sgf/delete", "admin_delete_sgf", admin_delete_sgf),
    ):
        admin_bp.add_url_rule(route, endpoint=endpoint,
                              view_func=view_func, methods=["POST"])


def _library_redirect(lang):
    return redirect(url_for("sgf_library", lang=lang))


def admin_link_sgf():
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    filename = (request.form.get("filename")
                or request.form.get("sgf_filename") or "").strip()
    match_id = request.form.get("match_id", type=int)
    if not filename or match_id is None or admin.get_sgf_path(filename) is None:
        flash(admin.TRANSLATIONS[lang].get(
            "sgf_file_not_found", admin.TRANSLATIONS[lang]["error"]))
        return _library_redirect(lang)

    conn = admin.get_db()
    try:
        admin.ensure_sgf_schema(conn)
        admin.clear_missing_sgf_links(conn)
        match = conn.execute(
            """
            SELECT id, match_date, white_player_id, black_player_id,
                   result, event, location, sgf_filename
            FROM matches
            WHERE id = ?
            """,
            (match_id,),
        ).fetchone()
        if match is None:
            flash(admin.TRANSLATIONS[lang]["error"])
            return _library_redirect(lang)

        if match["sgf_filename"] and match["sgf_filename"] != filename:
            flash(
                admin.TRANSLATIONS[lang].get(
                    "sgf_match_already_linked",
                    admin.TRANSLATIONS[lang]["error"],
                )
            )
            return _library_redirect(lang)

        existing = conn.execute(
            "SELECT id FROM matches WHERE sgf_filename = ? AND id != ? LIMIT 1",
            (filename, match_id),
        ).fetchone()
        if existing is not None:
            flash(admin.TRANSLATIONS[lang].get(
                "sgf_already_linked", admin.TRANSLATIONS[lang]["error"]))
            return _library_redirect(lang)

        try:
            metadata = admin.match_sgf_metadata(
                conn,
                match["white_player_id"],
                match["black_player_id"],
                match["match_date"],
                match["result"],
                match["event"] or "",
                location=match["location"],
            )
            admin.update_sgf_metadata(filename, metadata)
            conn.execute(
                "UPDATE matches SET sgf_filename = ? WHERE id = ?", (filename, match_id))
            conn.commit()
        except ValueError:
            conn.rollback()
            flash(admin.TRANSLATIONS[lang].get(
                "invalid_sgf", admin.TRANSLATIONS[lang]["error"]))
            return _library_redirect(lang)
        except sqlite3.IntegrityError:
            conn.rollback()
            flash(admin.TRANSLATIONS[lang].get(
                "sgf_already_linked", admin.TRANSLATIONS[lang]["error"]))
            return _library_redirect(lang)
    finally:
        conn.close()

    admin.log_admin_action(
        "sgf_linked",
        "sgf",
        {"filename": filename, "match_id": match_id},
        user_id=admin.session.get("user_id"),
    )
    flash(admin.TRANSLATIONS[lang].get(
        "sgf_linked_success", admin.TRANSLATIONS[lang]["success"]))
    return _library_redirect(lang)


def admin_unlink_sgf():
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    filename = (request.form.get("filename")
                or request.form.get("sgf_filename") or "").strip()
    match_id = request.form.get("match_id", type=int)
    if match_id is None:
        flash(admin.TRANSLATIONS[lang]["error"])
        return _library_redirect(lang)

    def action(conn):
        admin.ensure_sgf_schema(conn)
        if filename:
            result = conn.execute(
                "UPDATE matches SET sgf_filename = NULL WHERE id = ? AND sgf_filename = ?",
                (match_id, filename),
            )
        else:
            result = conn.execute(
                "UPDATE matches SET sgf_filename = NULL WHERE id = ?",
                (match_id,),
            )
        conn.commit()
        return result.rowcount

    updated = admin.run_admin_db_action(action, lang)
    if updated != 1:
        flash(admin.TRANSLATIONS[lang]["error"])
        return _library_redirect(lang)

    admin.log_admin_action(
        "sgf_unlinked",
        "sgf",
        {"filename": filename or None, "match_id": match_id},
        user_id=admin.session.get("user_id"),
    )
    flash(admin.TRANSLATIONS[lang].get(
        "sgf_unlinked_success", admin.TRANSLATIONS[lang]["success"]))
    return _library_redirect(lang)


def admin_delete_sgf():
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    filename = (request.form.get("filename")
                or request.form.get("sgf_filename") or "").strip()
    if not filename or admin.get_sgf_path(filename) is None:
        flash(admin.TRANSLATIONS[lang].get(
            "sgf_file_not_found", admin.TRANSLATIONS[lang]["error"]))
        return _library_redirect(lang)

    def action(conn):
        admin.ensure_sgf_schema(conn)
        conn.execute(
            "UPDATE matches SET sgf_filename = NULL WHERE sgf_filename = ?",
            (filename,),
        )
        conn.commit()
    admin.run_admin_db_action(action, lang)

    admin.delete_sgf_file(filename)
    admin.log_admin_action(
        "sgf_deleted",
        "sgf",
        {"filename": filename},
        user_id=admin.session.get("user_id"),
    )
    flash(admin.TRANSLATIONS[lang].get(
        "sgf_deleted_success", admin.TRANSLATIONS[lang]["success"]))
    return _library_redirect(lang)
