import mysql.connector
import os

# Using the same config as the app
DB_CONFIG = {
    'host': 'localhost',
    'port': 3307,
    'user': 'root',
    'password': '',
    'database': 'biblio_db'
}

try:
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    
    print("Checking database counts...")
    cursor.execute("SELECT COUNT(*) as count FROM authors")
    print(f"Total authors: {cursor.fetchone()['count']}")
    
    print("\nSample authors:")
    cursor.execute("SELECT * FROM authors LIMIT 5")
    for row in cursor.fetchall():
        print(row)
        
    print("\nChecking vw_author_profile...")
    cursor.execute("SELECT * FROM vw_author_profile LIMIT 5")
    for row in cursor.fetchall():
        print(row)
        
    conn.close()
except Exception as e:
    print(f"Error: {e}")
