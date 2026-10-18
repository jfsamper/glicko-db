"""Write a non-migrating cPanel deployment report using only standard-library tools."""
import argparse
from datetime import datetime, timezone
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import traceback


ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = {
    "Flask": "flask",
    "Flask-WTF": "flask_wtf",
    "Werkzeug": "werkzeug",
    "openpyxl": "openpyxl",
    "reportlab": "reportlab",
    "Markdown": "markdown",
    "Babel": "babel",
    "pycountry": "pycountry",
    "Pillow": "PIL",
    "networkx": "networkx",
    "tzdata": "tzdata",
}
ROUTING_DIRECTIVES = {
    "passengerapproot", "passengerbaseuri", "passengerpython",
    "passengerappenv", "passengerapptype", "passengerstartupfile",
    "passengerenabled", "rewriterule", "rewritecond", "rewritebase",
    "rewriteengine", "directoryindex",
    "errordocument", "fallbackresource", "alias", "aliasmatch",
    "sethandler", "addhandler", "rewriteoptions", "passengerpublicdir",
}


def error_details():
    return {"ok": False, "traceback": traceback.format_exc()}


def inspect_routing(path):
    result = {"path": str(path), "exists": path.is_file(), "directives": []}
    if not result["exists"]:
        return result
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped and stripped.split()[0].lower() in ROUTING_DIRECTIVES:
                result["directives"].append({"line": number, "text": stripped})
    except (OSError, UnicodeError):
        result.update(error_details())
    return result


def inspect_dependency(distribution, module_name):
    try:
        module = importlib.import_module(module_name)
        result = {"ok": True, "module_file": getattr(module, "__file__", None)}
        try:
            result["version"] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            result["metadata_error"] = "Imported package has no distribution metadata"
        if module_name == "babel":
            result["locale_check"] = module.Locale.parse("es").territories["CO"]
        elif module_name == "pycountry":
            result["country_check"] = module.countries.get(alpha_3="COL").name
        return result
    except Exception:
        # A broken dependency must not prevent reporting the remaining checks.
        return error_details()


def inspect_database(path):
    result = {"path": str(path), "exists": path.is_file()}
    if not result["exists"]:
        return result
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)
        try:
            result["table_count"] = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
            result["ok"] = True
        finally:
            conn.close()
    except sqlite3.Error:
        result.update(error_details())
    return result


def inspect_path(path, check_syntax=False):
    result = {"path": str(path)}
    try:
        entry = path.lstat()
        result.update({
            "exists": path.exists(),
            "symlink": path.is_symlink(),
            "resolved_path": str(path.resolve()),
            "mode": stat.filemode(entry.st_mode),
            "uid": entry.st_uid,
            "gid": entry.st_gid,
            "readable": os.access(path, os.R_OK),
            "searchable": os.access(path, os.X_OK) if path.is_dir() else None,
            "modified_at": datetime.fromtimestamp(
                entry.st_mtime, timezone.utc
            ).isoformat(),
        })
        if check_syntax:
            compile(path.read_bytes(), str(path), "exec")
            result["syntax_ok"] = True
    except FileNotFoundError:
        result["exists"] = False
    except (OSError, SyntaxError):
        result.update(error_details())
    return result


def inspect_application():
    try:
        app_module = importlib.import_module("app")
        application = app_module.create_app(auto_init=False)
        adapter = application.url_map.bind(
            "www.colombiago.org", script_name="/glicko"
        )
        endpoint, _values = adapter.match("/", method="GET")
        static_url = adapter.build(
            "static", {"filename": "css/admin.css"}, force_external=False
        )
        with application.test_client() as client:
            response = client.get(
                "/static/css/admin.css",
                environ_overrides={"SCRIPT_NAME": "/glicko"},
            )
            static_status = response.status_code
            response.close()
        return {
            "ok": static_status == 200,
            "module_file": app_module.__file__,
            "home_endpoint": endpoint,
            "mounted_static_url": static_url,
            "static_status": static_status,
            "note": "No startup migrations or dynamic database-backed pages were run.",
        }
    except Exception:
        return error_details()


def collect_report(root):
    sys.path.insert(0, str(root))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": {
            "executable": sys.executable,
            "version": sys.version,
            "prefix": sys.prefix,
            "cwd": str(Path.cwd()),
            "app_root": str(root),
            "sys_path": sys.path,
        },
        "environment_present": {
            key: bool(os.environ.get(key))
            for key in ("ADMIN_PASSWORD", "APP_SECRET_KEY")
        },
        "dependencies": {
            name: inspect_dependency(name, module)
            for name, module in DEPENDENCIES.items()
        },
        "routing": [
            inspect_routing(directory / ".htaccess")
            for directory in (root, root / "public", root.parent, root.parent.parent)
        ],
        "filesystem": [
            inspect_path(path, check_syntax=path.name == "passenger_wsgi.py")
            for path in (
                root.parent.parent, root.parent, root, root / "public",
                root / "passenger_wsgi.py", root / "app.py",
                root / "static" / "css" / "admin.css",
                root / "passenger.log",
            )
        ],
        "database": inspect_database(root / "data" / "acg_ratings.db"),
        "application": inspect_application(),
        "limitations": (
            "A cPanel script may use different environment variables or a different "
            "interpreter from Passenger. This does not test Passenger startup, nginx "
            "configuration, or database migrations."
        ),
    }


def redact_secrets(text):
    for key, value in os.environ.items():
        if value and any(word in key.upper() for word in ("PASSWORD", "SECRET", "TOKEN")):
            text = text.replace(value, "[REDACTED]")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "temp" / "deployment-diagnostic.json"
    )
    args = parser.parse_args()
    report = collect_report(ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        redact_secrets(json.dumps(report, indent=2)), encoding="utf-8"
    )
    print(f"Report written to {args.output.resolve()}")
    print("Download through cPanel File Manager; do not publish this report on the web.")
    return 0 if report["application"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
