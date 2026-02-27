import mysql.connector

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
    
    # Let's pick a known author ID from the previous run (ID 1)
    author_id = 17000

    
    print(f"Fetching profile for Author ID {author_id}...")
    cursor.execute("SELECT * FROM vw_author_profile WHERE author_id = %s", (author_id,))
    result = cursor.fetchone()
    
    if result:
        print("\n--- Profile Data ---")
        for k, v in result.items():
            print(f"{k}: {v}")
    else:
        print("Profile not found.")
        
    conn.close()
except Exception as e:
    print(f"Error: {e}")
