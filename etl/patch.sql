USE biblio_db;
ALTER TABLE papers DROP CONSTRAINT chk_paper_venue;
ALTER TABLE papers ADD CONSTRAINT chk_paper_venue CHECK ((type = 'conference' AND journal_id IS NULL) OR (type = 'journal' AND conf_id IS NULL));
SET FOREIGN_KEY_CHECKS=0;
TRUNCATE TABLE paper_authors;
TRUNCATE TABLE papers;
SET FOREIGN_KEY_CHECKS=1;
