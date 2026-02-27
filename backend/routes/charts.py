from flask import Blueprint, jsonify, request
from db import get_db_connection, execute_query
from extensions import cache

charts_bp = Blueprint('charts', __name__)

@charts_bp.route('/overview', methods=['GET'])
@cache.cached(timeout=3600)
def get_overview():
    """Real data for the homepage chart — total papers per year."""
    conn = get_db_connection()
    try:
        query = """
            SELECT year, COUNT(*) AS num_papers
            FROM papers
            WHERE year IS NOT NULL AND year > 0
            GROUP BY year
            ORDER BY year ASC
        """
        data = execute_query(conn, query)
        return jsonify({'yearly_totals': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

@charts_bp.route('/publishers/bar', methods=['GET'])
@cache.cached(timeout=3600)
def get_publishers_bar():
    conn = get_db_connection()
    try:
        query = """
            SELECT publisher,
                   COUNT(journal_id) AS total_journals,
                   SUM(CASE WHEN best_quartile = 'Q1' THEN 1 ELSE 0 END) AS q1_count,
                   SUM(CASE WHEN best_quartile = 'Q2' THEN 1 ELSE 0 END) AS q2_count,
                   SUM(CASE WHEN best_quartile = 'Q3' THEN 1 ELSE 0 END) AS q3_count,
                   SUM(CASE WHEN best_quartile = 'Q4' THEN 1 ELSE 0 END) AS q4_count
            FROM journals
            WHERE publisher IS NOT NULL AND publisher != ''
            GROUP BY publisher
            ORDER BY total_journals DESC
            LIMIT 15
        """
        data = execute_query(conn, query)
        return jsonify({'publishers': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

@charts_bp.route('/scatter/metrics', methods=['GET'])
@cache.cached(timeout=3600)
def get_metrics_scatter():
    conn = get_db_connection()
    try:
        query = """
            SELECT title, total_docs, citable_docs_3y, sjr_index, best_quartile
            FROM journals
            WHERE total_docs IS NOT NULL AND citable_docs_3y IS NOT NULL
            ORDER BY total_docs DESC
            LIMIT 500
        """
        data = execute_query(conn, query)
        return jsonify({'scatter': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()
