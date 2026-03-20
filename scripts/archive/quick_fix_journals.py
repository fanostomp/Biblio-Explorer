#!/usr/bin/env python3
"""
Quick fix: Directly update papers with new journal mappings.
More efficient than full reload for targeted fixes.
"""
import csv
import mysql.connector

DB_CONFIG = {
    'host': 'localhost',
    'port': 3307,
    'user': 'root',
    'password': '',
    'database': 'biblio_db'
}

def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    print("=" * 70)
    print("QUICK FIX: Link unmatched papers to journals via raw_id")
    print("=" * 70)

    # Load current successful mappings (journal_id known)
    journal_mappings = {}
    with open('data/matched/journal_name_to_id.csv', 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['journal_id']:
                journal_mappings[row['dblp_journal_name']] = int(row['journal_id'])

    print(f"\nLoaded {len(journal_mappings)} journal mappings")

    # Load cleaned_articles to get raw_id -> journal_name mapping
    print("Building raw_id -> journal_id mapping from cleaned_articles.csv...")
    raw_to_journal = {}
    with open('data/cleaned/cleaned_articles.csv', 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_id = row.get('raw_id', '').strip()
            jname = row.get('journal', '').strip()
            if raw_id and jname and jname in journal_mappings:
                raw_to_journal[raw_id] = journal_mappings[jname]

    print(f"  Built {len(raw_to_journal):,} raw_id -> journal_id mappings")

    # Find papers with NULL journal_id that match our mappings
    print("\nFinding papers to update...")
    cursor.execute("""
        SELECT paper_id, raw_id
        FROM papers
        WHERE type = 'journal' AND journal_id IS NULL
    """)
    papers_to_update = cursor.fetchall()
    print(f"  Found {len(papers_to_update):,} papers with NULL journal_id")

    updates = []
    not_found = 0
    for paper_id, raw_id in papers_to_update:
        if raw_id and raw_id in raw_to_journal:
            updates.append((raw_to_journal[raw_id], paper_id))
        else:
            not_found += 1

    print(f"  Can fix: {len(updates):,} papers")
    print(f"  Cannot fix: {not_found:,} papers (no matching raw_id)")

    # Batch update
    if updates:
        print(f"\nUpdating {len(updates):,} papers...")
        cursor.executemany(
            "UPDATE papers SET journal_id = %s WHERE paper_id = %s",
            updates
        )
        conn.commit()
        print(f"  Updated {cursor.rowcount:,} papers")

        # Verify
        cursor.execute("SELECT COUNT(*) FROM papers WHERE journal_id IS NULL AND type = 'journal'")
        remaining = cursor.fetchone()[0]
        print(f"\nRemaining unlinked journal papers: {remaining:,}")

    conn.close()
    print("\nDone!")

if __name__ == '__main__':
    main()
