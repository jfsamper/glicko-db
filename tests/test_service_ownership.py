from services import auth_service, audit_service, chart_service, common, db, i18n, stats_service, timezone_service


def test_translations_and_chart_helpers_have_dedicated_owners():
    assert common.TRANSLATIONS is i18n.TRANSLATIONS
    assert common.build_rating_chart_data is chart_service.build_rating_chart_data
    assert common.build_smooth_path is chart_service.build_smooth_path


def test_auth_audit_timezone_db_and_stats_have_dedicated_owners():
    assert common.get_db is db.get_db
    assert common.refresh_stats is stats_service.refresh_stats
    assert common.log_admin_action is audit_service.log_admin_action
    assert common.migrate_audit_log_schema is audit_service.migrate_audit_log_schema
    assert common.current_timestamp is timezone_service.current_timestamp
    assert common.get_configured_timezone is timezone_service.get_configured_timezone
    assert common.get_current_user is auth_service.get_current_user
    assert common.authenticate_user is auth_service.authenticate_user
    assert common.migrate_auth_schema is auth_service.migrate_auth_schema
    assert common.ALLOWED_ROLES is auth_service.ALLOWED_ROLES