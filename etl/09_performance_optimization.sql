-- ============================================================
-- 09_performance_optimization.sql
-- Safe additive indexes for the profile/papers query paths.
-- This script intentionally avoids schema denormalization or
-- view rewrites so DB-07 cannot change query correctness.
-- ============================================================

USE biblio_db;

DROP PROCEDURE IF EXISTS add_index_if_missing;

DELIMITER //
CREATE PROCEDURE add_index_if_missing(
    IN in_table_name VARCHAR(64),
    IN in_index_name VARCHAR(64),
    IN in_column_signature TEXT,
    IN in_add_sql TEXT
)
BEGIN
    DECLARE existing_count INT DEFAULT 0;

    SELECT COUNT(*)
    INTO existing_count
    FROM (
        SELECT
            index_name,
            GROUP_CONCAT(column_name ORDER BY seq_in_index SEPARATOR ',') AS column_signature
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = in_table_name
          AND index_type <> 'FULLTEXT'
        GROUP BY index_name
    ) AS candidate_indexes
    WHERE candidate_indexes.column_signature = in_column_signature;

    IF existing_count = 0 THEN
        SET @ddl = in_add_sql;
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;

        SELECT CONCAT(
            'Created index ',
            in_index_name,
            ' on ',
            in_table_name,
            '(',
            in_column_signature,
            ')'
        ) AS status;
    ELSE
        SELECT CONCAT(
            'Equivalent index already exists on ',
            in_table_name,
            '(',
            in_column_signature,
            ')'
        ) AS status;
    END IF;
END //
DELIMITER ;

CALL add_index_if_missing(
    'papers',
    'idx_paper_conf_year',
    'conf_id,year',
    'CREATE INDEX idx_paper_conf_year ON papers (conf_id, year)'
);

CALL add_index_if_missing(
    'papers',
    'idx_paper_journal_year',
    'journal_id,year',
    'CREATE INDEX idx_paper_journal_year ON papers (journal_id, year)'
);

DROP PROCEDURE IF EXISTS add_index_if_missing;
