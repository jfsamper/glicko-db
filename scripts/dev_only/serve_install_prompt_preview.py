import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_SECRET_KEY", "local-preview-only-key")
os.environ.setdefault("ADMIN_PASSWORD", "local-preview-only-password")
os.environ["LOAD_SAMPLE_DATA"] = "0"

import config

config.DB_PATH = str(ROOT / "temp" / "install-prompt-preview.db")
Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)

from app import app, initialize_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple


initialize_app()
preview_app = DispatcherMiddleware(app, {"/glicko": app})
run_simple("127.0.0.1", 5001, preview_app)