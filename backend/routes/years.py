from flask import Blueprint, jsonify, request
from db import get_db_connection, execute_query

years_bp = Blueprint('years', __name__)

@years_bp.route('/<int:year>/profile', methods=['GET'])
def get_profile(year):
    conn = get_db_connection()
    try:
        profile = execute_query(conn, "SELECT * FROM vw_year_profile WHERE year = %s", (year,), fetchone=True)
        if not profile:
            return jsonify({'error': 'Not found'}), 404
        
        return jsonify(profile)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@years_bp.route('/<int:year>/papers', methods=['GET'])
def get_papers(year):
    limit = request.args.get('limit', default=100, type=int)
    conn = get_db_connection()
    try:
        query = """
            SELECT p.paper_id, p.title, p.type, 
                   IFNULL(c.acronym, j.title) as venue_name
            FROM papers p
            LEFT JOIN conferences c ON p.conf_id = c.conf_id
            LEFT JOIN journals j ON p.journal_id = j.journal_id
            WHERE p.year = %s
            ORDER BY p.paper_id DESC LIMIT %s
        """
        papers = execute_query(conn, query, (year, limit))
        return jsonify({'papers': papers})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
