"""Server-side verification for score-based reCAPTCHA v3."""
import json
import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import (
    RECAPTCHA_EXPECTED_HOSTNAME,
    RECAPTCHA_MIN_SCORE,
    RECAPTCHA_SECRET_KEY,
    RECAPTCHA_SITE_KEY,
)

logger = logging.getLogger(__name__)
VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def verify_recaptcha(token, action, remote_ip=None):
    """Return whether a reCAPTCHA v3 token passes the configured policy."""
    if not RECAPTCHA_SITE_KEY and not RECAPTCHA_SECRET_KEY:
        return True
    if not token or not RECAPTCHA_SITE_KEY or not RECAPTCHA_SECRET_KEY:
        return False

    payload = {"secret": RECAPTCHA_SECRET_KEY, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        request = Request(
            VERIFY_URL,
            data=urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        logger.exception("reCAPTCHA verification failed")
        return False

    if not result.get("success") or result.get("action") != action:
        return False

    try:
        score = float(result.get("score"))
    except (TypeError, ValueError):
        return False
    if score < RECAPTCHA_MIN_SCORE:
        return False

    if RECAPTCHA_EXPECTED_HOSTNAME:
        hostname = result.get("hostname")
        if hostname != RECAPTCHA_EXPECTED_HOSTNAME:
            return False

    return True
