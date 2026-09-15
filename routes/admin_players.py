"""Administrative player, category, and rating routes."""
import json
import math
import sqlite3

from flask import flash, redirect, render_template, request, url_for


def _admin_routes():
    from routes import admin
    return admin


def register_player_routes(admin_bp):
    routes = (
        ("/admin/players", "admin_players", ("GET",)),
        ("/admin/players/edit", "admin_edit_player", ("GET", "POST")),
        ("/admin/players/delete", "admin_delete_player", ("POST",)),
        ("/admin/categories", "admin_categories", ("GET", "POST")),
        ("/admin/ratings", "admin_ratings", ("GET", "POST")),
    )
    for route, endpoint, methods in routes:
        admin_bp.add_url_rule(route, endpoint=endpoint, view_func=globals()[
                              endpoint], methods=list(methods))


def admin_players():
    admin = _admin_routes()
    lang = admin.get_language(request.args.get("lang"))
    category_config = admin.get_category_config()
    page = admin.parse_page_number(request.args.get("page"), default=1)
    page_size = admin.parse_page_size(
        request.args.get("page_size"), default=25)
    sort_key = admin.parse_player_sort(request.args.get("sort"))
    sort_order = admin.parse_player_order(request.args.get("order"))
    filters = {
        "display_name": request.args.get("display_name", "").strip(),
        "glicko_min": request.args.get("glicko_min", "").strip(),
        "glicko_max": request.args.get("glicko_max", "").strip(),
        "last_active": request.args.get("last_active", "").strip(),
        "sort": sort_key,
        "order": sort_order,
        "page": page,
        "page_size": page_size,
    }
    total_count = admin.count_rankings({key: filters[key] for key in (
        "display_name", "glicko_min", "glicko_max", "last_active")})
    page_details = admin.pagination_details(total_count, page, page_size)
    return render_template(
        "admin/players.html", rankings=admin.load_rankings(filters), lang=lang,
        translations=admin.TRANSLATIONS[lang], total_count=total_count,
        sort=sort_key, order=sort_order, category_config=category_config, **page_details,
    )


def admin_edit_player():
    admin = _admin_routes()
    if not admin.admin_required("data_admin"):
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    player_id = request.args.get("id")
    if not player_id:
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_players", lang=lang))
    conn = admin.get_db()
    player = conn.execute(
        "SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    if not player:
        conn.close()
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_players", lang=lang))
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        slug = request.form.get("slug", "").strip()
        initial_rating = request.form.get("initial_rating")
        active = int(request.form.get("active", 1))
        initial_rating_changed = initial_rating != (
            "" if player["initial_rating"] is None else str(player["initial_rating"]))
        conn.execute(
            """
            UPDATE players SET display_name = ?, first_name = ?, last_name = ?, slug = ?,
                initial_rating = ?, country = ?, club = ?, active = ? WHERE id = ?
            """,
            (display_name, first_name, last_name, slug, initial_rating,
             request.form.get("country"), request.form.get("club"), active, player_id),
        )
        conn.commit()
        earliest_match_date = None
        if initial_rating_changed:
            earliest_match_date = conn.execute(
                "SELECT MIN(match_date) FROM matches WHERE white_player_id = ? OR black_player_id = ?",
                (player_id, player_id),
            ).fetchone()[0]
        conn.close()
        if earliest_match_date:
            admin.mark_dirty(earliest_match_date)
            admin.update_from_latest_snapshot()
        admin.log_admin_action("player_updated", "player", {
                               "player_id": player_id, "active": active, "initial_rating_changed": initial_rating_changed}, user_id=admin.session.get("user_id"))
        flash(admin.TRANSLATIONS[lang]["success"])
        return redirect(url_for("admin_players", lang=lang))
    conn.close()
    return render_template("admin/edit_player.html", player=player, lang=lang, translations=admin.TRANSLATIONS[lang])


def admin_delete_player():
    admin = _admin_routes()
    if not admin.admin_required("data_admin"):
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    player_id = request.args.get("id")
    if not player_id:
        flash(admin.TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_players", lang=lang))
    conn = admin.get_db()
    try:
        earliest_match_date = conn.execute(
            "SELECT MIN(match_date) FROM matches WHERE white_player_id = ? OR black_player_id = ?", (player_id, player_id)).fetchone()[0]
        conn.execute(
            "DELETE FROM tournament_pairings WHERE white_player_id = ? OR black_player_id = ?", (player_id, player_id))
        conn.execute(
            "DELETE FROM tournament_round_players WHERE player_id = ?", (player_id,))
        conn.execute(
            "DELETE FROM tournament_participants WHERE player_id = ?", (player_id,))
        conn.execute(
            "DELETE FROM rating_snapshots WHERE player_id = ?", (player_id,))
        conn.execute(
            "DELETE FROM matches WHERE white_player_id = ? OR black_player_id = ?", (player_id, player_id))
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()
    try:
        admin.refresh_stats()
        if earliest_match_date:
            admin.recompute_ratings()
    except sqlite3.DatabaseError as exc:
        admin.logger.exception(
            "Player deletion for %s left the database in a malformed state; restoring a valid backup.", player_id)
        backup_path = admin.get_latest_valid_backup_path()
        if backup_path is not None and admin.restore_db_from_backup(backup_path):
            flash("Database recovered from backup after a malformed SQLite image. Please review the restored data before continuing.")
        else:
            flash(f"{admin.TRANSLATIONS[lang]['error']}: {exc}")
        return redirect(url_for("admin_players", lang=lang))
    flash(admin.TRANSLATIONS[lang]["success"])
    admin.log_admin_action("player_deleted", "player", {
                           "player_id": player_id}, user_id=admin.session.get("user_id"))
    return redirect(url_for("admin_players", lang=lang))


def admin_categories():
    admin = _admin_routes()
    if not admin.admin_required("data_admin"):
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    preview_players = []
    config = admin.get_category_config()
    current_k = config["glicko_k"]
    current_m = config["glicko_m"]
    if request.method == "POST":
        action = request.form.get("action")
        if action == "reset":
            admin.update_category_config(admin.GLICKO_K, admin.GLICKO_M)
            admin.log_admin_action("category_config_reset", "category_config", {
                                   "glicko_k": admin.GLICKO_K, "glicko_m": admin.GLICKO_M}, user_id=admin.session.get("user_id"))
            flash(admin.TRANSLATIONS[lang]["reset_to_default_success"])
            conn.close()
            return redirect(url_for("admin_categories", lang=lang))
        try:
            preview_k = float(request.form.get("glicko_k", current_k))
            preview_m = float(request.form.get("glicko_m", current_m))
        except (TypeError, ValueError):
            flash(
                f"{admin.TRANSLATIONS[lang]['error']}: invalid category parameters")
        else:
            if action == "save":
                try:
                    admin.update_category_config(preview_k, preview_m)
                except ValueError as exc:
                    flash(f"{admin.TRANSLATIONS[lang]['error']}: {exc}")
                else:
                    admin.log_admin_action("category_config_updated", "category_config", {
                                           "glicko_k": preview_k, "glicko_m": preview_m}, user_id=admin.session.get("user_id"))
                    flash(admin.TRANSLATIONS[lang]["success"])
                    current_k, current_m = preview_k, preview_m
            else:
                current_k, current_m = preview_k, preview_m
    players = conn.execute(
        "SELECT * FROM players WHERE games_played > 0 ORDER BY rating DESC LIMIT 5").fetchall()
    players += conn.execute(
        "SELECT * FROM players WHERE active = 1 ORDER BY RANDOM() LIMIT 5").fetchall()
    seen = set()
    for player in players:
        if player["id"] in seen:
            continue
        seen.add(player["id"])
        current_category = admin.glicko_to_category(player["rating"], 1)
        preview_category = admin.glicko_to_category(
            player["rating"], 1, k=current_k, m=int(current_m))
        preview_players.append({"id": player["id"], "display_name": player["display_name"], "rating": player["rating"],
                               "current_category": current_category, "preview_category": preview_category, "changed": current_category != preview_category})
    conn.close()
    return render_template("admin/categories.html", lang=lang, translations=admin.TRANSLATIONS[lang], glicko_k=current_k, glicko_m=current_m, preview_players=preview_players)


def admin_ratings():
    admin = _admin_routes()
    if not admin.admin_required("data_admin"):
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    cfg = admin.get_rating_config()
    lang = admin.get_language(request.args.get("lang"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "reset":
            payload = {"tau": admin.TAU, "default_rating": admin.DEFAULT_RATING,
                       "default_rd": admin.DEFAULT_RD, "default_volatility": admin.DEFAULT_VOLATILITY}
            admin.update_rating_config(**payload)
            admin.log_admin_action(
                "rating_config_reset", "rating_config", payload, user_id=admin.session.get("user_id"))
            flash(admin.TRANSLATIONS[lang]["reset_to_default_success"])
        elif action == "save":
            payload = {"tau": float(request.form["tau"]), "default_rating": float(request.form["default_rating"]), "default_rd": float(
                request.form["default_rd"]), "default_volatility": float(request.form["default_volatility"])}
            admin.update_rating_config(
                payload["tau"], payload["default_rating"], payload["default_rd"], payload["default_volatility"])
            admin.log_admin_action(
                "rating_config_updated", "rating_config", payload, user_id=admin.session.get("user_id"))
            flash(admin.TRANSLATIONS[lang]["success"])
        elif action == "recalculate":
            try:
                admin.recompute_ratings()
                admin.refresh_stats()
                admin.log_admin_action("ratings_recalculated", "ratings", {
                }, user_id=admin.session.get("user_id"))
                flash(admin.TRANSLATIONS[lang]["success"])
            except sqlite3.DatabaseError as exc:
                admin.logger.exception(
                    "Ratings recomputation failed because the SQLite database is malformed. Restoring the newest valid backup.")
                backup_path = admin.get_latest_valid_backup_path()
                if backup_path is not None and admin.restore_db_from_backup(backup_path):
                    restored_conn = sqlite3.connect(admin.DB_PATH)
                    try:
                        admin.refresh_stats(conn=restored_conn)
                    finally:
                        restored_conn.close()
                    flash(
                        "Database recovered from backup after a malformed SQLite image. Please review the restored data before continuing.")
                else:
                    flash(f"{admin.TRANSLATIONS[lang]['error']}: {exc}")
        elif action == "update":
            admin.update_from_latest_snapshot()
            admin.refresh_stats()
            flash(admin.TRANSLATIONS[lang]["success"])
        return redirect(url_for("admin_ratings", lang=lang))
    conn = admin.get_db()
    player_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    snapshot_count = conn.execute(
        "SELECT COUNT(*) FROM rating_snapshots").fetchone()[0]
    conn.close()
    return render_template("admin/ratings.html", config=admin.get_rating_config(), dirty_date=admin.get_dirty_date(), player_count=player_count, match_count=match_count, snapshot_count=snapshot_count, lang=lang, translations=admin.TRANSLATIONS[lang])
