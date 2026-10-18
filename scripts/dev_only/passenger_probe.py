"""Temporary cPanel startup file to isolate Passenger routing from Flask startup."""


def application(environ, start_response):
    body = (
        "GLICKO-PASSENGER-PROBE-20261018\n"
        f"SCRIPT_NAME={environ.get('SCRIPT_NAME', '')}\n"
        f"PATH_INFO={environ.get('PATH_INFO', '')}\n"
    ).encode("utf-8")
    start_response("200 OK", [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Glicko-Deployment-Probe", "20261018"),
    ])
    return [body]
