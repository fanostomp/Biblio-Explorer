import os

# ETL Configuration
# Edit DB_PASSWORD before running any ETL scripts.

DB_CONFIG = {
    "host":     "localhost",
    "port":     3307,
    "user":     "root",
    "password": "",
    "database": "biblio_db",
    "charset":  "utf8mb4",
}
# Absolute paths to source data files
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

INPROCEEDINGS_CSV = os.path.join(DATA_DIR, "dblp_dataset", "input_inproceedings.csv")
ARTICLES_CSV      = os.path.join(DATA_DIR, "dblp_dataset", "input_article.csv")
ICORE_CSV         = os.path.join(DATA_DIR, "icore26_data", "iCore26_KilledColumnsForLoading.csv")
ICORE_RAW_DIR     = os.path.join(DATA_DIR, "icore26_data", "iCORE_raw")
ICORE_CATS_XLSX   = os.path.join(DATA_DIR, "icore26_data", "icoreCategories.xlsx")
JOURNAL_RANK_CSV  = os.path.join(DATA_DIR, "journal_ranking_data_raw", "journal_ranking_data_raw.csv")
BEST_AREA_CSV     = os.path.join(DATA_DIR, "journal_ranking_data_raw", "bestSubjectArea.csv")
