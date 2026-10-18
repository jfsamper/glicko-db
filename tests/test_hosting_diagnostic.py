import json
from pathlib import Path
import sys

from scripts.dev_only import diagnose_hosting


def test_routing_report_excludes_environment_values(tmp_path):
    path = tmp_path / ".htaccess"
    path.write_text(
        'PassengerAppRoot "/home/example/app"\n'
        'SetEnv APP_SECRET_KEY do-not-share\n'
        'PassengerEnvVar ADMIN_PASSWORD also-private\n'
        'RewriteRule ^ index.php [L]\n',
        encoding="utf-8",
    )
    result = diagnose_hosting.inspect_routing(path)
    assert len(result["directives"]) == 2
    assert "do-not-share" not in json.dumps(result)
    assert "also-private" not in json.dumps(result)


def test_broken_dependency_records_traceback():
    result = diagnose_hosting.inspect_dependency("missing", "_missing_hosting_module")
    assert result["ok"] is False
    assert "ModuleNotFoundError" in result["traceback"]


def test_redacts_secret_values(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "private-diagnostic-key")
    assert diagnose_hosting.redact_secrets(
        "failure: private-diagnostic-key"
    ) == "failure: [REDACTED]"


def test_application_probe_uses_mount_without_migrations(monkeypatch):
    import app

    def forbidden():
        raise AssertionError("Diagnostic must not initialize the database")

    monkeypatch.setattr(app, "initialize_app", forbidden)
    result = diagnose_hosting.inspect_application()
    assert result["ok"] is True
    assert result["mounted_static_url"] == "/glicko/static/css/admin.css"
    assert result["static_status"] == 200


def test_database_probe_does_not_create_missing_database(tmp_path):
    path = tmp_path / "missing.db"
    result = diagnose_hosting.inspect_database(path)
    assert result["exists"] is False
    assert not path.exists()


def test_path_probe_checks_syntax_without_executing(tmp_path):
    path = tmp_path / "passenger_wsgi.py"
    path.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    result = diagnose_hosting.inspect_path(path, check_syntax=True)
    assert result["syntax_ok"] is True
    assert result["exists"] is True


def test_path_probe_reports_invalid_startup_syntax(tmp_path):
    path = tmp_path / "passenger_wsgi.py"
    path.write_text("def broken(\n", encoding="utf-8")
    result = diagnose_hosting.inspect_path(path, check_syntax=True)
    assert result["ok"] is False
    assert "SyntaxError" in result["traceback"]


def test_routing_report_includes_error_handlers(tmp_path):
    path = tmp_path / ".htaccess"
    path.write_text(
        "ErrorDocument 500 /index.php\nFallbackResource /index.php\n",
        encoding="utf-8",
    )
    assert len(diagnose_hosting.inspect_routing(path)["directives"]) == 2


def test_startup_check_captures_failure_and_restores_process_state(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(diagnose_hosting.__file__).parent))
    from scripts.dev_only.check_passenger_startup import check_startup

    (tmp_path / "passenger_wsgi.py").write_text(
        "print('startup reached')\nraise RuntimeError('startup failed')\n",
        encoding="utf-8",
    )
    previous_cwd = Path.cwd()
    previous_path = list(sys.path)
    result = check_startup(tmp_path)
    assert result["ok"] is False
    assert "RuntimeError: startup failed" in result["traceback"]
    assert "startup reached" in result["output"]
    assert Path.cwd() == previous_cwd
    assert sys.path == previous_path


def test_startup_check_requests_home_after_loading_entry_point(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(diagnose_hosting.__file__).parent))
    from scripts.dev_only.check_passenger_startup import check_startup

    (tmp_path / "passenger_wsgi.py").write_text(
        "from flask import Flask, request\n"
        "application = Flask('startup-test', root_path=" + repr(str(tmp_path)) + ")\n"
        "@application.get('/')\n"
        "def home():\n"
        "    assert request.script_root == '/glicko'\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    result = check_startup(tmp_path)
    assert result["ok"] is True
    assert result["home_status"] == 200


def test_passenger_probe_does_not_need_flask_or_database():
    from scripts.dev_only.passenger_probe import application

    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(application(
        {"SCRIPT_NAME": "/glicko", "PATH_INFO": "/"}, start_response
    ))
    assert captured["status"] == "200 OK"
    assert captured["headers"]["Cache-Control"] == "no-store"
    assert int(captured["headers"]["Content-Length"]) == len(body)
    assert b"SCRIPT_NAME=/glicko\nPATH_INFO=/\n" in body
