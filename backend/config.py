import importlib.util
from pathlib import Path

_ROOT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.py"
_spec = importlib.util.spec_from_file_location("project_root_config", _ROOT_CONFIG_PATH)
_root_config = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_root_config)

DB_CONFIG = _root_config.DB_CONFIG
FLASK_DEBUG = _root_config.FLASK_DEBUG

CACHE_CONFIG = {
    "DEBUG": True,          # some Flask-Caching versions use this
    "CACHE_TYPE": "SimpleCache", # In-memory cache for simplicity
    "CACHE_DEFAULT_TIMEOUT": 300
}
