-- ============================================================
-- 08_create_views.sql
-- SQL views for all queries used by the application.
-- Execute after all data has been loaded.
-- ============================================================

USE biblio_db;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_paper_author_count
-- Per paper: number of authors
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_paper_author_count AS
SELECT
    paper_id,
    COUNT(author_id) AS num_authors
FROM paper_authors
GROUP BY paper_id;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_conf_yearly_stats
-- Per conference per year: paper count, total authors, distinct authors
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_conf_yearly_stats AS
SELECT
    p.conf_id,
    p.year,
    COUNT(DISTINCT p.paper_id)          AS paper_count,
    COUNT(pa.author_id)                  AS total_author_slots,
    COUNT(DISTINCT pa.author_id)         AS distinct_authors,
    AVG(sub.num_authors)                 AS avg_authors_per_paper
FROM papers p
JOIN paper_authors  pa  ON pa.paper_id  = p.paper_id
JOIN vw_paper_author_count sub ON sub.paper_id = p.paper_id
WHERE p.type = 'conference'
  AND p.conf_id IS NOT NULL
GROUP BY p.conf_id, p.year;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_journal_yearly_stats
-- Per journal per year: paper count, total authors, distinct authors
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_journal_yearly_stats AS
SELECT
    p.journal_id,
    p.year,
    COUNT(DISTINCT p.paper_id)          AS paper_count,
    COUNT(pa.author_id)                  AS total_author_slots,
    COUNT(DISTINCT pa.author_id)         AS distinct_authors,
    AVG(sub.num_authors)                 AS avg_authors_per_paper
FROM papers p
JOIN paper_authors  pa  ON pa.paper_id  = p.paper_id
JOIN vw_paper_author_count sub ON sub.paper_id = p.paper_id
WHERE p.type = 'journal'
  AND p.journal_id IS NOT NULL
GROUP BY p.journal_id, p.year;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_conf_profile
-- Aggregate profile for each conference (all-time stats)
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_conf_profile AS
SELECT
    c.conf_id,
    c.title,
    c.acronym,
    c.rank,
    c.primary_for,
    pf.description                              AS for_description,
    MIN(p.year)                                 AS first_year,
    MAX(p.year)                                 AS last_year,
    COUNT(DISTINCT p.paper_id)                  AS total_papers,
    COUNT(DISTINCT pa.author_id)                AS distinct_authors,
    ROUND(AVG(sub.num_authors), 2)              AS avg_authors_per_paper,
    ROUND(COUNT(DISTINCT p.paper_id)
          / NULLIF(MAX(p.year) - MIN(p.year) + 1, 0), 2)
                                                AS avg_papers_per_year
FROM conferences c
JOIN papers      p   ON p.conf_id   = c.conf_id
JOIN paper_authors pa ON pa.paper_id = p.paper_id
JOIN vw_paper_author_count sub ON sub.paper_id = p.paper_id
LEFT JOIN primary_for pf ON pf.for_code = c.primary_for
GROUP BY c.conf_id, c.title, c.acronym, c.rank,
         c.primary_for, pf.description;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_journal_profile
-- Aggregate profile for each journal (all-time stats)
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_journal_profile AS
SELECT
    j.journal_id,
    j.title,
    j.publisher,
    j.best_quartile,
    j.sjr_index,
    j.cite_score,
    j.h_index,
    bsa.area_name                               AS subject_area,
    MIN(p.year)                                 AS first_year,
    MAX(p.year)                                 AS last_year,
    COUNT(DISTINCT p.paper_id)                  AS total_papers,
    COUNT(DISTINCT pa.author_id)                AS distinct_authors,
    ROUND(AVG(sub.num_authors), 2)              AS avg_authors_per_paper,
    ROUND(COUNT(DISTINCT p.paper_id)
          / NULLIF(MAX(p.year) - MIN(p.year) + 1, 0), 2)
                                                AS avg_papers_per_year
FROM journals j
JOIN papers      p   ON p.journal_id = j.journal_id
JOIN paper_authors pa ON pa.paper_id  = p.paper_id
JOIN vw_paper_author_count sub ON sub.paper_id = p.paper_id
LEFT JOIN best_subject_area bsa ON bsa.area_id = j.best_subject_area
GROUP BY j.journal_id, j.title, j.publisher, j.best_quartile,
         j.sjr_index, j.cite_score, j.h_index, bsa.area_name;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_author_profile
-- Per author: first/last year, total papers, avg per year
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_author_profile AS
SELECT
    a.author_id,
    a.name,
    MIN(p.year)                                 AS first_year,
    MAX(p.year)                                 AS last_year,
    COUNT(DISTINCT p.paper_id)                  AS total_papers,
    ROUND(COUNT(DISTINCT p.paper_id)
          / NULLIF(MAX(p.year) - MIN(p.year) + 1, 0), 2)
                                                AS avg_papers_per_year
FROM authors a
JOIN paper_authors pa ON pa.author_id  = a.author_id
JOIN papers       p  ON p.paper_id    = pa.paper_id
GROUP BY a.author_id, a.name;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_author_yearly_stats
-- Per author per year and type: how many papers published
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_author_yearly_stats AS
SELECT
    pa.author_id,
    p.year,
    p.type,
    COUNT(DISTINCT p.paper_id) AS paper_count
FROM paper_authors pa
JOIN papers p ON p.paper_id = pa.paper_id
GROUP BY pa.author_id, p.year, p.type;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_year_profile
-- Per year: total papers, distinct journals, distinct conferences,
-- total author slots, distinct authors
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_year_profile AS
SELECT
    p.year,
    COUNT(DISTINCT p.paper_id)               AS total_papers,
    COUNT(DISTINCT p.conf_id)                AS distinct_conferences,
    COUNT(DISTINCT p.journal_id)             AS distinct_journals,
    COUNT(pa.author_id)                       AS total_author_slots,
    COUNT(DISTINCT pa.author_id)              AS distinct_authors
FROM papers p
LEFT JOIN paper_authors pa ON pa.paper_id = p.paper_id
GROUP BY p.year;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_publisher_stats
-- Per publisher: total journals + breakdown by quartile
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_publisher_stats AS
SELECT
    publisher,
    COUNT(*)                                            AS total_journals,
    SUM(best_quartile = 'Q1')                           AS q1_count,
    SUM(best_quartile = 'Q2')                           AS q2_count,
    SUM(best_quartile = 'Q3')                           AS q3_count,
    SUM(best_quartile = 'Q4')                           AS q4_count
FROM journals
WHERE publisher IS NOT NULL
GROUP BY publisher;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_category_yearly_conf
-- Per PrimaryFoR per year: number of conferences active that year
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_category_yearly_conf AS
SELECT
    c.primary_for   AS for_code,
    pf.description,
    p.year,
    COUNT(DISTINCT c.conf_id)   AS conf_count,
    COUNT(DISTINCT p.paper_id)  AS paper_count
FROM papers p
JOIN conferences  c  ON c.conf_id  = p.conf_id
LEFT JOIN primary_for pf ON pf.for_code = c.primary_for
WHERE c.primary_for IS NOT NULL
GROUP BY c.primary_for, pf.description, p.year;

-- ────────────────────────────────────────────────────────────
-- VIEW: vw_category_yearly_journal
-- Per BestSubjectArea per year: journals active that year
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_category_yearly_journal AS
SELECT
    j.best_subject_area AS area_id,
    bsa.area_name,
    p.year,
    COUNT(DISTINCT j.journal_id) AS journal_count,
    COUNT(DISTINCT p.paper_id)   AS paper_count
FROM papers p
JOIN journals j ON j.journal_id = p.journal_id
LEFT JOIN best_subject_area bsa ON bsa.area_id = j.best_subject_area
WHERE j.best_subject_area IS NOT NULL
GROUP BY j.best_subject_area, bsa.area_name, p.year;

-- Confirm views created
SHOW FULL TABLES WHERE Table_type = 'VIEW';
