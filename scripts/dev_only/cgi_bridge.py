"""Emergency CGI fallback for hosts where Apache's mod_passenger is unavailable.

Copy this file and glicko.cgi to the app root only during a confirmed outage.
"""
import json
import os
import sys
from urllib.parse import unquote_to_bytes

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BASE_URI = os.environ.get("GLICKO_BASE_URI", "/glicko").rstrip("/")
INIT_STAMP = os.path.join(PROJECT_ROOT, "temp", ".cgi-initialized")


def load_selector_env(environ, project_root=PROJECT_ROOT, home=None):
    """Copy the cPanel Python app environment variables, which Passenger would normally inject."""
    if home is None:
        import pwd

        home = pwd.getpwuid(os.getuid()).pw_dir
    selector_file = os.path.join(home, ".cl.selector", "python-selector.json")
    try:
        with open(selector_file, encoding="utf-8") as handle:
            apps = json.load(handle)
    except (OSError, ValueError):
        return
    app_key = os.path.relpath(project_root, home).replace(os.sep, "/")
    for name, value in (apps.get(app_key) or {}).get("env_vars", {}).items():
        environ.setdefault(name, str(value))


def translate_environ(environ, base_uri=BASE_URI):
    """Make the CGI request look like the Passenger request the app expects."""
    raw_path = environ.get("REQUEST_URI", "").split("?", 1)[0]
    path = unquote_to_bytes(raw_path).decode("latin-1")
    if path.lower().startswith(base_uri.lower()):
        path = path[len(base_uri):]
    environ["SCRIPT_NAME"] = base_uri
    environ["PATH_INFO"] = path or "/"


def _init_signature():
    return str(os.stat(os.path.join(PROJECT_ROOT, "app.py")).st_mtime_ns)


def ensure_initialized(initialize):
    """Run migrations once per deployed app.py instead of on every CGI request."""
    signature = _init_signature()
    try:
        with open(INIT_STAMP, encoding="utf-8") as handle:
            if handle.read().strip() == signature:
                return
    except OSError:
        pass
    initialize()
    os.makedirs(os.path.dirname(INIT_STAMP), exist_ok=True)
    with open(INIT_STAMP, "w", encoding="utf-8") as handle:
        handle.write(signature)


def run():
    from wsgiref.handlers import CGIHandler

    sys.path.insert(0, PROJECT_ROOT)
    load_selector_env(os.environ)
    translate_environ(os.environ)

    from app import create_app, initialize_app

    ensure_initialized(initialize_app)
    CGIHandler().run(create_app(auto_init=False))
