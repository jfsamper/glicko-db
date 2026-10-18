import io
import json
import os
import wsgiref.handlers

import app as app_module
from scripts.dev_only import cgi_bridge


def test_selector_env_is_loaded_without_overriding_real_environment(tmp_path):
    home = tmp_path / "home"
    project = home / "example.org" / "glicko"
    project.mkdir(parents=True)
    selector = home / ".cl.selector"
    selector.mkdir()
    (selector / "python-selector.json").write_text(json.dumps({
        "example.org/glicko": {"env_vars": {"APP_SECRET_KEY": "from-selector", "ADMIN_PASSWORD": "pw"}},
        "other.org/app": {"env_vars": {"UNRELATED": "x"}},
    }), encoding="utf-8")
    environ = {"APP_SECRET_KEY": "already-set"}

    cgi_bridge.load_selector_env(environ, project_root=str(project), home=str(home))

    assert environ == {"APP_SECRET_KEY": "already-set", "ADMIN_PASSWORD": "pw"}


def test_missing_selector_file_is_ignored(tmp_path):
    environ = {}
    cgi_bridge.load_selector_env(environ, project_root=str(tmp_path / "app"), home=str(tmp_path))
    assert environ == {}


def test_request_uri_is_mapped_to_the_mounted_app():
    environ = {
        "REQUEST_URI": "/glicko/player/view%20x?id=1&lang=es",
        "SCRIPT_NAME": "/glicko/glicko.cgi",
        "PATH_INFO": "",
    }
    cgi_bridge.translate_environ(environ, base_uri="/glicko")
    assert environ["SCRIPT_NAME"] == "/glicko"
    assert environ["PATH_INFO"] == "/player/view x"


def test_app_root_maps_to_slash():
    for uri in ("/glicko", "/glicko/", "/glicko?lang=en"):
        environ = {"REQUEST_URI": uri}
        cgi_bridge.translate_environ(environ, base_uri="/glicko")
        assert environ["PATH_INFO"] == "/"


def test_initialization_runs_once_per_deployed_app(tmp_path, monkeypatch):
    (tmp_path / "app.py").touch()
    monkeypatch.setattr(cgi_bridge, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(cgi_bridge, "INIT_STAMP", str(tmp_path / "temp" / ".cgi-initialized"))
    calls = []

    cgi_bridge.ensure_initialized(lambda: calls.append(1))
    cgi_bridge.ensure_initialized(lambda: calls.append(1))
    assert calls == [1]

    (tmp_path / "temp" / ".cgi-initialized").write_text("older-deploy", encoding="utf-8")
    cgi_bridge.ensure_initialized(lambda: calls.append(1))
    assert calls == [1, 1]


def test_cgi_request_is_served_by_the_real_app():
    environ = {
        "REQUEST_METHOD": "GET",
        "REQUEST_URI": "/glicko/static/css/admin.css",
        "SERVER_NAME": "www.example.org",
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTPS": "on",
    }
    cgi_bridge.translate_environ(environ, base_uri="/glicko")
    stdout = io.BytesIO()
    handler = wsgiref.handlers.BaseCGIHandler(
        io.BytesIO(), stdout, io.StringIO(), environ, multithread=False, multiprocess=True)

    handler.run(app_module.create_app(auto_init=False))

    assert stdout.getvalue().startswith(b"Status: 200 OK")


def test_run_wires_environment_initialization_and_app(tmp_path, monkeypatch):
    (tmp_path / "app.py").touch()
    monkeypatch.setattr(cgi_bridge, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(cgi_bridge, "INIT_STAMP", str(tmp_path / ".cgi-initialized"))
    monkeypatch.setattr(cgi_bridge, "load_selector_env", lambda environ: None)
    monkeypatch.setitem(os.environ, "REQUEST_URI", "/glicko/rankings?lang=en")
    monkeypatch.delenv("SCRIPT_NAME", raising=False)
    monkeypatch.delenv("PATH_INFO", raising=False)
    initialized = []
    monkeypatch.setattr(app_module, "initialize_app", lambda: initialized.append(True))
    served = {}

    class FakeHandler:
        def run(self, application):
            served["app"] = application
            served["path"] = os.environ["PATH_INFO"]
            served["script"] = os.environ["SCRIPT_NAME"]

    monkeypatch.setattr(wsgiref.handlers, "CGIHandler", FakeHandler)

    cgi_bridge.run()

    assert initialized == [True]
    assert served["path"] == "/rankings"
    assert served["script"] == "/glicko"
    assert served["app"].name == "app"
