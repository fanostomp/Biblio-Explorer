# BiblioExplorer Database Task Board

**Owner:** Database / ETL Track  
**Context Date:** 2026-03-18  
**Goal:** Stabilize database correctness, improve venue mapping quality, and prevent future bad imports.

---

## Summary

The database rebuild fixed the duplicate-load issue and restored exact author identity handling. The next database work should focus on:

1. Improving conference matching coverage
2. Preventing future duplicate fact loads
3. Making ETL validation repeatable
4. Clarifying unmatched-venue behavior in schema and ETL
5. Fixing misleading ETL load reporting
6. Applying and validating DB performance/index improvements

---

## DB-01: Improve Conference Matching Coverage

**Priority:** P0  
**Status:** Done  
**Owner:** ETL / Data Integration

### Problem

Conference matching is still the weakest part of the pipeline.

- Distinct matched booktitles: `2,024 / 6,650`
- Matched conference rows: `780,401 / 1,413,090`
- Unmatched conference rows: `632,689`

### Objective

Reduce unmatched conference rows significantly by improving automated matching and adding curated manual mappings where needed.

### Scope

- [etl/05_match_conferences.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/05_match_conferences.py)
- [data/matched/unmatched_conferences.txt](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/data/matched/unmatched_conferences.txt)
- Optional curated mapping file in `data/matched/`

### Tasks

- [x] Identify top recurring unmatched conference names by row frequency, not only distinct title count
- [x] Add manual alias support for known variants and split-volume variants
- [x] Handle patterns like `CONF (1)`, `CONF (2)`, `X/Y`, workshop variants, and anniversary titles
- [x] Add a curated manual mapping file if heuristics are no longer enough
- [x] Rebuild mappings and measure improvement

### Acceptance Criteria

- [x] Conference matched rows increase materially from the current baseline of `780,401`
- [x] Unmatched conference rows decrease materially from the current baseline of `632,689`
- [x] Matching logic stays conservative enough to avoid obvious false positives
- [x] New match types are documented in script comments

### Result

- Conference lookup sync inserted `65` missing source-present acronyms into `conferences` without reassigning existing `conf_id` values
- Conference source conflicts are isolated in `data/matched/conference_source_conflicts.csv` and left unresolved intentionally
- Current matcher snapshot after sync: `1,684 / 6,650` distinct booktitles matched
- Current matcher snapshot after sync: `813,196 / 1,413,090` conference rows matched, `599,894` unmatched
- Current live DB after targeted conference backfill: `840,692 / 1,413,090` conference papers linked to a venue, `572,398` still unmatched

---

## DB-02: Prevent Duplicate Fact Imports

**Priority:** P0  
**Status:** Done  
**Owner:** DB Schema / ETL

### Problem

The previous database was corrupted because `07_load_papers.py` was rerun and `papers` allowed duplicate `(type, raw_id)` rows.

### Objective

Make repeated fact loads safe by default.

### Scope

- [etl/01_create_schema.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/01_create_schema.sql)
- [etl/07_load_papers.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/07_load_papers.py)

### Tasks

- [x] Decide whether `(type, raw_id)` should be enforced as unique in `papers`
- [x] Add a unique constraint and test repeat-load behavior
- [x] Add explicit ETL guardrails so reruns fail fast if the schema/data are unsafe
- [x] Document the intended rerun workflow in ETL comments

### Acceptance Criteria

- [x] It is no longer possible to silently duplicate fact rows by rerunning `07_load_papers.py`
- [x] Duplicate raw-id validation returns zero after repeated safe runs

### Result

- Chosen approach: both DB constraint and ETL guard
- `papers` now declares `UNIQUE KEY uq_paper_type_raw_id (type, raw_id)` in [etl/01_create_schema.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/01_create_schema.sql)
- [etl/07_load_papers.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/07_load_papers.py) now:
  - validates that a unique `(type, raw_id)` guard exists before loading
  - aborts if duplicate raw-id groups already exist in `papers`
  - upserts `papers` rows with `ON DUPLICATE KEY UPDATE` so reruns reuse/update existing facts instead of inserting duplicates
  - keeps `paper_authors` idempotent via `INSERT IGNORE`
- Concrete verification on scratch DB built from the updated schema:
  - first load: `papers = 3`, `paper_authors = 4`
  - second safe rerun over the same raw ids: `papers = 3`, `paper_authors = 4`
  - duplicate raw-id groups after rerun: `0`
  - rerun updated existing facts in place (`conf-001` title/EE changed, `jour-001` title/volume/EE changed) without creating extra rows
- Current live `biblio_db` check on 2026-03-18:
  - duplicate raw-id groups currently present: `0`
  - unique `(type, raw_id)` index is not yet present in the existing DB, so the updated loader now fails fast there until the schema change is applied by rebuild or migration

---

## DB-03: Add Repeatable ETL Validation

**Priority:** P0  
**Status:** Done  
**Owner:** QA / ETL

### Problem

We currently validate the DB manually after rebuilds. That is too fragile.

### Objective

Create a repeatable validation script for post-load database checks.

### Scope

- New script under `scripts/` or root
- Optional SQL validation file under `etl/`

### Tasks

- [x] Add a validation script that checks:
  - duplicate `papers(type, raw_id)`
  - orphan `paper_authors`
  - total row counts
  - unmatched conference and journal venue counts
  - author count vs cleaned distinct-author count
  - paper-author link count vs cleaned unique pair count
- [x] Print a short pass/fail summary
- [x] Make it runnable immediately after ETL

### Acceptance Criteria

- [x] One command can validate the rebuilt database
- [x] Validation clearly flags regressions in duplicates, missing links, and mapping coverage

### Result

- Added [scripts/validate_etl.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/scripts/validate_etl.py) as the repeatable post-load validation command:
  - `python scripts/validate_etl.py`
- Added [etl/validation_baseline.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/validation_baseline.py) with the checked ETL baseline snapshot used for count and venue-coverage regression detection
- Validator checks:
  - `papers UNIQUE(type, raw_id)` guard presence
  - orphan protection on `paper_authors` via required foreign keys
  - total `papers`, conference papers, and journal papers counts
  - unmatched and matched conference/journal venue row counts
  - `authors` count vs expected cleaned distinct-author count
  - `paper_authors` count vs expected cleaned unique pair count
- Concrete run on current live `biblio_db` on `2026-03-18`:
  - `11` checks passed
  - `2` checks failed
  - all row-count and venue-coverage checks matched the baseline exactly
  - failure is the known live-schema gap from DB-02: `papers` still lacks `UNIQUE(type, raw_id)`, so duplicate protection is reported as failing until rebuild or migration applies the new index

---

## DB-04: Clarify Unmatched Venue Policy

**Priority:** P1  
**Status:** Done  
**Owner:** DB Design

### Problem

The schema comments say exactly one venue FK should exist, but the current `CHECK` only enforces type consistency, not non-null venue assignment.

### Objective

Make unmatched-venue behavior explicit and consistent across schema, ETL, and documentation.

### Scope

- [etl/01_create_schema.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/01_create_schema.sql)
- [etl/07_load_papers.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/07_load_papers.py)
- README / ETL comments

### Tasks

- [x] Decide whether unmatched papers are allowed in final production tables
- [x] If allowed, update schema comments and ETL comments to match reality
- [x] Document downstream impact on analytics/profile pages
- [x] Verify the chosen policy against current validator and query behavior

### Acceptance Criteria

- [x] Schema comments, ETL behavior, and documentation no longer contradict each other

### Result

- Chosen policy: unmatched venue rows are allowed in final `papers`
- Reasoning:
  - the current ETL and validator already preserve and measure unmatched conference/journal rows explicitly
  - rejecting unmatched rows would undercount author/year activity and would require a new reject/staging flow that the current app does not use
  - venue-specific pages already operate on `conf_id` / `journal_id`, so matched-only venue views fall out naturally without changing the app contract
- Updated [etl/01_create_schema.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/01_create_schema.sql) comments to describe the actual invariant: type consistency is enforced, but the relevant venue FK may remain `NULL`
- Updated [etl/07_load_papers.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/07_load_papers.py) comments and load summary output so unmatched rows are an explicit supported ETL path
- Updated [README.md](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/README.md) to document how unmatched papers affect analytics and profile pages
- Concrete checks used for DB-04:
  - validator baseline still expects unmatched final rows: `conference_rows_unmatched_venue = 572,398`, `journal_rows_unmatched_venue = 31,937`
  - venue routes query `papers WHERE conf_id = ?` and `papers WHERE journal_id = ?`, so unmatched papers are excluded from venue pages by design
  - author/year routes and views read from `papers` with no venue filter or with left joins, so unmatched papers remain visible in non-venue analytics

---

## DB-05: Fix ETL Load Reporting

**Priority:** P1  
**Status:** Done  
**Owner:** ETL

### Problem

`04_load_lookups.py` reports attempted inserts, not actual inserted rows. This hides deduplication and makes diagnostics misleading.

### Scope

- [etl/04_load_lookups.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/04_load_lookups.py)

### Tasks

- [x] Replace naive counters with actual inserted/ignored/skipped reporting
- [x] Distinguish duplicate suppression from malformed-row skips
- [x] Report final DB row totals after each load stage where helpful

### Acceptance Criteria

- [x] Console output reflects actual inserted results, not just attempted operations

### Result

- [etl/04_load_lookups.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/04_load_lookups.py) now reports actual ETL outcomes instead of attempted operations for all lookup stages
- `primary_for`, `best_subject_area`, and `authors` now distinguish:
  - malformed/blank source-row skips
  - actual inserted rows from MySQL
  - duplicate suppression / already-present rows under their existing unique guards
  - final table totals after the stage
- `authors` reporting now also shows:
  - total source author rows scanned
  - duplicate source rows collapsed before load
  - progress phrased as distinct names processed rather than attempted inserts
- `conferences` keeps the current sync diagnostics but now reports actual insert outcomes and final row totals more directly, including in dry-run mode
- `journals` now reports valid rows, malformed skips, actual inserted rows, and final totals only
- Important current limitation: `journals` does **not** report duplicate suppression because the current schema still has no unique load guard on that table, so reruns append rows rather than being ignored
- Concrete dry-run verification on the live `biblio_db` on `2026-03-20`:
  - `primary_for`: `107` valid, `0` inserted, `107` ignored/already present, final total `107`
  - `best_subject_area`: `27` valid, `0` inserted, `27` ignored/already present, final total `27`
  - `conferences`: `944` existing before sync, `0` would insert, final total `944`
  - `journals`: `18,013` valid rows, actual inserts reflected directly in dry-run totals, with no duplicate-suppression claim
  - `authors`: dry-run output now separates blank-name skips, source deduplication, actual inserted rows, and duplicate suppression against the existing `authors` table

---

## DB-06: Review Conference Lookup Dedup Strategy

**Priority:** P1  
**Status:** Done  
**Owner:** Data Modeling

### Problem

The iCORE CSV contains repeated acronyms. Current conference loading collapses rows by acronym uniqueness, but that may hide useful distinctions or noisy duplicates.

### Scope

- [etl/04_load_lookups.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/04_load_lookups.py)
- `conferences` table design

### Tasks

- [x] Audit duplicate acronyms in source data
- [x] Decide whether acronym alone should remain the unique key
- [x] If needed, pre-merge or normalize conference source rows before insert
- [x] Document the decision and tradeoff

### Acceptance Criteria

- [x] Conference-table dedup logic is explicit and justified

### Result

- Chosen policy: keep acronym as the unique conference identity key for the current pipeline
- Reasoning:
  - the schema already enforces `UNIQUE KEY uq_conf_acronym (acronym)` on `conferences`
  - [etl/05_match_conferences.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/05_match_conferences.py) currently relies on a single `acronym -> conf_id` mapping
  - existing live `conf_id` values are already referenced by matched `papers`, so broadening conference identity beyond acronym would risk `conf_id` churn and matching regressions
- Source audit on the current iCORE snapshot:
  - `1,267` valid source rows
  - `944` distinct source acronyms
  - `255` repeated acronym groups
  - `308` exact duplicate rows collapsed across `246` groups
  - `0` same-normalized-title variant groups in the current source snapshot
  - `14` ambiguous acronym collisions skipped intentionally: `ACE`, `CIS`, `CISIS`, `FSE`, `GI`, `IAS`, `ICCP`, `ICEC`, `IDC`, `IE`, `ISC`, `ISWC`, `ITC`, `SAC`
- [etl/04_load_lookups.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/04_load_lookups.py) now makes the conference dedup stages explicit:
  - source rows are parsed and cleaned first
  - rows are grouped by acronym
  - each acronym group is classified as single-row, exact-duplicate-only, same-normalized-title variant, or ambiguous acronym collision
  - only non-ambiguous acronym groups produce a canonical insert candidate
  - the title fingerprint remains a conservative guard for same-conference spelling variants only; distinct normalized titles under one acronym are not auto-merged
- Load reporting is now explicit about the dedup policy and diagnostics:
  - repeated acronym groups are counted directly
  - same-normalized-title variant groups and ambiguous acronym collisions are named directly
  - the conflict CSV is described as the list of ambiguous acronym collisions intentionally not auto-merged
- Tradeoff:
  - this may leave some source acronyms under-modeled when iCORE reuses one acronym for multiple venues
  - that is safer than silently merging distinct conferences or changing the current conference identity model mid-pipeline
- Concrete verification on the current live `biblio_db` on `2026-03-20`:
  - `python etl/04_load_lookups.py --only conferences --dry-run` reported `944` distinct source acronyms, `255` repeated acronym groups, `308` exact duplicate rows collapsed, `0` same-normalized-title variant groups collapsed, `14` ambiguous acronym groups skipped intentionally, and `0` unambiguous source acronyms still missing
  - [data/matched/conference_source_conflicts.csv](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/data/matched/conference_source_conflicts.csv) still contains exactly the `14` ambiguous acronyms above
  - live DB sanity checks confirmed `conferences = 944`, `COUNT(DISTINCT acronym) = 944`, and no duplicate acronyms exist
  - each ambiguous acronym still resolves to a single existing conference row in the live DB; no new rows are inserted for those collisions

---

## DB-07: Apply and Verify Search / Performance Indexes

**Priority:** P1  
**Status:** Done  
**Owner:** DB Performance

### Problem

Data quality fixes are now in place, but database performance still needs formal verification.

### Scope

- [etl/09_search_indexes.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/09_search_indexes.sql)
- [etl/09_performance_optimization.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/09_performance_optimization.sql)
- [scripts/benchmark_search_profile.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/scripts/benchmark_search_profile.py)
- Search and profile endpoints

### Tasks

- [x] Check whether these SQL optimizations are already applied
- [x] Apply missing indexes safely
- [x] Benchmark key queries before/after
- [x] Record improvements and any regressions

### Acceptance Criteria

- [x] Search/profile queries are measurably faster
- [x] No correctness regressions introduced by optimization scripts

### Result

- DB-07 was implemented as a safe live-schema/index fix, not by running the old optimization script blindly
- Current live DB target on `2026-03-20` is MariaDB `10.4.32`, so verification was done against MariaDB behavior, not assumed MySQL 8.0 behavior
- [etl/09_search_indexes.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/09_search_indexes.sql) is now idempotent by checking for equivalent FULLTEXT indexes by ordered column list instead of hard-coded index name only
- [etl/09_performance_optimization.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/09_performance_optimization.sql) was rewritten to stay additive and index-only:
  - no `DROP INDEX`
  - no `papers.num_authors` column
  - no `vw_paper_author_count` rewrite
  - only conditional creation of `papers(conf_id, year)` and `papers(journal_id, year)`
- [scripts/benchmark_search_profile.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/scripts/benchmark_search_profile.py) was added as a repeatable read-only verifier for:
  - DB engine/version
  - required index coverage
  - deterministic search/profile fixtures
  - 5-run benchmark medians
  - result fingerprints
  - `EXPLAIN` plans for the year-filtered paper/profile queries
- Concrete live index state before DB-07 on `2026-03-20`:
  - `authors(name)` FULLTEXT already existed as `ft_author_name`
  - `journals(title)` FULLTEXT was missing
  - `conferences(title, acronym)` FULLTEXT was missing
  - `papers(conf_id, year)` was missing
  - `papers(journal_id, year)` was missing
- Concrete live index application on `2026-03-20`:
  - `etl/09_search_indexes.sql` preserved the existing author FULLTEXT index and created:
    - `idx_journal_title_ft` on `journals(title)`
    - `idx_conf_search_ft` on `conferences(title, acronym)`
  - `etl/09_performance_optimization.sql` created:
    - `idx_paper_conf_year` on `papers(conf_id, year)`
    - `idx_paper_journal_year` on `papers(journal_id, year)`
- Benchmark fixture used by the repeatable verifier on the current live DB:
  - conference: `conf_id = 305` (`ICASSP`), filtered years `2008-2013`
  - journal: `journal_id = 11215` (`Corrosion`), filtered years `2009-2014`
  - author search term: `vincent`
  - conference search term: `science`
  - journal search term: `nature`
- Before/after benchmark medians from `python scripts/benchmark_search_profile.py` on `2026-03-20`:
  - `conference_papers_count`: `61.40 ms -> 3.59 ms`
  - `conference_papers_page`: `3868.53 ms -> 0.79 ms`
  - `conference_yearly_stats`: `732.92 ms -> 744.46 ms`
  - `journal_papers_count`: `166.24 ms -> 18.75 ms`
  - `journal_papers_page`: `1333.99 ms -> 0.87 ms`
  - `journal_yearly_stats`: `2685.23 ms -> 2940.91 ms`
  - `author_search_match`: `389.16 ms -> 300.05 ms`
  - `conference_search_match`: `ERROR 1191 (missing FULLTEXT) -> 0.78 ms`
  - `journal_search_match`: `ERROR 1191 (missing FULLTEXT) -> 1.00 ms`
- Correctness/regression verification on `2026-03-20`:
  - all benchmarked query fingerprints were unchanged for the year-filtered conference/journal paper queries and for author search before vs after index application
  - post-change conference FULLTEXT search returned the same fingerprint as the `LIKE` sanity query for `science`
  - post-change journal FULLTEXT search returned the same fingerprint as the `LIKE` sanity query for `nature`
  - post-change `EXPLAIN` confirmed the paged/count paper queries now use `idx_paper_conf_year` and `idx_paper_journal_year`
  - no schema/view correctness regressions were introduced because DB-07 intentionally did not denormalize `papers` or replace `vw_paper_author_count`
  - `python scripts/validate_etl.py` still matches the DB-03 baseline after DB-07, with the same expected live failure only for the still-missing `papers UNIQUE(type, raw_id)` guard from DB-02
- Tradeoff kept intentionally for safety:
  - the safe index-only approach materially improved search and year-filtered paper list/count endpoints, but it did **not** improve the heavy `vw_conf_yearly_stats` / `vw_journal_yearly_stats` derived views
  - those views still execute through aggregate work on `papers` + `paper_authors`; improving them further would require a separate query/view refactor or pre-aggregation decision, which DB-07 intentionally avoids to keep the current pipeline stable

---

## DB-08: Apply Live `papers UNIQUE(type, raw_id)` Guard

**Priority:** P1  
**Status:** Complete  
**Owner:** DB Schema / ETL

### Problem

DB-02 is complete in code and schema files, but the current live `biblio_db` still does not have the `papers UNIQUE(type, raw_id)` guard applied.

### Scope

- Live `biblio_db` schema
- [etl/01_create_schema.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/01_create_schema.sql)
- [etl/07_load_papers.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/07_load_papers.py)
- [scripts/validate_etl.py](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/scripts/validate_etl.py)

### Tasks

- [x] Check the live `papers` table for duplicate `(type, raw_id)` rows before applying the guard
- [x] Choose and document the safest application path: online migration or rebuild
- [x] Apply `UNIQUE(type, raw_id)` to the live `papers` table without undoing DB-01 through DB-07 work
- [x] Re-run ETL validation and confirm the DB-02 live-schema gap is closed

### Acceptance Criteria

- [x] Live `papers` table has a `UNIQUE(type, raw_id)` guard
- [x] `python scripts/validate_etl.py` no longer fails on the DB-02 live-schema check
- [x] No paper-count or venue-linkage regressions are introduced

### Implementation Notes

- Live pre-check on `2026-03-20`:
  - `SHOW INDEX FROM papers` confirmed the live table still lacked any non-primary unique guard on `(type, raw_id)`
  - duplicate scan over non-null identities returned `duplicate_groups = 0` and `excess_duplicate_rows = 0`
- Safest path chosen for the current pipeline:
  - apply a direct live `ALTER TABLE` instead of rebuild/cleanup, because the current `papers` data was already clean and DB-03 through DB-07 baseline counts were stable
  - this was the minimum-change option that closed the DB-02 live-schema gap without reloading `papers` or touching venue/linkage data
- Applied on live MariaDB `10.4.32` on `2026-03-20`:
  - `ALTER TABLE papers ADD UNIQUE KEY uq_paper_type_raw_id (type, raw_id);`
- Post-change verification on `2026-03-20`:
  - `SHOW CREATE TABLE papers` now includes `UNIQUE KEY uq_paper_type_raw_id (type, raw_id)`
  - `python scripts/validate_etl.py` passed with `13 passed, 0 failed`
  - no count regressions were introduced:
    - `papers_total = 2,525,752`
    - `papers_conference = 1,413,090`
    - `papers_journal = 1,112,662`
    - `matched_conference_rows = 840,692`
    - `unmatched_conference_rows = 572,398`
    - `matched_journal_rows = 1,080,725`
    - `unmatched_journal_rows = 31,937`

---

## DB-09: Add Safe Backup + Restore Workflow Notes

**Priority:** P2  
**Status:** Complete  
**Owner:** Infra / Ops

### Problem

The project has backups, but the rebuild / restore workflow is not yet cleanly documented for future team use.

### Scope

- [etl/09_backup.bat](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/09_backup.bat)
- README or ETL docs

### Tasks

- [x] Document standard sequence: backup -> rebuild -> validate
- [x] Document expected runtime for heavy ETL steps
- [x] Document where dump files are stored and how to restore one

### Acceptance Criteria

- [x] A team member can safely rebuild or restore the DB without guessing

### Implementation Notes

- Chosen documentation path: keep the workflow in [README.md](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/README.md) next to the ETL command list instead of adding a separate ops document
- Reasoning:
  - future teammates already start from the README for rebuild commands
  - the workflow is short enough that splitting it into another file would add navigation overhead without adding clarity
- Hardened [etl/09_backup.bat](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/09_backup.bat) so the documented dump naming matches actual behavior:
  - dump names now use an invariant `YYYY-MM-DD_HH-MM-SS` timestamp generated via PowerShell instead of locale-sensitive `%DATE%` / `%TIME%`
  - default connection settings now match the repo ETL config more closely (`localhost:3307`, `root`, blank password unless edited)
  - failed dumps no longer leave behind zero-byte `.sql` files
  - restore usage is echoed directly by the script after a successful backup
- Added a README workflow for the current local MariaDB pipeline:
  - backup first with `etl\09_backup.bat`
  - rebuild from ETL by recreating `biblio_db`, rerunning `etl/01_create_schema.sql` through `etl/08_create_views.sql`, then applying [etl/09_search_indexes.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/09_search_indexes.sql) and [etl/09_performance_optimization.sql](/c:/Users/public.LAPTOP-17PSCTIQ/Desktop/MATHIMATA/proxorimena/project/etl/09_performance_optimization.sql)
  - restore by recreating `biblio_db` and importing a dump from `data/backups/`
  - validate either rebuild or restore with `python scripts/validate_etl.py`
- Runtime / operator notes now documented explicitly:
  - `python etl/07_load_papers.py` remains the longest ETL step at about `35` minutes
  - dump files in `data/backups/` are large, so backup and restore are expected to take additional minutes on local hardware

---

## Current Baseline

Use these values as the current checkpoint after the latest rebuild:

- `authors = 1,403,305`
- `papers_total = 2,525,752`
- `papers_conference = 1,413,090`
- `papers_journal = 1,112,662`
- `paper_authors = 7,050,510`
- `dup_conf_raw_ids = 0`
- `dup_journal_raw_ids = 0`
- `orphan_pa_paper = 0`
- `orphan_pa_author = 0`
- `conference_rows_matched_venue = 840,692`
- `conference_rows_unmatched_venue = 572,398`
- `journal_rows_matched_venue = 1,080,725`
- `journal_rows_unmatched_venue = 31,937`

---

## Recommended Execution Order

1. `DB-03 Add Repeatable ETL Validation`
2. `DB-04 Clarify Unmatched Venue Policy`
3. `DB-05 Fix ETL Load Reporting`
4. `DB-07 Apply and Verify Search / Performance Indexes`
5. `DB-06 Review Conference Lookup Dedup Strategy`
6. `DB-08 Apply Live papers UNIQUE(type, raw_id) Guard`
7. `DB-09 Add Safe Backup + Restore Workflow Notes`
