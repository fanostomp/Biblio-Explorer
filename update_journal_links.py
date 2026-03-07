import csv
import mysql.connector
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from etl.config import DB_CONFIG

def update_links():
    print("Preparing to update journal links...")
    
    # 1. Load Journal Name -> Journal ID
    # data/matched/journal_name_to_id.csv
    JOUR_MAP_CSV = os.path.join("data", "matched", "journal_name_to_id.csv")
    journal_map = {}
    with open(JOUR_MAP_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["journal_id"]:
                journal_map[row["dblp_journal_name"]] = int(row["journal_id"])
    
    print(f"Loaded {len(journal_map)} matched journals from mapping file.")

    # 2. Load Raw ID -> Journal Name
    # data/cleaned/cleaned_articles.csv
    ARTICLES_CSV = os.path.join("data", "cleaned", "cleaned_articles.csv")
    raw_to_jid = []
    with open(ARTICLES_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_id = row.get("id")
            jname = row.get("journal")
            if raw_id and jname and jname in journal_map:
                raw_to_jid.append((raw_id, journal_map[jname]))

    print(f"Found {len(raw_to_jid)} article records to update.")

    # 3. Update DB
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        print("Creating temporary table...")
        cur.execute("DROP TABLE IF EXISTS temp_journal_links")
        cur.execute("""
            CREATE TABLE temp_journal_links (
                raw_id VARCHAR(255) PRIMARY KEY,
                journal_id INT
            )
        """)
        conn.commit()
        
        print("Inserting data into temporary table...")
        batch_size = 50000
        for i in range(0, len(raw_to_jid), batch_size):
            batch = raw_to_jid[i:i+batch_size]
            cur.executemany("INSERT IGNORE INTO temp_journal_links (raw_id, journal_id) VALUES (%s, %s)", batch)
            conn.commit()
            print(f"  Inserted {min(i+batch_size, len(raw_to_jid))} / {len(raw_to_jid)}")
            
        print("Executing batch UPDATE using JOIN...")
        cur.execute("""
            UPDATE papers p
            JOIN temp_journal_links t ON p.raw_id = t.raw_id
            SET p.journal_id = t.journal_id
            WHERE p.type = 'journal'
        """)
        updated = cur.rowcount
        conn.commit()
        print(f"Update complete. Rows modified: {updated}")
        
        cur.execute("DROP TABLE temp_journal_links")
        conn.commit()
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    update_links()
