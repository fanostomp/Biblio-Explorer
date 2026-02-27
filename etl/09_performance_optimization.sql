-- ============================================================
-- SQL Optimization Script
-- ============================================================

USE biblio_db;

-- 1. Add compound indexes for frequent filtered lookups
DROP INDEX IF EXISTS idx_paper_conf_year ON papers;
DROP INDEX IF EXISTS idx_paper_journal_year ON papers;
CREATE INDEX idx_paper_conf_year ON papers (conf_id, year);
CREATE INDEX idx_paper_journal_year ON papers (journal_id, year);

-- 2. Performance optimization: Denormalize author count if not already done
-- We use a procedure to add the column only if it doesn't exist
DROP PROCEDURE IF EXISTS add_num_authors_col;
DELIMITER //
CREATE PROCEDURE add_num_authors_col()
BEGIN
    IF NOT EXISTS (
        SELECT * FROM information_schema.columns 
        WHERE table_schema = 'biblio_db' 
        AND table_name = 'papers' 
        AND column_name = 'num_authors'
    ) THEN
        ALTER TABLE papers ADD COLUMN num_authors INT DEFAULT 0;
    END IF;
END //
DELIMITER ;
CALL add_num_authors_col();
DROP PROCEDURE add_num_authors_col;

-- Populate num_authors using a more efficient JOIN-based update
UPDATE papers p
JOIN (
    SELECT paper_id, COUNT(*) as c 
    FROM paper_authors 
    GROUP BY paper_id
) t ON p.paper_id = t.paper_id
SET p.num_authors = t.c;

-- 3. Update view to use the new column
CREATE OR REPLACE VIEW vw_paper_author_count AS
SELECT
    paper_id,
    num_authors
FROM papers;
