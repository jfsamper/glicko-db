import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, PROJECT_ROOT)

from app import create_app

application = create_app(auto_init=True)
