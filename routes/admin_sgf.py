"""Administrative SGF library linking routes."""
import sqlite3

from flask import flash, redirect, request, url_for

MAX_SGF_DELETE_BATCH = 100


def _admin_routes():
    from routes import admin

    return admin


def register_sgf_routes(admin_bp):
    for route, endpoint, view_func in (
        ("/admin/sgf/upload", "admin_upload_sgf", admin_upload_sgf),
        ("/admin/sgf/link", "admin_link_sgf", admin_link_sgf),
        ("/admin/sgf-library/link", "admin_link_sgf_alias", admin_link_sgf),
        ("/admin/sgf/unlink", "admin_unlink_sgf", admin_unlink_sgf),
        ("/admin/sgf-library/unlink", "admin_unlink_sgf_alias", admin_unlink_sgf),
        ("/admin/sgf/delete", "admin_delete_sgf", admin_delete_sgf),
        ("/admin/sgf/delete-batch",
         "admin_delete_sgf_batch", admin_delete_sgf_batch),
    ):
        admin_bp.add_url_rule(route, endpoint=endpoint,
                              view_func=view_func, methods=["POST"])


def _library_redirect(lang):
    return redirect(url_for("sgf_library", lang=lang))


def admin_upload_sgf():
    admin = _admin_routes()
    permission_error = admin.require_permission("results_submitter")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    sgf_file = request.files.get("sgf_file")
    if sgf_file is None or not sgf_file.filename:
        flash(admin.TRANSLATIONS[lang].get(
            "sgf_file_required", admin.TRANSLATIONS[lang]["error"])
        )
        return _library_redirect(lang)

    try:
        filename = admin.save_sgf_upload(sgf_file)
    except ValueError:
        flash(admin.TRANSLATIONS[lang].get(
            "invalid_sgf", admin.TRANSLATIONS[lang]["error"])
        )
        return _library_redirect(lang)

    admin.log_admin_action(
        "sgf_uploaded",
        "sgf",
        {"filename": filename},
        user_id=admin.session.get("user_id"),
    )
    flash(admin.TRANSLATIONS[lang].get(
        "sgf_uploaded_success", admin.TRANSLATIONS[lang]["success"]))
    return _library_redirect(lang)


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


def _delete_sgf_files(admin, lang, filenames):
    translations = admin.TRANSLATIONS[lang]
    selected = list(dict.fromkeys(filenames))
    if not selected or len(selected) > MAX_SGF_DELETE_BATCH:
        flash(translations["error"])
        return _library_redirect(lang)
    paths = {filename: admin.get_sgf_path(filename) for filename in selected}
    if any(path is None or not path.is_file() for path in paths.values()):
        flash(translations.get(
            "sgf_file_not_found", translations["error"]))
        return _library_redirect(lang)

    def action(conn):
        admin.ensure_sgf_schema(conn)
        placeholders = ", ".join("?" for _ in selected)
        conn.execute(
            f"UPDATE matches SET sgf_filename = NULL "
            f"WHERE sgf_filename IN ({placeholders})",
            selected,
        )
        submissions_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'result_submissions'"
        ).fetchone()
        if submissions_table and "sgf_filename" in {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(result_submissions)"
            ).fetchall()
        }:
            conn.execute(
                f"UPDATE result_submissions SET sgf_filename = NULL "
                f"WHERE sgf_filename IN ({placeholders})",
                selected,
            )
        conn.commit()

    try:
        admin.run_admin_db_action(action, lang)
    except sqlite3.DatabaseError as exc:
        admin.logger.exception("SGF batch deletion could not clear match links")
        flash(f"{translations['error']}: {exc}")
        return _library_redirect(lang)

    failures = []
    for filename, path in paths.items():
        try:
            path.unlink()
        except OSError:
            admin.logger.exception("Could not delete SGF file %s", filename)
            failures.append(filename)
            continue
        admin.log_admin_action(
            "sgf_deleted",
            "sgf",
            {"filename": filename},
            user_id=admin.session.get("user_id"),
        )

    deleted_count = len(selected) - len(failures)
    if failures:
        flash(
            translations["sgf_batch_delete_partial"].format(
                deleted=deleted_count, failed=len(failures)
            )
        )
    else:
        flash(translations["sgf_batch_deleted_success"].format(
            count=deleted_count))
    return _library_redirect(lang)


def admin_delete_sgf():
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    filename = (request.form.get("filename")
                or request.form.get("sgf_filename") or "").strip()
    return _delete_sgf_files(admin, lang, [filename] if filename else [])


def admin_delete_sgf_batch():
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    filenames = [
        filename.strip()
        for filename in request.form.getlist("filenames")
        if filename.strip()
    ]
    return _delete_sgf_files(admin, lang, filenames)
