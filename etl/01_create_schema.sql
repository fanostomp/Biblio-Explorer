-- ============================================================
-- DDL Script: Bibliographic Data Integration DB
-- Course: MYE030/PLE045 Spring 2026
-- Team: AM 4855 & AM 5381
-- ============================================================

DROP DATABASE IF EXISTS biblio_db;
CREATE DATABASE biblio_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE biblio_db;

-- ============================================================
-- LOOKUP TABLE: Field of Research (PrimaryFoR) for conferences
-- Note: for_code is stored as VARCHAR because some codes are
-- non-integer strings (per the iCORE README).
-- ============================================================
CREATE TABLE primary_for (
    for_code    VARCHAR(20)  NOT NULL,
    description VARCHAR(255) NOT NULL,
    PRIMARY KEY (for_code)
) ENGINE=InnoDB;

-- ============================================================
-- LOOKUP TABLE: Best Subject Area for journals (Kaggle)
-- ============================================================
CREATE TABLE best_subject_area (
    area_id     INT          NOT NULL AUTO_INCREMENT,
    area_name   VARCHAR(255) NOT NULL,
    PRIMARY KEY (area_id),
    UNIQUE KEY uq_area_name (area_name)
) ENGINE=InnoDB;

-- ============================================================
-- LOOKUP TABLE: Conferences (from iCORE26)
-- dblp_key: the DBLP crossref key used to match papers (nullable
-- because not all iCORE26 entries have a confirmed DBLP entry)
-- ============================================================
CREATE TABLE conferences (
    conf_id     INT          NOT NULL AUTO_INCREMENT,
    title       VARCHAR(512) NOT NULL,
    acronym     VARCHAR(50)  NOT NULL,
    rank        ENUM('A*','A','B','C') DEFAULT NULL,
    primary_for VARCHAR(20)  DEFAULT NULL,
    dblp_key    VARCHAR(255) DEFAULT NULL,
    PRIMARY KEY (conf_id),
    UNIQUE KEY uq_conf_acronym (acronym),
    KEY idx_conf_rank (rank),
    KEY idx_conf_for (primary_for),
    CONSTRAINT fk_conf_for FOREIGN KEY (primary_for)
        REFERENCES primary_for(for_code)
        ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

-- ============================================================
-- LOOKUP TABLE: Journals (from Kaggle ranking data)
-- Metrics kept here are the ranking attributes — actual
-- articles are in the papers factual table.
-- ============================================================
CREATE TABLE journals (
    journal_id          INT            NOT NULL AUTO_INCREMENT,
    title               VARCHAR(512)   NOT NULL,
    country             VARCHAR(100)   DEFAULT NULL,
    publisher           VARCHAR(255)   DEFAULT NULL,
    sjr_index           DECIMAL(10,3)  DEFAULT NULL,
    cite_score          DECIMAL(10,3)  DEFAULT NULL,
    h_index             INT            DEFAULT NULL,
    best_quartile       ENUM('Q1','Q2','Q3','Q4') DEFAULT NULL,
    best_subject_area   INT            DEFAULT NULL,
    total_docs          INT            DEFAULT NULL,
    total_refs          INT            DEFAULT NULL,
    total_cites_3y      INT            DEFAULT NULL,
    citable_docs_3y     INT            DEFAULT NULL,
    cites_per_doc_2y    DECIMAL(10,3)  DEFAULT NULL,
    refs_per_doc        DECIMAL(10,3)  DEFAULT NULL,
    -- DBLP journal name as it appears in input_article.csv (for matching)
    dblp_name           VARCHAR(512)   DEFAULT NULL,
    PRIMARY KEY (journal_id),
    KEY idx_journal_quartile (best_quartile),
    KEY idx_journal_area (best_subject_area),
    KEY idx_journal_publisher (publisher(100)),
    CONSTRAINT fk_journal_area FOREIGN KEY (best_subject_area)
        REFERENCES best_subject_area(area_id)
        ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

-- ============================================================
-- LOOKUP TABLE: Authors
-- Assumption: no synonymy (per project description)
-- ============================================================
CREATE TABLE authors (
    author_id   INT          NOT NULL AUTO_INCREMENT,
    name        VARCHAR(512) NOT NULL,
    PRIMARY KEY (author_id),
    UNIQUE KEY uq_author_name (name(255)),
    FULLTEXT KEY ft_author_name (name)
) ENGINE=InnoDB;

-- ============================================================
-- FACTUAL TABLE: Papers (conference papers + journal articles)
-- Design decision: single table with type discriminator.
-- Trade-off documented in implementation_plan.md.
-- Exactly one of (conf_id, journal_id) must be non-NULL
-- (enforced by CHECK constraint).
-- ============================================================
CREATE TABLE papers (
    paper_id    INT          NOT NULL AUTO_INCREMENT,
    title       TEXT         NOT NULL,
    year        INT          NOT NULL,
    pages       VARCHAR(50)  DEFAULT NULL,
    type        ENUM('conference','journal') NOT NULL,
    conf_id     INT          DEFAULT NULL,
    journal_id  INT          DEFAULT NULL,
    volume      VARCHAR(50)  DEFAULT NULL,
    number      VARCHAR(50)  DEFAULT NULL,
    dblp_key    VARCHAR(255) DEFAULT NULL,
    ee          VARCHAR(512) DEFAULT NULL,
    url         VARCHAR(512) DEFAULT NULL,
    raw_id      VARCHAR(255) DEFAULT NULL,
    PRIMARY KEY (paper_id),
    KEY idx_paper_year (year),
    KEY idx_paper_conf (conf_id),
    KEY idx_paper_journal (journal_id),
    KEY idx_paper_type (type),
    FULLTEXT KEY ft_paper_title (title),
    CONSTRAINT fk_paper_conf FOREIGN KEY (conf_id)
        REFERENCES conferences(conf_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_paper_journal FOREIGN KEY (journal_id)
        REFERENCES journals(journal_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    -- Exactly one venue must be set
    CONSTRAINT chk_paper_venue CHECK (
        (type = 'conference' AND journal_id IS NULL)
        OR
        (type = 'journal' AND conf_id IS NULL)
    )
) ENGINE=InnoDB;

-- ============================================================
-- FACTUAL TABLE: Paper-Author (N:M) with author ordering
-- author_order: 1-based position in the original author list
-- ============================================================
CREATE TABLE paper_authors (
    paper_id        INT NOT NULL,
    author_id       INT NOT NULL,
    author_order    INT NOT NULL,
    PRIMARY KEY (paper_id, author_id),
    KEY idx_pa_author (author_id),
    CONSTRAINT fk_pa_paper  FOREIGN KEY (paper_id)
        REFERENCES papers(paper_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_pa_author FOREIGN KEY (author_id)
        REFERENCES authors(author_id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB;

-- ============================================================
-- Verify structure
-- ============================================================
SHOW TABLES;
