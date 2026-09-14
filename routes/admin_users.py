"""Administrative authentication, user, profile, settings, audit, and submission routes."""
import sqlite3
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def _admin_routes():
    from routes import admin

    return admin


def register_user_routes(admin_bp):
    routes = (
        ("/admin/login", "admin_login", ("GET", "POST")),
        ("/admin/register", "admin_register", ("GET", "POST")),
        ("/admin/report-results", "admin_report_results", ("GET", "POST")),
        ("/admin/result-submissions", "admin_result_submissions", ("GET",)),
        ("/admin/result-submissions/<int:submission_id>/sgf", "admin_result_submission_sgf", ("GET",)),
        ("/admin/result-submissions/<int:submission_id>/approve", "admin_approve_result_submission", ("POST",)),
        ("/admin/result-submissions/<int:submission_id>/reject", "admin_reject_result_submission", ("POST",)),
        ("/admin/settings", "admin_settings", ("GET", "POST")),
        ("/admin/profile", "admin_profile", ("GET", "POST")),
        ("/admin/forgot-password", "admin_forgot_password", ("GET", "POST")),
        ("/admin/forgot_password", "admin_forgot_password_alias", ("GET", "POST")),
        ("/admin/reset-password/<token>", "admin_reset_password", ("GET", "POST")),
        ("/admin/logout", "admin_logout", ("GET",)),
        ("/admin/audit", "admin_audit_review", ("GET",)),
        ("/admin/users", "admin_users", ("GET",)),
        ("/admin/users/create", "admin_create_user", ("GET", "POST")),
        ("/admin/users/<int:user_id>/edit", "admin_edit_user", ("GET", "POST")),
        ("/admin/users/<int:user_id>/delete", "admin_delete_user", ("POST",)),
    )
    view_functions = {
        "admin_login": admin_login,
        "admin_register": admin_register,
        "admin_report_results": admin_report_results,
        "admin_result_submissions": admin_result_submissions,
        "admin_result_submission_sgf": admin_result_submission_sgf,
        "admin_approve_result_submission": admin_approve_result_submission,
        "admin_reject_result_submission": admin_reject_result_submission,
        "admin_settings": admin_settings,
        "admin_profile": admin_profile,
        "admin_forgot_password": admin_forgot_password,
        "admin_forgot_password_alias": admin_forgot_password,
        "admin_reset_password": admin_reset_password,
        "admin_logout": admin_logout,
        "admin_audit_review": admin_audit_review,
        "admin_users": admin_users,
        "admin_create_user": admin_create_user,
        "admin_edit_user": admin_edit_user,
        "admin_delete_user": admin_delete_user,
    }
    for route, endpoint, methods in routes:
        admin_bp.add_url_rule(
            route,
            endpoint=endpoint,
            view_func=view_functions[endpoint],
            methods=list(methods),
        )


def admin_login():
    admin = _admin_routes()
    lang = admin.get_language(request.args.get("lang"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password")
        client_ip = request.remote_addr or "unknown"

        conn = admin.get_db()
        try:
            user = admin.authenticate_user(username, password, conn=conn)
            if user is not None:
                session.clear()
                session["user_id"] = user["id"]
                session["user_role"] = user.get("role") or "administrator"
                if user.get("language"):
                    session["user_language"] = user["language"]
                if user.get("theme"):
                    session["user_theme"] = user["theme"]
                admin.clear_login_attempts(client_ip)
                admin.log_admin_action(
                    "login",
                    "auth",
                    {"username": user["username"], "role": user.get("role")},
                    user_id=user["id"],
                )
                if user.get("role") == "member":
                    return redirect(url_for("admin_report_results", lang=lang))
                return redirect(url_for("admin", lang=lang))
        finally:
            conn.close()

        if admin.record_failed_login_attempt(client_ip):
            flash(admin.TRANSLATIONS[lang]["invalid_password"])
            return render_template(
                "admin/login.html",
                lang=lang,
                translations=admin.TRANSLATIONS[lang],
            )

        flash(admin.TRANSLATIONS[lang]["invalid_password"])

    return render_template(
        "admin/login.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
    )


def admin_register():
    admin = _admin_routes()
    lang = admin.get_language(request.args.get("lang"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        email = (request.form.get("email") or "").strip()
        if not admin.verify_recaptcha(
            request.form.get("recaptcha_token"),
            action="register",
            remote_ip=request.remote_addr,
        ):
            flash(admin.TRANSLATIONS[lang]["recaptcha_failed"])
        elif len(password) < 8:
            flash(admin.TRANSLATIONS[lang]["password_too_short"])
        elif password != confirm_password:
            flash(admin.TRANSLATIONS[lang]["password_mismatch"])
        else:
            try:
                admin.create_user_account(
                    username,
                    password,
                    role_name="member",
                    email=email,
                )
                flash(admin.TRANSLATIONS[lang]["account_created_success"])
                return redirect(url_for("admin_login", lang=lang))
            except ValueError as exc:
                message_key = {
                    "username already exists": "user_username_taken",
                    "email already exists": "email_taken",
                    "invalid email": "invalid_email",
                }.get(str(exc), "error")
                flash(admin.TRANSLATIONS[lang][message_key])

    return render_template(
        "admin/register.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        recaptcha_site_key=admin.RECAPTCHA_SITE_KEY,
    )


def admin_report_results():
    admin = _admin_routes()
    permission_error = admin.require_permission("results_submitter")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    user = admin.get_current_user()
    conn = admin.get_db()
    try:
        if user and user.get("player_id") is not None:
            linked_player = conn.execute(
                "SELECT display_name FROM players WHERE id = ?",
                (user["player_id"],),
            ).fetchone()
            user["player_name"] = linked_player["display_name"] if linked_player else ""

        if request.method == "POST":
            player_id = user.get("player_id") if user else None
            opponent_id = request.form.get("opponent_player_id")
            color = request.form.get("color", "white")
            result = (request.form.get("result") or "").strip()
            match_date = (request.form.get("match_date") or "").strip()
            event = (request.form.get("event") or "").strip()
            location = (request.form.get("location") or "").strip()
            notes = (request.form.get("notes") or "").strip()
            sgf_file = request.files.get("sgf_file")
            sgf_filename = None
            if player_id is None:
                flash(admin.TRANSLATIONS[lang]["player_link_required"])
            elif color not in {"white", "black"}:
                flash(admin.TRANSLATIONS[lang]["error"])
            else:
                white_player_id = player_id if color == "white" else opponent_id
                black_player_id = opponent_id if color == "white" else player_id
                valid, message = admin.validate_match_form_data(
                    conn,
                    match_date,
                    white_player_id,
                    black_player_id,
                    result,
                    lang,
                )
                try:
                    handicap_stones = admin.parse_handicap_stones(request.form.get("handicap_stones"))
                except ValueError:
                    valid = False
                    message = admin.TRANSLATIONS[lang]["error"]
                if valid:
                    duplicate = conn.execute(
                        """
                        SELECT 1 FROM result_submissions
                        WHERE submitted_by_user_id = ? AND status = 'pending'
                          AND match_date = ? AND white_player_id = ?
                          AND black_player_id = ? AND result = ?
                        """,
                        (user["id"], match_date, white_player_id, black_player_id, result),
                    ).fetchone()
                    if duplicate is not None:
                        valid = False
                        message = admin.TRANSLATIONS[lang]["duplicate_submission"]
                if valid and sgf_file and sgf_file.filename:
                    try:
                        sgf_filename = admin.save_sgf_upload(
                            sgf_file,
                            admin.match_sgf_metadata(
                                conn,
                                white_player_id,
                                black_player_id,
                                match_date,
                                result,
                                event,
                                location=location,
                            ),
                        )
                    except ValueError:
                        valid = False
                        message = f"{admin.TRANSLATIONS[lang]['error']}: {admin.TRANSLATIONS[lang].get('invalid_sgf', 'Invalid SGF file')}"
                if valid:
                    conn.execute(
                        """
                        INSERT INTO result_submissions
                            (submitted_by_user_id, match_date, white_player_id, black_player_id,
                                result, event, location, notes, handicap_stones, sgf_filename)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user["id"],
                            match_date,
                            white_player_id,
                            black_player_id,
                            result,
                            event,
                            location,
                            notes,
                            handicap_stones,
                            sgf_filename,
                        ),
                    )
                    conn.commit()
                    admin.log_admin_action(
                        "result_submitted",
                        "result_submission",
                        {"submission_id": conn.execute("SELECT last_insert_rowid()").fetchone()[0]},
                        user_id=user["id"],
                    )
                    flash(admin.TRANSLATIONS[lang]["result_submitted_success"])
                    return redirect(url_for("admin_report_results", lang=lang))
                flash(message or admin.TRANSLATIONS[lang]["error"])

        opponents = []
        if user and user.get("player_id") is not None:
            opponents = conn.execute(
                "SELECT id, display_name FROM players WHERE id != ? ORDER BY display_name",
                (user["player_id"],),
            ).fetchall()
        submissions = conn.execute(
            """
            SELECT rs.*, p_white.display_name AS white_name, p_black.display_name AS black_name
            FROM result_submissions rs
            JOIN players p_white ON p_white.id = rs.white_player_id
            JOIN players p_black ON p_black.id = rs.black_player_id
            WHERE rs.submitted_by_user_id = ?
            ORDER BY rs.created_at DESC, rs.id DESC
            """,
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()

    return render_template(
        "admin/report_results.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        user=user,
        opponents=opponents,
        submissions=submissions,
    )


def admin_result_submissions():
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    status = request.args.get("status", "pending")
    if status not in {"pending", "approved", "rejected", "all"}:
        status = "pending"
    conn = admin.get_db()
    try:
        query = """
            SELECT rs.*, submitter.username AS submitter_username,
                   p_white.display_name AS white_name, p_black.display_name AS black_name,
                   reviewer.username AS reviewer_username
            FROM result_submissions rs
            JOIN users submitter ON submitter.id = rs.submitted_by_user_id
            JOIN players p_white ON p_white.id = rs.white_player_id
            JOIN players p_black ON p_black.id = rs.black_player_id
            LEFT JOIN users reviewer ON reviewer.id = rs.reviewed_by_user_id
        """
        params = []
        if status != "all":
            query += " WHERE rs.status = ?"
            params.append(status)
        query += " ORDER BY rs.created_at DESC, rs.id DESC"
        submissions = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return render_template(
        "admin/result_submissions.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        submissions=submissions,
        selected_status=status,
    )


def admin_result_submission_sgf(submission_id):
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    conn = admin.get_db()
    submission = conn.execute(
        "SELECT sgf_filename FROM result_submissions WHERE id = ?",
        (submission_id,),
    ).fetchone()
    conn.close()
    if submission is None:
        abort(404)
    path = admin.get_sgf_path(submission["sgf_filename"])
    if path is None:
        abort(404)
    return send_file(
        path,
        mimetype="application/x-go-sgf",
        as_attachment=False,
        download_name=f"submission-{submission_id}.sgf",
        max_age=0,
    )


def admin_approve_result_submission(submission_id):
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        admin.ensure_sgf_schema(conn)
        submission = conn.execute(
            "SELECT * FROM result_submissions WHERE id = ? AND status = 'pending'",
            (submission_id,),
        ).fetchone()
        if submission is None:
            flash(admin.TRANSLATIONS[lang]["submission_not_pending"])
            return redirect(url_for("admin_result_submissions", lang=lang))
        now = admin.current_timestamp()
        match_id = conn.execute(
            """
            INSERT INTO matches
                (match_date, white_player_id, black_player_id, result, event, location, notes, round_number, handicap_stones, sgf_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submission["match_date"],
                submission["white_player_id"],
                submission["black_player_id"],
                submission["result"],
                submission["event"],
                submission["location"],
                submission["notes"],
                submission["round_number"],
                submission["handicap_stones"],
                submission["sgf_filename"],
            ),
        ).lastrowid
        conn.execute(
            """
            UPDATE result_submissions
            SET status = 'approved', reviewed_by_user_id = ?, reviewed_at = ?, review_notes = ?
            WHERE id = ? AND status = 'pending'
            """,
            (session.get("user_id"), now, request.form.get("review_notes", "").strip(), submission_id),
        )
        conn.commit()
    except sqlite3.DatabaseError:
        conn.rollback()
        admin.logger.exception("Could not approve result submission %s", submission_id)
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_result_submissions", lang=lang))
    finally:
        conn.close()
    admin.refresh_stats()
    admin.mark_dirty(submission["match_date"])
    admin.update_from_latest_snapshot()
    admin.log_admin_action(
        "result_submission_approved",
        "result_submission",
        {"submission_id": submission_id, "match_id": match_id},
        user_id=session.get("user_id"),
    )
    flash(admin.TRANSLATIONS[lang]["submission_approved"])
    return redirect(url_for("admin_result_submissions", lang=lang))


def admin_reject_result_submission(submission_id):
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        submission = conn.execute(
            "SELECT sgf_filename FROM result_submissions WHERE id = ? AND status = 'pending'",
            (submission_id,),
        ).fetchone()
        updated = conn.execute(
            """
            UPDATE result_submissions
            SET status = 'rejected', reviewed_by_user_id = ?, reviewed_at = ?, review_notes = ?
            WHERE id = ? AND status = 'pending'
            """,
            (session.get("user_id"), admin.current_timestamp(), request.form.get("review_notes", "").strip(), submission_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not updated:
        flash(admin.TRANSLATIONS[lang]["submission_not_pending"])
    else:
        admin.log_admin_action(
            "result_submission_rejected",
            "result_submission",
            {"submission_id": submission_id},
            user_id=session.get("user_id"),
        )
        flash(admin.TRANSLATIONS[lang]["submission_rejected"])
    return redirect(url_for("admin_result_submissions", lang=lang))


def admin_settings():
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    settings = admin.get_application_settings()
    if request.method == "POST":
        action = request.form.get("action")
        values = admin.DEFAULT_APPLICATION_SETTINGS if action == "reset" else {
            "max_login_attempts": request.form.get("max_login_attempts"),
            "login_window_seconds": request.form.get("login_window_seconds"),
            "password_reset_ttl_seconds": request.form.get("password_reset_ttl_seconds"),
        }
        try:
            settings = admin.update_application_settings(values)
        except ValueError:
            flash(admin.TRANSLATIONS[lang]["invalid_application_settings"])
        else:
            admin.log_admin_action(
                "application_settings_reset" if action == "reset" else "application_settings_updated",
                "application_settings",
                settings,
                user_id=session.get("user_id"),
            )
            flash(
                admin.TRANSLATIONS[lang]["reset_to_default_success"]
                if action == "reset"
                else admin.TRANSLATIONS[lang]["success"]
            )
            return redirect(url_for("admin_settings", lang=lang))

    return render_template(
        "admin/settings.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        settings=settings,
    )


def admin_profile():
    admin = _admin_routes()
    permission_error = admin.require_permission("results_submitter")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    user = admin.get_current_user()
    if user is None:
        return redirect(url_for("admin_login", lang=lang))
    timezone_choices = admin.get_timezone_choices()
    timezone_labels = {
        timezone: admin.format_timezone_label(timezone)
        for timezone in timezone_choices
    }

    if request.method == "POST":
        if request.form.get("logout") == "1":
            admin.log_admin_action(
                "logout",
                "auth",
                {"status": "success"},
                user_id=user["id"],
            )
            session.clear()
            return redirect(url_for("index", lang=lang))

        email = (request.form.get("email") or "").strip()
        language = (request.form.get("language") or "").strip()
        theme = (request.form.get("theme") or "").strip()
        timezone_name = (request.form.get("timezone") or "").strip()
        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        try:
            email = admin.validate_email_address(email) if email else None
            timezone_name = admin.validate_timezone(timezone_name)
            admin.validate_theme(theme)
        except ValueError as exc:
            message_key = {
                "invalid email": "invalid_email",
                "unsupported timezone": "invalid_timezone",
                "unsupported theme": "invalid_theme",
            }.get(str(exc), "error")
            flash(admin.TRANSLATIONS[lang][message_key])
            user.update(email=email, language=language, theme=theme, timezone=timezone_name)
            return render_template(
                "admin/profile.html",
                lang=lang,
                translations=admin.TRANSLATIONS[lang],
                user=user,
                languages=admin.LANGUAGE_CHOICES,
                themes=admin.THEME_CHOICES,
                timezone_choices=timezone_choices,
                timezone_labels=timezone_labels,
            )

        if language not in admin.LANGUAGE_CHOICES:
            flash(admin.TRANSLATIONS[lang]["invalid_language"])
            return redirect(url_for("admin_profile", lang=lang))

        password_hash = None
        if current_password or new_password or confirm_password:
            if not check_password_hash(user["password_hash"], current_password):
                flash(admin.TRANSLATIONS[lang]["current_password_invalid"])
                return redirect(url_for("admin_profile", lang=lang))
            if len(new_password) < 8:
                flash(admin.TRANSLATIONS[lang]["password_too_short"])
                return redirect(url_for("admin_profile", lang=lang))
            if new_password != confirm_password:
                flash(admin.TRANSLATIONS[lang]["password_mismatch"])
                return redirect(url_for("admin_profile", lang=lang))
            password_hash = generate_password_hash(new_password)

        conn = admin.get_db()
        try:
            duplicate = conn.execute(
                "SELECT id FROM users WHERE email = ? AND id != ?",
                (email, user["id"]),
            ).fetchone() if email else None
            if duplicate is not None:
                flash(admin.TRANSLATIONS[lang]["email_taken"])
                return redirect(url_for("admin_profile", lang=lang))
            if password_hash:
                conn.execute(
                    "UPDATE users SET email = ?, language = ?, theme = ?, timezone = ?, password_hash = ? WHERE id = ?",
                    (email, language, theme, timezone_name, password_hash, user["id"]),
                )
            else:
                conn.execute(
                    "UPDATE users SET email = ?, language = ?, theme = ?, timezone = ? WHERE id = ?",
                    (email, language, theme, timezone_name, user["id"]),
                )
            conn.commit()
        finally:
            conn.close()
        session["user_language"] = language
        session["user_theme"] = theme
        admin.log_admin_action(
            "profile_updated",
            "user",
            {"email_changed": email != user.get("email"), "password_changed": bool(password_hash)},
            user_id=user["id"],
        )
        flash(admin.TRANSLATIONS[lang]["profile_updated_success"])
        return redirect(url_for("admin_profile", lang=language))

    return render_template(
        "admin/profile.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        user=user,
        languages=admin.LANGUAGE_CHOICES,
        themes=admin.THEME_CHOICES,
        timezone_choices=timezone_choices,
        timezone_labels=timezone_labels,
    )


def admin_forgot_password():
    admin = _admin_routes()
    lang = admin.get_language(request.args.get("lang"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        conn = admin.get_db()
        try:
            user = conn.execute(
                "SELECT id, email FROM users WHERE lower(email) = ? AND is_active = 1",
                (email,),
            ).fetchone() if email else None
            if user is not None and user["email"]:
                token = admin.create_password_reset_token(user["id"], conn=conn)
                conn.commit()
                reset_url = url_for(
                    "admin.admin_reset_password",
                    token=token,
                    lang=lang,
                    _external=True,
                )
                try:
                    admin.send_password_reset_email(user["email"], reset_url)
                except Exception:
                    admin.logger.exception("Password reset email delivery failed")
        finally:
            conn.close()
        flash(admin.TRANSLATIONS[lang]["password_reset_requested"])
    return render_template(
        "admin/forgot_password.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
    )


def admin_reset_password(token):
    admin = _admin_routes()
    lang = admin.get_language(request.args.get("lang"))
    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        if len(new_password) < 8:
            flash(admin.TRANSLATIONS[lang]["password_too_short"])
        elif new_password != confirm_password:
            flash(admin.TRANSLATIONS[lang]["password_mismatch"])
        else:
            if admin.reset_password_with_token(token, new_password):
                flash(admin.TRANSLATIONS[lang]["password_reset_success"])
                return redirect(url_for("admin_login", lang=lang))
            flash(admin.TRANSLATIONS[lang]["invalid_reset_token"])
    return render_template(
        "admin/reset_password.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        token=token,
    )


def admin_logout():
    admin = _admin_routes()
    current_user_id = session.get("user_id")
    admin.log_admin_action(
        "logout",
        "auth",
        {"status": "success"},
        user_id=current_user_id,
    )
    session.clear()

    return redirect(
        url_for("index")
    )


def admin_audit_review():
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    selected_user_id = (request.args.get("user_id") or "").strip()
    selected_action_type = (request.args.get("action_type") or "").strip()
    search_text = (request.args.get("q") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    for date_value_name in ("date_from", "date_to"):
        date_value = locals()[date_value_name]
        if date_value:
            try:
                datetime.strptime(date_value, "%Y-%m-%d")
            except ValueError:
                if date_value_name == "date_from":
                    date_from = ""
                else:
                    date_to = ""

    conn = admin.get_db()
    try:
        users = conn.execute(
            "SELECT id, username FROM users ORDER BY username"
        ).fetchall()
        action_types = [
            row["action_type"]
            for row in conn.execute(
                "SELECT DISTINCT action_type FROM audit_log ORDER BY action_type"
            ).fetchall()
        ]

        query = """
            SELECT a.id, a.user_id, a.action_type, a.resource_type, a.details, a.created_at,
                   COALESCE(u.username, 'System') AS username
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE 1 = 1
        """
        params = []

        if selected_user_id not in ("", "all"):
            if selected_user_id.isdigit():
                query += " AND a.user_id = ?"
                params.append(int(selected_user_id))
            else:
                selected_user_id = ""

        if selected_action_type:
            query += " AND a.action_type = ?"
            params.append(selected_action_type)

        if search_text:
            query += " AND (u.username LIKE ? OR a.action_type LIKE ? OR a.resource_type LIKE ? OR a.details LIKE ?)"
            search_pattern = f"%{search_text}%"
            params.extend([search_pattern] * 4)
        if date_from:
            query += " AND a.created_at >= ?"
            params.append(f"{date_from} 00:00:00")
        if date_to:
            query += " AND a.created_at <= ?"
            params.append(f"{date_to} 23:59:59")

        query += " ORDER BY a.created_at DESC LIMIT 200"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    events = []
    for row in rows:
        events.append(
            {
                "id": row["id"],
                "username": row["username"],
                "action_type": row["action_type"],
                "resource_type": row["resource_type"],
                "created_at": row["created_at"],
                "details_summary": admin._audit_details_summary(row["details"]),
            }
        )

    return render_template(
        "admin/audit.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        events=events,
        users=users,
        action_types=action_types,
        selected_user_id=selected_user_id,
        selected_action_type=selected_action_type,
        search_text=search_text,
        date_from=date_from,
        date_to=date_to,
    )


def admin_users():
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        users = conn.execute(
            """
            SELECT u.id,
                   u.username,
                   u.is_active,
                     u.timezone,
                     u.email,
                    u.player_id,
                   COALESCE(GROUP_CONCAT(r.name, ', '), '') AS role_names
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
                 GROUP BY u.id, u.username, u.is_active, u.timezone, u.email, u.player_id
            ORDER BY u.username
            """
        ).fetchall()
    finally:
        conn.close()

    player_names = {
        player["id"]: player["display_name"]
        for player in admin.load_players_for_user_link()
    }
    users = [dict(user, player_name=player_names.get(user["player_id"])) for user in users]

    return render_template(
        "admin/users.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        users=users,
        roles=list(admin.ALLOWED_ROLES),
        timezone_choices=admin.get_timezone_choices(),
        timezone_labels={
            timezone: admin.format_timezone_label(timezone)
            for timezone in admin.get_timezone_choices()
        },
    )


def admin_create_user():
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        role_name = (request.form.get("role_name") or "operator").strip()
        timezone_name = (request.form.get("timezone") or "").strip()
        email = (request.form.get("email") or "").strip()
        player_id = request.form.get("player_id") or None
        if player_id is not None:
            try:
                player_id = int(player_id)
            except ValueError:
                player_id = None

        try:
            user_id = admin.create_user_account(
                username,
                password,
                role_name=role_name,
                timezone_name=timezone_name,
                email=email,
                player_id=player_id,
            )
            admin.log_admin_action(
                "user_created",
                "user",
                {"username": username, "role": role_name},
                user_id=session.get("user_id"),
            )
            flash(admin.TRANSLATIONS[lang]["user_created_success"])
            return redirect(url_for("admin_users", lang=lang))
        except ValueError as exc:
            message_key = {
                "unsupported timezone": "invalid_timezone",
                "invalid email": "invalid_email",
                "email already exists": "email_taken",
            }.get(str(exc))
            flash(admin.TRANSLATIONS[lang][message_key] if message_key else admin.TRANSLATIONS[lang]["error"])

    return render_template(
        "admin/create_user.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        roles=list(admin.ALLOWED_ROLES),
        players=admin.load_players_for_user_link(),
        timezone_choices=admin.get_timezone_choices(),
        timezone_labels={
            timezone: admin.format_timezone_label(timezone)
            for timezone in admin.get_timezone_choices()
        },
    )


def admin_edit_user(user_id):
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    user = conn.execute(
        """
        SELECT u.id, u.username, u.is_active, u.timezone, u.email, u.player_id,
               COALESCE(r.name, 'operator') AS role_name
        FROM users u
        LEFT JOIN user_roles ur ON ur.user_id = u.id
        LEFT JOIN roles r ON r.id = ur.role_id
        WHERE u.id = ?
        ORDER BY r.name
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    conn.close()

    if user is None:
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_users", lang=lang))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        role_name = (request.form.get("role_name") or user["role_name"] or "operator").strip()
        timezone_name = (request.form.get("timezone") or "").strip()
        email = (request.form.get("email") or "").strip()
        player_id = request.form.get("player_id") or None
        if player_id is not None:
            try:
                player_id = int(player_id)
            except ValueError:
                player_id = None
        is_active_raw = request.form.get("is_active", "1")
        is_active = 1 if str(is_active_raw).lower() in {"1", "true", "on", "yes"} else 0

        if not username:
            flash(admin.TRANSLATIONS[lang]["user_username_required"])
            return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))
        try:
            timezone_name = admin.validate_timezone(timezone_name)
            email = admin.validate_email_address(email) if email else None
        except ValueError as exc:
            message_key = "invalid_email" if str(exc) == "invalid email" else "invalid_timezone"
            flash(admin.TRANSLATIONS[lang][message_key])
            return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))

        conn = admin.get_db()
        try:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ? AND id != ?",
                (username, user_id),
            ).fetchone()
            if existing is not None:
                flash(admin.TRANSLATIONS[lang]["user_username_taken"])
                return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))
            existing_email = conn.execute(
                "SELECT id FROM users WHERE email = ? AND id != ?",
                (email, user_id),
            ).fetchone() if email else None
            if existing_email is not None:
                flash(admin.TRANSLATIONS[lang]["email_taken"])
                return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))

            if player_id is not None and conn.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone() is None:
                flash(admin.TRANSLATIONS[lang]["error"])
                return redirect(url_for("admin_edit_user", user_id=user_id, lang=lang))
            conn.execute(
                "UPDATE users SET username = ?, is_active = ?, timezone = ?, email = ?, player_id = ? WHERE id = ?",
                (username, is_active, timezone_name, email, player_id, user_id),
            )
            if password:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(password), user_id),
                )

            conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            role = conn.execute("SELECT id FROM roles WHERE name = ?", (role_name,)).fetchone()
            if role is not None:
                conn.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                    (user_id, role["id"]),
                )
            conn.commit()
            admin.log_admin_action(
                "user_updated",
                "user",
                {
                    "target_user_id": user_id,
                    "username": username,
                    "role": role_name,
                    "is_active": is_active,
                    "password_changed": bool(password),
                },
                user_id=session.get("user_id"),
            )
            flash(admin.TRANSLATIONS[lang]["user_updated_success"])
        finally:
            conn.close()
        return redirect(url_for("admin_users", lang=lang))

    return render_template(
        "admin/edit_user.html",
        lang=lang,
        translations=admin.TRANSLATIONS[lang],
        user=user,
        roles=list(admin.ALLOWED_ROLES),
        players=admin.load_players_for_user_link(),
        timezone_choices=admin.get_timezone_choices(),
        timezone_labels={
            timezone: admin.format_timezone_label(timezone)
            for timezone in admin.get_timezone_choices()
        },
    )


def admin_delete_user(user_id):
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        admin.log_admin_action(
            "user_deleted",
            "user",
            {"target_user_id": user_id},
            user_id=session.get("user_id"),
        )
        flash(admin.TRANSLATIONS[lang]["user_deleted_success"])
    finally:
        conn.close()

    return redirect(url_for("admin_users", lang=lang))
