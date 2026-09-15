"""Administrative backup routes registered on the shared admin blueprint."""
import os
import shutil
from datetime import datetime

from flask import flash, redirect, render_template, request, url_for

from services.timezone_service import current_datetime
from services.audit_service import log_admin_action
from services.i18n import TRANSLATIONS


def _admin_routes():
    # Import lazily because admin.py owns the shared blueprint and compatibility helpers.
    from routes import admin

    return admin


def register_backup_routes(admin_bp):
    """Register backup endpoints without creating a second Flask blueprint."""
    admin_bp.add_url_rule(
        "/admin/backups",
        endpoint="admin_backups",
        view_func=admin_backups,
        methods=["GET"],
    )
    admin_bp.add_url_rule(
        "/admin/backups/create",
        endpoint="admin_create_backup",
        view_func=admin_create_backup,
        methods=["POST"],
    )
    admin_bp.add_url_rule(
        "/admin/backups/restore",
        endpoint="admin_restore_backup",
        view_func=admin_restore_backup,
        methods=["POST"],
    )
    admin_bp.add_url_rule(
        "/admin/backups/delete",
        endpoint="admin_delete_backup",
        view_func=admin_delete_backup,
        methods=["POST"],
    )


def admin_backups():
    admin = _admin_routes()
    permission_error = admin.require_permission("admin")
    if permission_error is not None:
        return permission_error

    lang = admin.get_language(request.args.get("lang"))
    admin.ensure_backup_dir()
    backups = []

    for filename in sorted(os.listdir(admin.BACKUP_DIR), reverse=True):
        if not filename.endswith(".db"):
            continue

        path = os.path.join(admin.BACKUP_DIR, filename)
        backups.append(
            {
                "name": filename,
                "modified": datetime.fromtimestamp(
                    os.path.getmtime(path), tz=current_datetime().tzinfo
                ).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    return render_template(
        "admin/backups.html",
        backups=backups,
        lang=lang,
        translations=TRANSLATIONS[lang],
    )


def admin_create_backup():
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))

    lang = admin.get_language(request.args.get("lang"))
    admin.ensure_backup_dir()
    filename = current_datetime().strftime("%Y-%m-%d-%H%M%S") + ".db"
    backup_path = os.path.join(admin.BACKUP_DIR, filename)
    shutil.copy2(admin.DB_PATH, backup_path)
    admin.backup_sgf_files(backup_path)
    log_admin_action(
        "backup_created",
        "backup",
        {"filename": filename},
        user_id=admin.session.get("user_id"),
    )
    flash(TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_backups", lang=lang))


def admin_restore_backup():
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))

    lang = admin.get_language(request.args.get("lang"))
    path = admin.get_backup_path(request.form.get("name"))
    if path is None or not path.is_file():
        admin.logger.warning(
            "Backup restore rejected: missing or invalid backup path %r",
            request.form.get("name"),
        )
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_backups", lang=lang))

    if not admin.is_valid_sqlite_backup(path):
        admin.logger.warning(
            "Backup restore rejected: invalid SQLite backup %s", path)
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_backups", lang=lang))

    if not admin.restore_db_from_backup(path):
        admin.logger.warning(
            "Backup restore failed during restore step for %s", path)
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_backups", lang=lang))

    log_admin_action(
        "backup_restored",
        "backup",
        {"filename": path.name},
        user_id=admin.session.get("user_id"),
    )
    flash(TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_backups", lang=lang))


def admin_delete_backup():
    admin = _admin_routes()
    if not admin.admin_required():
        return redirect(url_for("admin_login", lang=admin.get_language(request.args.get("lang"))))

    lang = admin.get_language(request.args.get("lang"))
    path = admin.get_backup_path(request.form.get("name"))
    if path is None or not path.is_file():
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_backups", lang=lang))

    os.remove(path)
    sgf_sidecar = path.with_suffix(".sgf")
    if sgf_sidecar.is_dir():
        shutil.rmtree(sgf_sidecar)
    log_admin_action(
        "backup_deleted",
        "backup",
        {"filename": path.name},
        user_id=admin.session.get("user_id"),
    )
    flash(TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_backups", lang=lang))
