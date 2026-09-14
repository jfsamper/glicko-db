"""Administrative authentication, user, profile, settings, audit, and submission routes."""


def _admin_routes():
    from routes import admin
    return admin


def _delegate(function_name):
    def delegated_handler(*args, **kwargs):
        return getattr(_admin_routes(), function_name)(*args, **kwargs)

    delegated_handler.__name__ = function_name
    return delegated_handler


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
    for route, endpoint, methods in routes:
        target = "admin_forgot_password" if endpoint == "admin_forgot_password_alias" else endpoint
        admin_bp.add_url_rule(
            route,
            endpoint=endpoint,
            view_func=_delegate(target),
            methods=list(methods),
        )
