"""Compatibility facade re-exporting owners split out of this former god-module.

Auth/session/permissions live in services.auth_service, audit logging in
services.audit_service, timezone/current-time helpers in
services.timezone_service, the raw DB connection factory in services.db, and
the player-totals recompute in services.stats_service. Import from those
owners directly in new code; this module only exists for backward
compatibility with existing imports.
"""
from services.auth_service import (
    ALLOWED_ROLES,
    admin_required,
    authenticate_user,
    bootstrap_default_admin_account,
    consume_result_approval_code,
    create_password_reset_token,
    create_result_approval_code,
    create_user_account,
    get_current_user,
    migrate_auth_schema,
    migrate_result_submissions_schema,
    reset_password_with_token,
    send_password_reset_email,
    user_has_permission,
    validate_email_address,
    validate_theme,
)
from services.audit_service import (
    AUDIT_DETAILS_MAX_BYTES,
    AUDIT_RETENTION_DAYS,
    log_admin_action,
    migrate_audit_log_schema,
)
from services.chart_service import build_rating_chart_data, build_smooth_path
from services.db import get_db
from services.i18n import TRANSLATIONS, get_language
from services.player_stats import build_player_result_summary
from services.stats_service import refresh_stats
from services.timezone_service import (
    current_date,
    current_datetime,
    current_timestamp,
    format_timezone_label,
    get_configured_timezone,
    get_timezone_choices,
    server_date,
    timestamp_days_ago,
    validate_timezone,
)

# Compatibility-only: some tests still monkeypatch this instead of config.DB_PATH.
# services.db.get_db() reads config.DB_PATH directly and ignores this value.
from config import DB_PATH
