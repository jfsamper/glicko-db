from app import create_app
import os
import sys

PROJECT_ROOT = os.path.dirname(__file__)

VENV_SITE_PACKAGES = os.path.join(
    PROJECT_ROOT,
    ".venv",
    "Lib",
    "site-packages"
)

sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, VENV_SITE_PACKAGES)


application = create_app(auto_init=True)
