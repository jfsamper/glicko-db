import os
from datetime import timedelta, timezone

# Require an explicit admin password in every environment.
# For local development, set ADMIN_PASSWORD in the shell or user environment.
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"] #dev-example-password
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "60"))
MAIL_SERVER = os.getenv("MAIL_SERVER", "")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() in ("true", "1", "yes")
MAIL_FROM = os.getenv("MAIL_FROM", "")
PASSWORD_RESET_TTL_SECONDS = int(os.getenv("PASSWORD_RESET_TTL_SECONDS", "3600"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(
    BASE_DIR,
    "data",
    "acg_ratings.db"
)

DEFAULT_RATING = 1500.0
DEFAULT_RD = 88.0
DEFAULT_VOLATILITY = 0.01
TAU = 0.5
DEFAULT_TIMEZONE = timezone(timedelta(hours=-5), name="UTC-05:00")
TIMEZONE_CHOICES = (
    "UTC",
    "America/Bogota",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Mexico_City",
    "America/New_York",
    "America/Sao_Paulo",
    "Asia/Seoul",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Australia/Sydney",
    "Europe/London",
    "Europe/Madrid",
    "Europe/Paris",
)
LANGUAGE_CHOICES = ("es", "en", "pt")
THEME_CHOICES = ("light", "dark")

GLICKO_K = 16.6 #17 #18 #19
GLICKO_M = 340 #338 #336 #352 #405 #423

SKIP_SHEETS = {
    "index",
    "player list",
    "player list*",
    "summary sheets",
    "ratings by player",
    "wins & losses",
    "matches",
    "recording form",
    "groups",
    "lista de jugadores",
    "tabla de resultados",
    "indice",
}