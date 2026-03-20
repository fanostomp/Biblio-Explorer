import mysql.connector

DB_CONFIG = {
    'host': 'localhost',
    'port': 3307,
    'user': 'root',
    'password': '',
    'database': 'biblio_db'
}

def check_journal(title):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        print(f"--- Investigating Journal: {title} ---")
        
        # 1. Find journal entry
        cursor.execute("SELECT journal_id, title FROM journals WHERE title LIKE %s", (f"%{title}%",))
        journals = cursor.fetchall()
        print(f"Found {len(journals)} journal entries in lookup table.")
        
        for j in journals:
            jid = j['journal_id']
            jtitle = j['title']
            print(f"  Lookup ID: {jid}, Title: {jtitle}")
            
            # 2. Check for papers linked via FK
            cursor.execute("SELECT COUNT(*) as count FROM papers WHERE journal_id = %s", (jid,))
            count = cursor.fetchone()['count']
            print(f"    Papers currently linked: {count}")

        # 3. Check for papers that SHOULD be linked (based on title search if possible)
        # Note: In this schema, papers usually only have a FK to journals.
        # But maybe there's a papers_raw or similar? Or let's check a few paper titles.
        print("\n--- Sampling papers to see what journal info is stored ---")
        cursor.execute("SELECT * FROM papers WHERE type = 'journal' LIMIT 3")
        samples = cursor.fetchall()
        for s in samples:
            print(f"  Paper ID: {s['paper_id']}, Title: {s['title']}, JournalID FK: {s.get('journal_id', 'MISSING')}")

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

check_journal("Nature Reviews Molecular Cell Biology")
