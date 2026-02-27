import os

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 3307)),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'biblio_db')
}

CACHE_CONFIG = {
    "DEBUG": True,          # some Flask-Caching versions use this
    "CACHE_TYPE": "SimpleCache", # In-memory cache for simplicity
    "CACHE_DEFAULT_TIMEOUT": 300
}
