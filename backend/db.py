from contextlib import contextmanager
import mysql.connector
from mysql.connector import pooling

# Global pool object
connection_pool = None

def init_pool(config, pool_name="mypool", pool_size=5):
    """Initialize the global MySQL connection pool."""
    global connection_pool
    # Create a copy to prevent mutation
    dbconfig = config.copy()
    
    # We remove any pool-related configs if they exist and explicitly set them
    connection_pool = mysql.connector.pooling.MySQLConnectionPool(
        pool_name=pool_name,
        pool_size=pool_size,
        pool_reset_session=True,
        **dbconfig
    )
    print(f"MySQL Connection Pool '{pool_name}' initialized with {pool_size} connections.")

def get_db_connection():
    """Get a connection from the global pool."""
    global connection_pool
    if connection_pool is None:
        raise RuntimeError("Connection pool not initialized. Call init_pool() first.")
    return connection_pool.get_connection()

@contextmanager
def get_db_cursor(commit=False):
    """Context manager for database connections and cursors."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        yield cursor
        if commit:
            conn.commit()
    finally:
        cursor.close()
        conn.close()

def execute_query(conn, query, params=(), fetchone=False):
    """Execute a query and return results as dictionaries.
    Note: If 'conn' is None, it uses the context manager (legacy support).
    """
    if conn is None:
        with get_db_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone() if fetchone else cursor.fetchall()
            
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(query, params)
        if fetchone:
            result = cursor.fetchone()
        else:
            result = cursor.fetchall()
        return result
    finally:
        cursor.close()
