import os
import importlib.util
from pathlib import Path

_ROOT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.py"
_spec = importlib.util.spec_from_file_location("project_root_config", _ROOT_CONFIG_PATH)
_root_config = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_root_config)

DB_CONFIG = dict(_root_config.DB_CONFIG)
DB_CONFIG["charset"] = "utf8mb4"
# Absolute paths to source data files
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

INPROCEEDINGS_CSV = os.path.join(DATA_DIR, "dblp_dataset", "input_inproceedings.csv")
ARTICLES_CSV      = os.path.join(DATA_DIR, "dblp_dataset", "input_article.csv")
ICORE_CSV         = os.path.join(DATA_DIR, "icore26_data", "iCore26_KilledColumnsForLoading.csv")
ICORE_RAW_DIR     = os.path.join(DATA_DIR, "icore26_data", "iCORE_raw")
ICORE_CATS_XLSX   = os.path.join(DATA_DIR, "icore26_data", "icoreCategories.xlsx")
JOURNAL_RANK_CSV  = os.path.join(DATA_DIR, "journal_ranking_data_raw", "journal_ranking_data_raw.csv")
BEST_AREA_CSV     = os.path.join(DATA_DIR, "journal_ranking_data_raw", "bestSubjectArea.csv")
