import logging
import mysql.connector

logger = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(level=logging.INFO):
    logging.basicConfig(level=level, format=LOG_FORMAT)

DB_CONFIG = {
    'host': 'localhost',
    'port': 3307,
    'user': 'root',
    'password': '',
    'database': 'biblio_db'
}

def verify_system_health():
    logger.info("--- Verifying System Health ---")
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 1. Total papers count
        cursor.execute("SELECT COUNT(*) as count FROM papers")
        logger.info(f"Total papers in DB: {cursor.fetchone()['count']}")
        
        # 2. Total journal articles count
        cursor.execute("SELECT COUNT(*) as count FROM papers WHERE type = 'journal'")
        logger.info(f"Total journal articles in DB: {cursor.fetchone()['count']}")
        
        # 3. Top journals by paper count
        cursor.execute("""
            SELECT j.title, COUNT(p.paper_id) as paper_count 
            FROM journals j 
            JOIN papers p ON p.journal_id = j.journal_id 
            GROUP BY j.journal_id 
            ORDER BY paper_count DESC 
            LIMIT 10
        """)
        top_journals = cursor.fetchall()
        logger.info(f"Top 10 journals by paper count:\n{top_journals}")
        
        # 4. Check if vw_journal_profile has any data
        cursor.execute("SELECT COUNT(*) as count FROM vw_journal_profile")
        logger.info(f"Journals in vw_journal_profile: {cursor.fetchone()['count']}")

        conn.close()
    except Exception as e:
        logger.error("DB Error: %s", e, exc_info=True)

if __name__ == "__main__":
    configure_logging()
    verify_system_health()
