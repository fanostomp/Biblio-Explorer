-- ============================================================
-- 09_search_indexes.sql
-- Add Full-Text indexes for better search performance.
-- ============================================================

USE biblio_db;

-- 1. Authors
-- Drop if exists (not strictly standard for FT, so we index only if missing)
-- Note: User already did this possibly, but confirming.
SET @exist := (SELECT COUNT(*) FROM information_schema.statistics 
               WHERE table_schema = 'biblio_db' AND table_name = 'authors' AND index_name = 'idx_author_name_ft');
SET @sqlstmt := IF(@exist > 0, 'SELECT "Index already exists on authors"', 'ALTER TABLE authors ADD FULLTEXT INDEX idx_author_name_ft (name)');
PREPARE stmt FROM @sqlstmt;
EXECUTE stmt;

-- 2. Journals
SET @exist := (SELECT COUNT(*) FROM information_schema.statistics 
               WHERE table_schema = 'biblio_db' AND table_name = 'journals' AND index_name = 'idx_journal_title_ft');
SET @sqlstmt := IF(@exist > 0, 'SELECT "Index already exists on journals"', 'ALTER TABLE journals ADD FULLTEXT INDEX idx_journal_title_ft (title)');
PREPARE stmt FROM @sqlstmt;
EXECUTE stmt;

-- 3. Conferences
SET @exist := (SELECT COUNT(*) FROM information_schema.statistics 
               WHERE table_schema = 'biblio_db' AND table_name = 'conferences' AND index_name = 'idx_conf_search_ft');
SET @sqlstmt := IF(@exist > 0, 'SELECT "Index already exists on conferences"', 'ALTER TABLE conferences ADD FULLTEXT INDEX idx_conf_search_ft (title, acronym)');
PREPARE stmt FROM @sqlstmt;
EXECUTE stmt;
