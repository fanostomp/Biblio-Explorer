-- ============================================================
-- 09_search_indexes.sql
-- Add only the missing FULLTEXT indexes required by the live
-- search endpoints. This script is additive and idempotent: it
-- checks for equivalent FULLTEXT indexes by column list, not by
-- hard-coded index name.
-- ============================================================

USE biblio_db;

DROP PROCEDURE IF EXISTS add_fulltext_index_if_missing;

DELIMITER //
CREATE PROCEDURE add_fulltext_index_if_missing(
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
          AND index_type = 'FULLTEXT'
        GROUP BY index_name
    ) AS candidate_indexes
    WHERE candidate_indexes.column_signature = in_column_signature;

    IF existing_count = 0 THEN
        SET @ddl = in_add_sql;
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;

        SELECT CONCAT(
            'Created FULLTEXT index ',
            in_index_name,
            ' on ',
            in_table_name,
            '(',
            in_column_signature,
            ')'
        ) AS status;
    ELSE
        SELECT CONCAT(
            'Equivalent FULLTEXT index already exists on ',
            in_table_name,
            '(',
            in_column_signature,
            ')'
        ) AS status;
    END IF;
END //
DELIMITER ;

CALL add_fulltext_index_if_missing(
    'authors',
    'idx_author_name_ft',
    'name',
    'ALTER TABLE authors ADD FULLTEXT INDEX idx_author_name_ft (name)'
);

CALL add_fulltext_index_if_missing(
    'journals',
    'idx_journal_title_ft',
    'title',
    'ALTER TABLE journals ADD FULLTEXT INDEX idx_journal_title_ft (title)'
);

CALL add_fulltext_index_if_missing(
    'conferences',
    'idx_conf_search_ft',
    'title,acronym',
    'ALTER TABLE conferences ADD FULLTEXT INDEX idx_conf_search_ft (title, acronym)'
);

DROP PROCEDURE IF EXISTS add_fulltext_index_if_missing;
