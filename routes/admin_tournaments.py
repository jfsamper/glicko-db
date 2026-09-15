"""Tournament index and creation routes for the admin blueprint."""
import os
import math
import sqlite3

from flask import Response, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from services.i18n import TRANSLATIONS


def _admin_routes():
    from routes import admin

    return admin


def register_tournament_routes(admin_bp):
    admin_bp.add_url_rule(
        "/admin/tournaments",
        endpoint="admin_tournaments",
        view_func=admin_tournaments,
        methods=["GET", "POST"],
    )


def register_tournament_detail_routes(admin_bp):
    routes = (
        ("/admin/tournaments/<int:tournament_id>/delete",
         "admin_delete_tournament", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/status",
         "admin_update_tournament_status", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/settings",
         "admin_update_tournament_settings", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/settings",
         "admin_tournament_settings", ("GET",)),
        ("/admin/tournaments/<int:tournament_id>/pending-player-resolve",
         "admin_resolve_pending_player", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>", "admin_tournament", ("GET",)),
        ("/admin/tournaments/<int:tournament_id>/export",
         "admin_export_tournament_results", ("GET",)),
        ("/admin/tournaments/<int:tournament_id>/players",
         "admin_tournament_players", ("GET",)),
        ("/admin/tournaments/<int:tournament_id>/participants/add",
         "admin_add_tournament_participant", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/players/create",
         "admin_create_tournament_player", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/pending-player/delete",
         "admin_delete_pending_player", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/participants/remove",
         "admin_remove_tournament_participant", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/pairing-handicap",
         "admin_update_pairing_handicap", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/pair",
         "admin_manual_pair", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/pair-selected",
         "admin_pair_selected_players", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/pairing-edit",
         "admin_edit_pairing", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/unpair", "admin_unpair", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/unpair-all",
         "admin_unpair_all", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/result",
         "admin_set_tournament_result", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/generate",
         "admin_generate_tournament_round", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/save",
         "admin_save_tournament", ("POST",)),
        ("/admin/tournaments/<int:tournament_id>/process-round",
         "admin_process_tournament_round", ("POST",)),
    )
    handler_map = {
        "admin_delete_tournament": admin_delete_tournament,
        "admin_update_tournament_status": admin_update_tournament_status,
        "admin_update_tournament_settings": admin_update_tournament_settings,
        "admin_tournament_settings": admin_tournament_settings,
        "admin_resolve_pending_player": admin_resolve_pending_player,
        "admin_export_tournament_results": admin_export_tournament_results,
        "admin_tournament": admin_tournament,
        "admin_create_tournament_player": admin_create_tournament_player,
        "admin_tournament_players": admin_tournament_players,
        "admin_add_tournament_participant": admin_add_tournament_participant,
        "admin_delete_pending_player": admin_delete_pending_player,
        "admin_remove_tournament_participant": admin_remove_tournament_participant,
        "admin_update_pairing_handicap": admin_update_pairing_handicap,
        "admin_manual_pair": admin_manual_pair,
        "admin_pair_selected_players": admin_pair_selected_players,
        "admin_edit_pairing": admin_edit_pairing,
        "admin_unpair": admin_unpair,
        "admin_unpair_all": admin_unpair_all,
        "admin_set_tournament_result": admin_set_tournament_result,
        "admin_generate_tournament_round": admin_generate_tournament_round,
        "admin_save_tournament": admin_save_tournament,
        "admin_process_tournament_round": admin_process_tournament_round,
    }
    for route, endpoint, methods in routes:
        admin_bp.add_url_rule(
            route,
            endpoint=endpoint,
            view_func=handler_map[endpoint],
            methods=list(methods),
        )


def admin_update_tournament_status(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))

    lang = admin.get_language(request.args.get("lang"))
    status = request.form.get("status", "").strip()
    if status not in admin.TOURNAMENT_STATUSES:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournaments", lang=lang))

    conn = admin.get_db()
    try:
        tournament_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
        }
        if "handicap_enabled" not in tournament_columns:
            conn.execute(
                "ALTER TABLE tournaments ADD COLUMN handicap_enabled INTEGER NOT NULL DEFAULT 0"
            )
        updated = conn.execute(
            "UPDATE tournaments SET status = ? WHERE id = ?",
            (status, tournament_id),
        ).rowcount
        if not updated:
            flash(TRANSLATIONS[lang]["error"])
        else:
            conn.commit()
            admin.log_admin_action(
                "tournament_status_updated",
                "tournament",
                {"tournament_id": tournament_id, "status": status},
                user_id=admin.session.get("user_id"),
            )
            flash(TRANSLATIONS[lang]["success"])
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        admin.logger.exception(
            "Tournament settings update failed for %s", tournament_id)
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return admin.redirect_or_json(
        url_for("admin_tournament", tournament_id=tournament_id, lang=lang)
    )


def admin_delete_tournament(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))

    def action(conn):
        admin.delete_tournament(conn, tournament_id)
        admin.log_admin_action(
            "tournament_deleted",
            "tournament",
            {"tournament_id": tournament_id},
            user_id=admin.session.get("user_id"),
        )
    admin.run_admin_db_action(action, lang, TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_tournaments", lang=lang))


def admin_tournament_settings(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    tournament = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    conn.close()
    if tournament is None:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournaments", lang=lang))
    return render_template(
        "admin/tournament_settings.html",
        tournament=tournament,
        acceleration_categories=admin.acceleration_category_settings(
            tournament["acceleration_scheme"] if "acceleration_scheme" in tournament.keys(
            ) else None
        ),
        acceleration_scheme_options=admin.ACCELERATION_SCHEMES,
        acceleration_scheme_choice=admin.acceleration_scheme_choice(
            tournament["acceleration_scheme"] if "acceleration_scheme" in tournament.keys(
            ) else None
        ),
        acceleration_rounds=(
            tournament["acceleration_rounds"]
            if "acceleration_rounds" in tournament.keys() and tournament["acceleration_rounds"] is not None
            else admin.default_acceleration_rounds(tournament["rounds"])
        ),
        category_rounds=(
            tournament["category_rounds"]
            if "category_rounds" in tournament.keys()
            else admin.DEFAULT_CATEGORY_ROUNDS
        ),
        lang=lang,
        translations=TRANSLATIONS[lang],
    )


def admin_update_tournament_settings(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(
            url_for("admin_login", lang=admin.get_language(
                request.args.get("lang")))
        )
    lang = admin.get_language(request.args.get("lang"))
    name = request.form.get("name", "").strip()
    location = request.form.get("location", "").strip()
    description = request.form.get("description")
    begin_date = request.form.get("begin_date")
    end_date = request.form.get("end_date")
    rounds = admin.normalize_tournament_rounds(
        request.form.get("rounds", 1, type=int))
    bye_points = request.form.get("bye_points", type=float)
    absent_points = request.form.get("absent_points", type=float)
    handicap_enabled = request.form.get("handicap_enabled") == "1"
    apply_auto_handicap = request.form.get("apply_auto_handicap") == "1"
    number_of_categories = request.form.get("number_of_categories")
    category_floors = request.form.getlist("category_floor")
    acceleration_rounds = request.form.get("acceleration_rounds")
    category_rounds = request.form.get("category_rounds")
    mm_bar = request.form.get("mm_bar")
    mm_floor = request.form.get("mm_floor")
    mm_zero = request.form.get("mm_zero")

    if not name or bye_points not in {0.0, 0.5, 1.0} or absent_points not in {0.0, 0.5, 1.0}:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

    conn = admin.get_db()
    tournament_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
    }
    optional_columns = [
        column for column in (
            "description", "begin_date", "end_date", "acceleration_scheme", "acceleration_rounds", "category_rounds",
            "mm_bar", "mm_floor", "mm_zero"
        ) if column in tournament_columns
    ]
    optional_select = f", {', '.join(optional_columns)}" if optional_columns else ""
    try:
        current_tournament = conn.execute(
            f"SELECT pairing_system, tournament_type{optional_select} FROM tournaments WHERE id = ?",
            (tournament_id,),
        ).fetchone()
        if current_tournament is None:
            flash(TRANSLATIONS[lang]["error"])
            return redirect(url_for("admin_tournaments", lang=lang))

        if begin_date is None:
            begin_date = current_tournament["begin_date"] if "begin_date" in current_tournament.keys(
            ) else ""
        if end_date is None:
            end_date = current_tournament["end_date"] if "end_date" in current_tournament.keys(
            ) else ""
        if description is None:
            description = current_tournament["description"] if "description" in current_tournament.keys(
            ) else ""
        try:
            begin_date = admin.parse_date_value(
                begin_date) if begin_date else None
            end_date = admin.parse_date_value(end_date) if end_date else None
            if begin_date and end_date and begin_date > end_date:
                raise ValueError
        except ValueError:
            flash(TRANSLATIONS[lang]["error"])
            return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

        pairing_system = admin.normalize_tournament_system(
            current_tournament["pairing_system"])
        acceleration_scheme = (
            current_tournament["acceleration_scheme"]
            if "acceleration_scheme" in tournament_columns
            else admin.DEFAULT_ACCELERATION_SCHEME
        ) or admin.DEFAULT_ACCELERATION_SCHEME
        if pairing_system == "accelerated_swiss":
            try:
                acceleration_scheme = admin.acceleration_scheme_from_form(
                    request.form)
            except (TypeError, ValueError):
                flash(TRANSLATIONS[lang]["error"])
                return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))
        if pairing_system == "mcmahon":
            try:
                mm_bar_value, mm_floor_value, mm_zero_value = admin.validate_mcmahon_settings(
                    mm_bar, mm_floor, mm_zero)
            except (TypeError, ValueError):
                flash(TRANSLATIONS[lang]["error"])
                return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

        try:
            acceleration_rounds = (
                admin.default_acceleration_rounds(rounds)
                if acceleration_rounds in (None, "")
                else int(acceleration_rounds)
            )
            category_rounds = (
                admin.DEFAULT_CATEGORY_ROUNDS
                if category_rounds in (None, "")
                else int(category_rounds)
            )
            if acceleration_rounds < 0 or category_rounds < 0 or acceleration_rounds > rounds or category_rounds > rounds:
                raise ValueError
        except (TypeError, ValueError):
            flash(TRANSLATIONS[lang]["error"])
            return redirect(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))

        update_fields = [
            "name = ?", "location = ?", "rounds = ?", "bye_points = ?",
            "absent_points = ?", "handicap_enabled = ?", "tournament_type = ?", "pairing_system = ?",
        ]
        update_values = [name, location, rounds, bye_points, absent_points, int(
            handicap_enabled), pairing_system, pairing_system]
        if "begin_date" in tournament_columns:
            update_fields.append("begin_date = ?")
            update_values.append(begin_date)
        if "end_date" in tournament_columns:
            update_fields.append("end_date = ?")
            update_values.append(end_date)
        if "description" in tournament_columns:
            update_fields.append("description = ?")
            update_values.append(description)
        if pairing_system == "accelerated_swiss" and "acceleration_scheme" in tournament_columns:
            update_fields.append("acceleration_scheme = ?")
            update_values.append(acceleration_scheme)
        if pairing_system == "accelerated_swiss" and "acceleration_rounds" in tournament_columns:
            update_fields.append("acceleration_rounds = ?")
            update_values.append(acceleration_rounds)
        if pairing_system == "swiss_cat" and "category_rounds" in tournament_columns:
            update_fields.append("category_rounds = ?")
            update_values.append(category_rounds)
        if pairing_system == "mcmahon" and {"mm_bar", "mm_floor", "mm_zero"}.issubset(tournament_columns):
            update_fields.extend(["mm_bar = ?", "mm_floor = ?", "mm_zero = ?"])
            update_values.extend([mm_bar_value, mm_floor_value, mm_zero_value])
        update_values.append(tournament_id)
        updated = conn.execute(
            f"UPDATE tournaments SET {', '.join(update_fields)} WHERE id = ?",
            update_values,
        ).rowcount
        if not updated:
            flash(TRANSLATIONS[lang]["error"])
        else:
            has_matches = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'matches'"
            ).fetchone() is not None
            old_match_dates = []
            if has_matches:
                old_match_dates = conn.execute(
                    """
                    SELECT m.match_date
                    FROM matches m
                    JOIN tournament_pairings p ON p.id = m.tournament_pairing_id
                    JOIN tournament_rounds r ON r.id = p.round_id
                    WHERE r.tournament_id = ?
                    """,
                    (tournament_id,),
                ).fetchall()
            admin.update_tournament_handicaps(
                conn,
                tournament_id,
                handicap_enabled,
                apply_auto_handicap=apply_auto_handicap,
            )
            updated_matches = admin.sync_tournament_matches(
                conn,
                tournament_id,
                name=name,
                match_date=begin_date,
            )
            if pairing_system == "mcmahon":
                participant_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(tournament_participants)").fetchall()
                }
                if "mc_seeds_calculated" in participant_columns:
                    conn.execute(
                        "UPDATE tournament_participants SET mc_seeds_calculated = 0 WHERE tournament_id = ?",
                        (tournament_id,),
                    )
                admin._recalculate_mcmahon_seeds(conn, tournament_id)
            conn.commit()
            if updated_matches and begin_date:
                for row in old_match_dates:
                    admin.mark_dirty(row["match_date"])
                admin.mark_dirty(begin_date)
                admin.refresh_stats()
            admin.log_admin_action(
                "tournament_settings_updated",
                "tournament",
                {"tournament_id": tournament_id, "name": name, "rounds": rounds},
                user_id=admin.session.get("user_id"),
            )
            flash(TRANSLATIONS[lang]["success"])
    finally:
        conn.close()
    return admin.redirect_or_json(url_for("admin_tournament_settings", tournament_id=tournament_id, lang=lang))


def admin_resolve_pending_player(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    pending_id = request.form.get("pending_id", type=int)
    resolved_player_id = request.form.get("resolved_player_id", type=int)
    conn = admin.get_db()
    try:
        if pending_id is None:
            raise ValueError("Pending player not found")
        if resolved_player_id is not None:
            player_row = conn.execute(
                "SELECT display_name FROM players WHERE id = ?", (
                    resolved_player_id,)
            ).fetchone()
            if player_row is None:
                raise ValueError("Resolved player not found")
            updated = conn.execute(
                """
                UPDATE tournament_pending_players
                SET resolved_player_id = ?, display_name = ?
                WHERE tournament_id = ? AND id = ?
                """,
                (resolved_player_id,
                 player_row["display_name"], tournament_id, pending_id),
            ).rowcount
        else:
            updated = conn.execute(
                """
                UPDATE tournament_pending_players
                SET resolved_player_id = ?
                WHERE tournament_id = ? AND id = ?
                """,
                (resolved_player_id, tournament_id, pending_id),
            ).rowcount
        if not updated:
            raise ValueError("Pending player not found")
        if not admin._materialize_pending_players(conn, tournament_id, pending_id=pending_id):
            raise ValueError("Pending player could not be materialized")
        conn.commit()
        admin.log_admin_action(
            "pending_player_resolved",
            "tournament_pending_player",
            {"tournament_id": tournament_id, "pending_id": pending_id,
                "resolved_player_id": resolved_player_id},
            user_id=admin.session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except (ValueError, sqlite3.DatabaseError) as exc:
        conn.rollback()
        admin.logger.warning("Pending player resolution failed: %s", exc)
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))


def admin_export_tournament_results(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        xml_text = admin.export_tournament_results(conn, tournament_id)
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
        conn.close()
        return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
    conn.close()
    return Response(
        xml_text,
        content_type="application/xml; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=tournament_{tournament_id}.xml"},
    )


def admin_tournament(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    tournament = conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
    if tournament is None:
        conn.close()
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournaments", lang=lang))

    selected_round_id = request.args.get("round_id", type=int)
    if selected_round_id is None:
        selected_round = conn.execute(
            "SELECT id FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC LIMIT 1",
            (tournament_id,),
        ).fetchone()
        selected_round_id = selected_round["id"] if selected_round else None
    pairings = conn.execute(
        """
        SELECT p.id, r.id AS round_id, r.round_number, r.status AS round_status, p.board_number,
               p.white_player_id, p.black_player_id, p.is_bye, p.result,
               COALESCE(white.display_name, p.white_player_name) AS white_name,
               COALESCE(black.display_name, p.black_player_name) AS black_name
        FROM tournament_rounds r
        JOIN tournament_pairings p ON p.round_id = r.id
        LEFT JOIN players white ON white.id = p.white_player_id
        LEFT JOIN players black ON black.id = p.black_player_id
        WHERE r.tournament_id = ?
        ORDER BY r.round_number DESC, p.board_number
        """,
        (tournament_id,),
    ).fetchall()
    selected_pairings = [
        row for row in pairings if row["round_id"] == selected_round_id]
    round_statuses = {
        row["player_id"]: row["status"]
        for row in conn.execute(
            "SELECT player_id, status FROM tournament_round_players WHERE round_id = ?",
            (selected_round_id,),
        ).fetchall()
    } if selected_round_id else {}
    all_participants = admin.list_tournament_participants(conn, tournament_id)
    participants = [row for row in all_participants if not row["is_pending"]]
    paired_player_ids = {
        player_id
        for pairing in selected_pairings
        for player_id in (pairing["white_player_id"], pairing["black_player_id"])
        if player_id is not None
    }
    available_players = conn.execute(
        """
        SELECT p.id, p.display_name, p.rating
        FROM players p
        WHERE p.active = 1
          AND NOT EXISTS (SELECT 1 FROM tournament_participants tp
                          WHERE tp.tournament_id = ? AND tp.player_id = p.id)
        ORDER BY p.display_name
        """,
        (tournament_id,),
    ).fetchall()
    rounds = conn.execute(
        "SELECT id, round_number FROM tournament_rounds WHERE tournament_id = ? ORDER BY round_number DESC",
        (tournament_id,),
    ).fetchall()
    round_status_player_ids = set(round_statuses)
    unpaired_players = [
        row for row in participants
        if row["player_id"] not in paired_player_ids and row["player_id"] not in round_status_player_ids
    ]
    absent_players = [row for row in participants if round_statuses.get(
        row["player_id"]) == "absent"]
    pending_rows = conn.execute(
        "SELECT * FROM tournament_pending_players WHERE tournament_id = ? ORDER BY rank, display_name",
        (tournament_id,),
    ).fetchall()
    suggested_player_ids = {row["display_name"]: row["id"]
                            for row in available_players}
    pending_players = []
    for row in pending_rows:
        pending = dict(row)
        pending["suggested_player_id"] = suggested_player_ids.get(
            pending.get("suggested_name"))
        pending_players.append(pending)
    standings = admin.get_tournament_standings(conn, tournament_id)
    conn.close()
    return render_template(
        "admin/tournament.html",
        tournament=tournament,
        pairings=pairings,
        selected_pairings=selected_pairings,
        selected_round_id=selected_round_id,
        participant_count=len(all_participants),
        participants=participants,
        available_players=available_players,
        unpaired_players=unpaired_players,
        absent_players=absent_players,
        round_statuses=round_statuses,
        rounds=rounds,
        standings=standings,
        pending_players=pending_players,
        lang=lang,
        translations=TRANSLATIONS[lang],
    )


def admin_tournament_players(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    tournament = conn.execute(
        "SELECT id, name FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()
    if tournament is None:
        conn.close()
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_tournaments", lang=lang))
    participants = admin.list_tournament_participants(conn, tournament_id)
    available_players = conn.execute(
        """
        SELECT p.id, p.display_name, p.rating
        FROM players p
        WHERE p.active = 1
          AND NOT EXISTS (
              SELECT 1 FROM tournament_participants tp
              WHERE tp.tournament_id = ? AND tp.player_id = p.id
          )
        ORDER BY p.display_name
        """,
        (tournament_id,),
    ).fetchall()
    category_config = admin.get_category_config(conn=conn)
    rank_options = [
        {
            "label": f"{rank_value + 1} dan" if rank_value >= 0 else f"{-rank_value} kyu",
            "glicko": round(category_config["glicko_m"] * math.exp((rank_value + 29) / category_config["glicko_k"])),
        }
        for rank_value in range(8, -31, -1)
    ]
    conn.close()
    return render_template(
        "admin/tournament_players.html",
        tournament=tournament,
        participants=participants,
        available_players=available_players,
        rank_options=rank_options,
        lang=lang,
        translations=TRANSLATIONS[lang],
    )


def admin_create_tournament_player(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    if not first_name or not last_name:
        flash(
            f"{TRANSLATIONS[lang]['error']}: {TRANSLATIONS[lang]['player_name_required']}")
        return redirect(url_for("admin_tournament_players", tournament_id=tournament_id, lang=lang))

    display_name = f"{first_name} {last_name}"
    selected_glicko = request.form.get("glicko", "").strip()
    category_config = admin.get_category_config()
    valid_glickos = {
        round(category_config["glicko_m"] *
              math.exp((rank_value + 29) / category_config["glicko_k"]))
        for rank_value in range(8, -31, -1)
    }
    try:
        if not selected_glicko or float(selected_glicko) not in valid_glickos:
            raise ValueError
        glicko = float(selected_glicko)
    except ValueError:
        flash(
            f"{TRANSLATIONS[lang]['error']}: {TRANSLATIONS[lang]['invalid_rating']}")
        return redirect(url_for("admin_tournament_players", tournament_id=tournament_id, lang=lang))

    conn = admin.get_db()
    try:
        existing_player = admin._player_lookup(
            conn).get(admin.normalize_key(display_name))
        if existing_player is not None:
            raise ValueError(TRANSLATIONS[lang]["player_already_exists"].format(
                name=existing_player["display_name"]))
        similar_player = admin._suggest_player_name(display_name, conn)
        if similar_player:
            raise ValueError(
                TRANSLATIONS[lang]["similar_player_exists"].format(name=similar_player))
        if conn.execute("SELECT 1 FROM tournaments WHERE id = ?", (tournament_id,)).fetchone() is None:
            raise ValueError("Tournament not found")
        rank = conn.execute(
            "SELECT COALESCE(MAX(rank), 0) + 1 FROM tournament_pending_players WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()[0]
        pending_columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(tournament_pending_players)").fetchall()}
        pending_values = (tournament_id, display_name, glicko,
                          rank, f"manual:{first_name}:{last_name}")
        if "created_at" in pending_columns:
            conn.execute(
                """
                INSERT INTO tournament_pending_players
                    (tournament_id, display_name, rating, rank, source_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                pending_values + (admin.current_timestamp(),),
            )
        else:
            conn.execute(
                """
                INSERT INTO tournament_pending_players
                    (tournament_id, display_name, rating, rank, source_key)
                VALUES (?, ?, ?, ?, ?)
                """,
                pending_values,
            )
        conn.commit()
        admin.log_admin_action(
            "tournament_pending_player_created",
            "tournament_pending_player",
            {"tournament_id": tournament_id,
                "display_name": display_name, "glicko": glicko},
            user_id=admin.session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["pending_player_created"])
    except (ValueError, sqlite3.IntegrityError) as exc:
        conn.rollback()
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return redirect(url_for("admin_tournament_players", tournament_id=tournament_id, lang=lang))


def admin_add_tournament_participant(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))

    def action(conn):
        player_id = request.form.get("player_id", type=int)
        admin.add_participant(conn, tournament_id, player_id)
        admin.log_admin_action(
            "tournament_participant_added",
            "tournament",
            {"tournament_id": tournament_id, "player_id": player_id},
            user_id=admin.session.get("user_id"),
        )
    admin.run_admin_db_action(action, lang, TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_tournament_players", tournament_id=tournament_id, lang=lang))


def admin_delete_pending_player(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    pending_id = request.form.get("pending_id", type=int)
    conn = admin.get_db()
    try:
        deleted = conn.execute(
            "DELETE FROM tournament_pending_players WHERE tournament_id = ? AND id = ?",
            (tournament_id, pending_id),
        ).rowcount
        if not deleted:
            raise ValueError("Pending player not found")
        conn.commit()
        admin.log_admin_action(
            "tournament_pending_player_deleted",
            "tournament_pending_player",
            {"tournament_id": tournament_id, "pending_id": pending_id},
            user_id=admin.session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["pending_player_deleted"])
    except (ValueError, sqlite3.DatabaseError) as exc:
        conn.rollback()
        admin.logger.warning("Pending player deletion failed: %s", exc)
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    destination = "admin_tournament_players" if request.form.get(
        "return_to") == "players" else "admin_tournament"
    return redirect(url_for(destination, tournament_id=tournament_id, lang=lang))


def admin_remove_tournament_participant(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))

    def action(conn):
        player_id = request.form.get("player_id", type=int)
        admin.remove_participant(conn, tournament_id, player_id)
        admin.log_admin_action(
            "tournament_participant_removed",
            "tournament",
            {"tournament_id": tournament_id, "player_id": player_id},
            user_id=admin.session.get("user_id"),
        )
    admin.run_admin_db_action(action, lang, TRANSLATIONS[lang]["success"])
    return redirect(
        url_for(
            "admin_tournament",
            tournament_id=tournament_id,
            lang=lang,
            round_id=request.form.get("round_id", type=int),
        )
    )


def admin_update_pairing_handicap(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))

    def action(conn):
        pairing_id = request.form.get("pairing_id", type=int)
        handicap_stones = request.form.get("handicap_stones", type=int)
        admin.update_pairing_handicap(
            conn, tournament_id, pairing_id, handicap_stones)
        admin.log_admin_action(
            "tournament_pairing_handicap_updated",
            "tournament_pairing",
            {"tournament_id": tournament_id, "pairing_id": pairing_id,
                "handicap_stones": handicap_stones},
            user_id=admin.session.get("user_id"),
        )
    admin.run_admin_db_action(action, lang, TRANSLATIONS[lang]["success"])
    return admin.redirect_or_json(
        url_for("admin_tournament", tournament_id=tournament_id,
                lang=lang, round_id=request.form.get("round_id", type=int))
    )


def admin_manual_pair(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))

    def action(conn):
        round_id = request.form.get("round_id", type=int)
        white_player_id = request.form.get("white_player_id", type=int)
        black_player_id = request.form.get("black_player_id", type=int)
        raw_handicap = request.form.get("handicap_stones", "").strip()
        handicap_stones = int(raw_handicap) if raw_handicap else None
        admin.manual_pair(
            conn, tournament_id, round_id, white_player_id, black_player_id,
            handicap_stones=handicap_stones,
        )
        admin.log_admin_action(
            "tournament_pairing_created",
            "tournament_pairing",
            {"tournament_id": tournament_id, "round_id": round_id,
                "white_player_id": white_player_id, "black_player_id": black_player_id},
            user_id=admin.session.get("user_id"),
        )
    admin.run_admin_db_action(action, lang, TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=request.form.get("round_id", type=int)))


def admin_pair_selected_players(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))

    def action(conn):
        round_id = request.form.get("round_id", type=int)
        player_ids = request.form.getlist("player_ids")
        admin.pair_selected_players(conn, tournament_id, round_id, player_ids)
        admin.log_admin_action(
            "tournament_pairings_created",
            "tournament_round",
            {"tournament_id": tournament_id, "round_id": round_id,
                "player_count": len(player_ids)},
            user_id=admin.session.get("user_id"),
        )
    admin.run_admin_db_action(action, lang, TRANSLATIONS[lang]["success"])
    return admin.redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=request.form.get("round_id", type=int)))


def admin_edit_pairing(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    pairing_id = request.form.get("pairing_id", type=int)
    round_id = request.form.get("round_id", type=int)
    previous_dates = conn.execute(
        "SELECT match_date FROM matches WHERE tournament_pairing_id = ?",
        (pairing_id,),
    ).fetchall()
    try:
        admin.update_pairing(
            conn,
            tournament_id,
            pairing_id,
            request.form.get("white_player_id", type=int),
            request.form.get("black_player_id", type=int),
        )
        admin.log_admin_action(
            "tournament_pairing_updated",
            "tournament_pairing",
            {"tournament_id": tournament_id, "pairing_id": pairing_id},
            user_id=admin.session.get("user_id"),
        )
        admin.refresh_stats()
        current_dates = conn.execute(
            "SELECT match_date FROM matches WHERE tournament_pairing_id = ?",
            (pairing_id,),
        ).fetchall()
        for row in previous_dates + current_dates:
            admin.mark_dirty(row["match_date"])
        admin.update_from_latest_snapshot()
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        conn.rollback()
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return admin.redirect_or_json(
        url_for("admin_tournament", tournament_id=tournament_id,
                lang=lang, round_id=round_id)
    )


def admin_unpair(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    pairing_id = request.form.get("pairing_id", type=int)
    previous_dates = conn.execute(
        "SELECT match_date FROM matches WHERE tournament_pairing_id = ?",
        (pairing_id,),
    ).fetchall()
    try:
        admin.unpair(conn, tournament_id, pairing_id)
        admin.log_admin_action(
            "tournament_pairing_removed",
            "tournament_pairing",
            {"tournament_id": tournament_id, "pairing_id": pairing_id},
            user_id=admin.session.get("user_id"),
        )
        admin.refresh_stats()
        for row in previous_dates:
            admin.mark_dirty(row["match_date"])
        admin.update_from_latest_snapshot()
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return admin.redirect_or_json(
        url_for(
            "admin_tournament",
            tournament_id=tournament_id,
            lang=lang,
            round_id=request.form.get("round_id", type=int),
        )
    )


def admin_unpair_all(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    round_id = request.form.get("round_id", type=int)
    conn = admin.get_db()
    try:
        pairing_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM tournament_pairings WHERE round_id = ?",
                (round_id,),
            ).fetchall()
        ]
        previous_dates = conn.execute(
            "SELECT match_date FROM matches WHERE tournament_pairing_id IN (SELECT id FROM tournament_pairings WHERE round_id = ?)",
            (round_id,),
        ).fetchall()
        removed = admin.unpair_all(conn, tournament_id, round_id)
        admin.log_admin_action(
            "tournament_round_pairings_removed",
            "tournament_round",
            {"tournament_id": tournament_id,
                "round_id": round_id, "pairing_ids": pairing_ids},
            user_id=admin.session.get("user_id"),
        )
        if removed:
            admin.refresh_stats()
            for row in previous_dates:
                admin.mark_dirty(row["match_date"])
            admin.update_from_latest_snapshot()
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return admin.redirect_or_json(
        url_for(
            "admin_tournament",
            tournament_id=tournament_id,
            lang=lang,
            round_id=round_id,
        )
    )


def admin_set_tournament_result(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    pairing_id = request.form.get("pairing_id", type=int)
    previous_dates = conn.execute(
        "SELECT match_date FROM matches WHERE tournament_pairing_id = ?", (
            pairing_id,)
    ).fetchall()
    try:
        admin.set_pairing_result(
            conn, tournament_id, pairing_id, request.form.get("result", ""))
        current_dates = conn.execute(
            "SELECT match_date FROM matches WHERE tournament_pairing_id = ?", (
                pairing_id,)
        ).fetchall()
        admin.refresh_stats()
        for row in previous_dates + current_dates:
            admin.mark_dirty(row["match_date"])
        admin.update_from_latest_snapshot()
        admin.log_admin_action(
            "tournament_result_updated",
            "tournament_pairing",
            {"tournament_id": tournament_id, "pairing_id": pairing_id},
            user_id=admin.session.get("user_id"),
        )
        flash(TRANSLATIONS[lang]["success"])
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return admin.redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=request.form.get("round_id", type=int)))


def admin_generate_tournament_round(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))

    def action(conn):
        admin.generate_next_round(conn, tournament_id)
        admin.log_admin_action(
            "tournament_round_generated", "tournament", {
                "tournament_id": tournament_id},
            user_id=admin.session.get("user_id"),
        )
    admin.run_admin_db_action(action, lang, TRANSLATIONS[lang]["success"])
    return admin.redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))


def admin_save_tournament(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        saved = admin.save_tournament_matches(conn, tournament_id)
        conn.commit()
        if saved:
            admin.refresh_stats()
            earliest = conn.execute(
                """
                SELECT MIN(m.match_date)
                FROM matches m
                JOIN tournament_pairings p ON p.id = m.tournament_pairing_id
                JOIN tournament_rounds r ON r.id = p.round_id
                WHERE r.tournament_id = ?
                """,
                (tournament_id,),
            ).fetchone()[0]
            if earliest:
                admin.mark_dirty(earliest)
                admin.update_from_latest_snapshot()
        admin.log_admin_action(
            "tournament_saved", "tournament",
            {"tournament_id": tournament_id, "matches_saved": saved},
            user_id=admin.session.get("user_id"),
        )
        flash(f"{TRANSLATIONS[lang]['success']} ({saved} matches)")
    except ValueError as exc:
        conn.rollback()
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        admin.logger.exception("Tournament save failed for %s", tournament_id)
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    finally:
        conn.close()
    return admin.redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))


def admin_process_tournament_round(tournament_id):
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    round_id = request.form.get("round_id", type=int)
    match_date = request.form.get("match_date", "").strip() or None
    event = request.form.get("event", "").strip() or None
    try:
        inserted = admin.process_tournament_round_matches(
            conn, tournament_id, round_id=round_id, match_date=match_date, event=event
        )
        conn.commit()
        admin.log_admin_action(
            "tournament_round_processed", "tournament_round",
            {"tournament_id": tournament_id,
                "round_id": round_id, "matches": inserted},
            user_id=admin.session.get("user_id"),
        )
        if inserted:
            admin.refresh_stats(conn)
            earliest = conn.execute(
                """
                SELECT MIN(match_date) FROM matches
                WHERE white_player_id IN (SELECT white_player_id FROM tournament_pairings WHERE round_id = ?)
                   OR black_player_id IN (SELECT black_player_id FROM tournament_pairings WHERE round_id = ?)
                """,
                (round_id, round_id),
            ).fetchone()[0]
            if earliest:
                admin.mark_dirty(earliest)
                admin.update_from_latest_snapshot()
        flash(f"{TRANSLATIONS[lang]['success']} ({inserted} matches)")
    except ValueError as exc:
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        flash(f"DatabaseError: {exc}")
    finally:
        conn.close()
    return admin.redirect_or_json(url_for("admin_tournament", tournament_id=tournament_id, lang=lang, round_id=round_id))


def admin_tournaments():
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))

    lang = admin.get_language(request.args.get("lang"))
    translations = TRANSLATIONS[lang]
    conn = admin.get_db()

    if request.method == "POST":
        action = request.form.get("action")
        pairing_system = admin.normalize_tournament_system(
            request.form.get("tournament_type")
            or request.form.get("pairing_system", "swiss")
        )
        if action == "import_opengotha":
            file = request.files.get("file")
            if not file or not (file.filename or "").lower().endswith(".xml"):
                flash(translations["no_file"])
            else:
                filename = secure_filename(file.filename or "")
                upload_path = os.path.join(admin.BASE_DIR, "uploads", filename)
                file.save(upload_path)
                try:
                    tournament_id, metadata, matched = admin.create_tournament_from_gotha(
                        conn, upload_path)
                    admin.log_admin_action(
                        "tournament_imported",
                        "tournament",
                        {"tournament_id": tournament_id,
                            "filename": filename, "matched_players": matched},
                        user_id=admin.session.get("user_id"),
                    )
                    flash(f"{translations['success']} ({matched} players)")
                    conn.close()
                    return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
                except (OSError, ValueError) as exc:
                    flash(f"{translations['error']}: {exc}")
                except sqlite3.DatabaseError as exc:
                    conn.rollback()
                    admin.logger.exception(
                        "OpenGotha tournament import failed for %s", upload_path)
                    flash(f"{translations['error']}: {exc}")
                    conn.close()
                    return render_template(
                        "admin/tournaments.html",
                        tournaments=[],
                        systems=admin.SUPPORTED_SYSTEMS,
                        lang=lang,
                        translations=translations,
                    )
        elif action == "create" and pairing_system in admin.SUPPORTED_SYSTEMS:
            name = request.form.get("name", "").strip()
            if not name:
                flash(translations["error"])
            else:
                rounds = admin.normalize_tournament_rounds(
                    request.form.get("rounds", 1, type=int))
                bye_points = request.form.get("bye_points", 1.0, type=float)
                absent_points = request.form.get(
                    "absent_points", 0.0, type=float)
                handicap_enabled = 1 if request.form.get(
                    "handicap_enabled") == "1" else 0
                try:
                    acceleration_scheme = (
                        admin.acceleration_scheme_from_form(request.form)
                        if pairing_system == "accelerated_swiss"
                        else admin.DEFAULT_ACCELERATION_SCHEME
                    )
                except (TypeError, ValueError):
                    acceleration_scheme = None
                if (
                    bye_points not in {0.0, 0.5, 1.0}
                    or absent_points not in {0.0, 0.5, 1.0}
                    or acceleration_scheme is None
                ):
                    flash(translations["error"])
                    bye_points = absent_points = None
                if bye_points is None:
                    tournaments = conn.execute(
                        "SELECT * FROM tournaments ORDER BY id DESC").fetchall()
                    conn.close()
                    return render_template(
                        "admin/tournaments.html",
                        tournaments=tournaments,
                        systems=admin.SUPPORTED_SYSTEMS,
                        lang=lang,
                        translations=translations,
                    )
                tournament_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
                }
                tournament_values = [
                    name,
                    request.form.get("location", "").strip(),
                    rounds,
                    pairing_system,
                    pairing_system,
                    bye_points,
                    absent_points,
                ]
                insert_columns = [
                    "name", "location", "rounds", "tournament_type", "pairing_system",
                    "bye_points", "absent_points",
                ]
                if "description" in tournament_columns:
                    insert_columns.insert(1, "description")
                    tournament_values.insert(
                        1, request.form.get("description", "").strip())
                if "acceleration_scheme" in tournament_columns:
                    insert_columns.append("acceleration_scheme")
                    tournament_values.append(acceleration_scheme)
                if pairing_system == "accelerated_swiss" and "acceleration_rounds" in tournament_columns:
                    insert_columns.append("acceleration_rounds")
                    tournament_values.append(
                        admin.default_acceleration_rounds(rounds))
                if "handicap_enabled" in tournament_columns:
                    insert_columns.append("handicap_enabled")
                    tournament_values.append(handicap_enabled)
                insert_columns.extend(["status", "created_at"])
                tournament_values.extend(["draft", admin.current_timestamp()])
                placeholders = ", ".join("?" for _ in insert_columns)
                conn.execute(
                    f"INSERT INTO tournaments ({', '.join(insert_columns)}) VALUES ({placeholders})",
                    tournament_values,
                )
                tournament_id = conn.execute(
                    "SELECT last_insert_rowid()").fetchone()[0]
                conn.commit()
                admin.log_admin_action(
                    "tournament_created",
                    "tournament",
                    {"tournament_id": tournament_id, "name": name,
                        "pairing_system": pairing_system},
                    user_id=admin.session.get("user_id"),
                )
                flash(translations["success"])
                conn.close()
                return redirect(url_for("admin_tournament", tournament_id=tournament_id, lang=lang))
        elif action == "create":
            flash(translations["error"])

    sort_key = admin.parse_tournament_sort(request.args.get("sort"))
    sort_order = admin.parse_tournament_order(request.args.get("order"))
    participant_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tournament_participants', 'tournament_pending_players')"
        ).fetchall()
    }
    if sort_key == "participants" and {"tournament_participants", "tournament_pending_players"}.issubset(participant_tables):
        participant_sort_expr = "(SELECT COUNT(*) FROM tournament_participants WHERE tournament_id = t.id) + (SELECT COUNT(*) FROM tournament_pending_players WHERE tournament_id = t.id)"
    else:
        participant_sort_expr = "0"
    page = admin.parse_page_number(request.args.get("page"), default=1)
    page_size = admin.parse_page_size(
        request.args.get("page_size"), default=25)
    total_count = conn.execute(
        "SELECT COUNT(*) FROM tournaments").fetchone()[0]
    page_details = admin.pagination_details(total_count, page, page_size)
    page = page_details["page"]
    page_size = page_details["page_size"]
    order_expression = (
        admin.TOURNAMENT_SORT_FIELDS[sort_key]
        if sort_key != "participants"
        else participant_sort_expr
    )
    tournaments = conn.execute(
        f"SELECT t.* FROM tournaments t ORDER BY {order_expression} {sort_order.upper()}, t.id DESC LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    conn.close()
    return render_template(
        "admin/tournaments.html",
        tournaments=tournaments,
        systems=admin.SUPPORTED_SYSTEMS,
        lang=lang,
        translations=translations,
        sort=sort_key,
        order=sort_order,
        total_count=total_count,
        **page_details,
    )
