"""Test real Passenger startup; runs normal database migrations. Back up first."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import runpy
import sys
import traceback

from diagnose_hosting import redact_secrets


ROOT = Path(__file__).resolve().parents[2]


def check_startup(root):
    output = io.StringIO()
    result = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "executable": sys.executable,
        "app_root": str(root),
        "ok": False,
        "warning": "This check runs normal startup migrations and initialization.",
    }
    previous_cwd = Path.cwd()
    previous_path = list(sys.path)
    try:
        os.chdir(root)
        sys.path.insert(0, str(root))
        with redirect_stdout(output), redirect_stderr(output):
            namespace = runpy.run_path(str(root / "passenger_wsgi.py"))
            application = namespace["application"]
            with application.test_client() as client:
                response = client.get(
                    "/", environ_overrides={"SCRIPT_NAME": "/glicko"}
                )
                result["home_status"] = response.status_code
                response.close()
            result["ok"] = result["home_status"] == 200
    except Exception:
        result["traceback"] = traceback.format_exc()
    finally:
        os.chdir(previous_cwd)
        sys.path[:] = previous_path
        result["output"] = output.getvalue()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


def main():
    path = ROOT / "temp" / "passenger-startup-check.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": "started",
        "warning": "Real startup is running, including database migrations.",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")
    result = check_startup(ROOT)
    path.write_text(
        redact_secrets(json.dumps(result, indent=2)), encoding="utf-8"
    )
    print(f"Private startup report written to {path}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
