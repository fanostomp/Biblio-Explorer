# Bibliographic Data Integration & Visualization

This project handles the integration, schema modeling, processing, and visualization of academic bibliographic data originating from DBLP, iCORE26, and Kaggle. It extracts heavily denormalized data, performs matching and transformation, and builds an optimized MySQL database over which a Flask/D3.js dashboard is built.

**Team**: AM 4855 & AM 5381  
**Course**: MYE030 / PLE045 — Proxorimena Themata Texnologias Logismikou

---

## 🎯 Project Overview (Phases Completed so far)

### Phase I: ETL & Database Architecture

- **Database Target**: MariaDB 10.4+ / MySQL 8.0 `biblio_db` (Port: 3307)
- **Dataset Processed**:
  - Formatted DBLP `inproceedings` and `articles` (CSV)
  - Ranked venues from iCORE26 `conference_rankings`
  - Journal rankings from Kaggle `journal_ranking_data_raw` (TSV)
- **Design Pattern**: We designed a cohesive, unified `papers` schema that handles both conferences and journals in a single table, allowing fast cross-aggregate queries for Author profiles.
- **Extraction Processing**:
  - Corrected string encodings & invalid split characters.
  - Resolved the multiple-authors lists via mapping N:M relationships and maintaining integer sequence order.
  - Extracted 1.4 million highly distinct Author objects from raw strings.
- **Venue Matching algorithms**:
  - Deployed exact matches plus custom Regex normalization algorithms to map plain-text DBLP references to iCORE/Kaggle verified ranking databases.

### Phase II: Application Backend Prototype

- **Stack**: Python 3.10+, Flask, `mysql-connector-python`
- **Architecture**: Implements modular REST API endpoints using Flask Blueprints mapping to entities.
- **Performance Enhancements**:
  - Features a configured MySQL Connection Pool handling concurrent analytical requests.
  - Heavy aggregation analytics (Yearly Stats, Averages) are generated on the DB layer using optimized `SQL Views`.

### Phase III: Full Interactive Application

- **Dynamic Frontend**: Modern, responsive dark-mode HTML/CSS UI with CSS variables, active states, and custom UI components.
- **Search & Filtering**: Live autocomplete search functionality for Conferences and Journals. Parameterized Year Range filters affecting both tables and charts dynamically.
- **D3.js Visualization**:
  - Reusable, animated, interactive Line Charts with hover tooltips (Papers & Authors over time).
  - Advanced Multi-Select Comparison Charts: Evaluate and compare multiple distinct venues (Conferences and Journals) on the same timeline simultaneously.
- **Profile Pages**:
  - Detailed Conference, Journal, Author, and Year profile views displaying specialized rankings (H-index, SJR, Quartile, active years, distinct counts).
  - Scrollable data tables for published papers linking to external DBLP and EE URLs.
  - **Note on Coverage**: Activity statistics (paper counts, active years, authors) reflect the DBLP dataset (`input_article.csv`). Some journals present in the Kaggle ranking database (e.g. non-CS journals like "California Management Review") may have 0 papers simply because they are not covered by DBLP. In such cases, only their base metrics (Quartile, H-Index) are displayed.

---

## 📂 Code Structure & Usage

### 1. Database Creation & Python ETL pipeline

The data extraction algorithms exist within `/etl`. Execute them chronologically:

1. `mysql -u root -P 3307 -e "CREATE DATABASE biblio_db;"`
2. `mysql -u root -P 3307 biblio_db < etl/01_create_schema.sql`
3. `python etl/02_clean_inproceedings.py`
4. `python etl/03_clean_articles.py`
5. `python etl/04_load_lookups.py`
6. `python etl/05_match_conferences.py`
7. `python etl/06_match_journals.py`
8. `python etl/07_load_papers.py` _(takes ~35 mins for 2.5 million rows due to SQL batching)_
9. `mysql -u root -P 3307 biblio_db < etl/08_create_views.sql`
10. `mysql -u root -P 3307 biblio_db < etl/09_search_indexes.sql`
11. `mysql -u root -P 3307 biblio_db < etl/09_performance_optimization.sql`

_A database backup script is provided in `etl/09_backup.bat`._

### Safe Backup / Rebuild / Restore Workflow

For current local DB work, assume the live pipeline is MariaDB `10.4.32` on `localhost:3307` and always take a logical backup before schema or ETL changes.

1. Create a backup first:
   - `etl\09_backup.bat`
   - Dumps are written under `data\backups\` as `biblio_db_backup_YYYY-MM-DD_HH-MM-SS.sql`
   - The script now removes failed zero-byte outputs instead of leaving them behind
2. Rebuild from ETL only when you intentionally want a fresh database from source files:
   - `mysql -u root -P 3307 -e "DROP DATABASE IF EXISTS biblio_db; CREATE DATABASE biblio_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"`
   - Then rerun steps `2` through `11` above in order
3. Restore a known-good dump instead of rerunning ETL when you need the safest rollback path:
   - `mysql -u root -P 3307 -e "DROP DATABASE IF EXISTS biblio_db; CREATE DATABASE biblio_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"`
   - `mysql -u root -P 3307 biblio_db < data/backups/biblio_db_backup_YYYY-MM-DD_HH-MM-SS.sql`
4. Validate after either rebuild or restore:
   - `python scripts/validate_etl.py`

Practical notes:

- `07_load_papers.py` is still the dominant ETL step and currently takes about `35` minutes for the full `2.5M` row load.
- Full SQL dumps are large; the current `data/backups/` snapshot already contains multi-GB files, so backup and restore may take several minutes depending on disk and local DB throughput.
- Use rebuild when ETL code or source mappings changed. Use restore when you want the quickest return to an exact known-good local state.

### Venue Matching Policy

- Venue matching is best-effort. If a DBLP `booktitle` or `journal` value does not map to an iCORE/Kaggle venue row, the paper still remains in the final `papers` table.
- Unmatched conference papers are stored with `type = 'conference'` and `conf_id = NULL`.
- Unmatched journal papers are stored with `type = 'journal'` and `journal_id = NULL`.
- This is intentional: dropping unmatched papers would undercount author and year activity and would make rebuild results depend on incomplete venue dictionaries.
- Downstream effect: author and year analytics include unmatched papers, while conference and journal profile pages only count venue-linked rows because they filter by `conf_id` / `journal_id`.

### 2. Analytical Flask Backend

The REST App is contained in `/backend` serving the static HTML out of `/frontend`.
All logic utilizes a thread-safe DB Pool mapped to port `3307`.

**To run the Dev Server**:

```bash
# Run from the root directory
python backend/app.py
```

> The dashboard will initialize locally on `http://localhost:5000`

### Example Active REST Endpoints:

- `GET /health` : JSON healthcheck.
- `GET /api/conference/` : List of ranked conferences
- `GET /api/journal/<id>/profile` : JSON bundle representing profile data + aggregated stats
- `GET /api/author/<id>/papers` : Reverse-lookup of all published papers for a specific scholar.
