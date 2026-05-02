import sys
from pathlib import Path

try:
    from project_config import DB_CONFIG, FLASK_DEBUG
except ModuleNotFoundError:
    ROOT_DIR = Path(__file__).resolve().parent.parent
    root_path = str(ROOT_DIR)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    from project_config import DB_CONFIG, FLASK_DEBUG

CACHE_CONFIG = {
    "DEBUG": True,          # some Flask-Caching versions use this
    "CACHE_TYPE": "SimpleCache", # In-memory cache for simplicity
    "CACHE_DEFAULT_TIMEOUT": 300
}
