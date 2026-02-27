from flask import Blueprint, jsonify, request
from db import get_db_connection, execute_query

authors_bp = Blueprint('authors', __name__)

@authors_bp.route('/search', methods=['GET'])
def search_authors():
    """Server-side search for authors (needed because there are 1.4M+ authors)."""
    q = request.args.get('q', '')
    if len(q) < 3:
        return jsonify([])
    conn = get_db_connection()
    try:
        results = execute_query(
            conn,
            "SELECT author_id, name FROM authors WHERE MATCH(name) AGAINST(%s IN BOOLEAN MODE) ORDER BY name LIMIT 15",
            (f"+{q}*",)
        )
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@authors_bp.route('/<int:author_id>/profile', methods=['GET'])
def get_profile(author_id):
    conn = get_db_connection()
    try:
        profile = execute_query(conn, "SELECT * FROM vw_author_profile WHERE author_id = %s", (author_id,), fetchone=True)
        if not profile:
            return jsonify({'error': 'Not found'}), 404
            
        # Improved stats for charting (breakdown by year, including total and type counts)
        yearly_stats = execute_query(
            conn, 
            """SELECT year, 
                      COUNT(*) as total_count,
                      SUM(CASE WHEN type = 'conference' THEN 1 ELSE 0 END) as conf_count,
                      SUM(CASE WHEN type = 'journal' THEN 1 ELSE 0 END) as journal_count
               FROM papers p 
               JOIN paper_authors pa ON p.paper_id = pa.paper_id 
               WHERE pa.author_id = %s 
               GROUP BY year 
               ORDER BY year ASC""", 
            (author_id,)
        )
        
        return jsonify({
            'profile': profile,
            'yearly_stats': yearly_stats
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@authors_bp.route('/<int:author_id>/papers', methods=['GET'])
def get_papers(author_id):
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=50, type=int)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        # Get total count for pagination info
        count_res = execute_query(conn, "SELECT COUNT(*) as total FROM paper_authors WHERE author_id = %s", (author_id,), fetchone=True)
        total_papers = count_res['total'] if count_res else 0

        papers = execute_query(
            conn, 
            """SELECT p.paper_id, p.title, p.year, p.type, 
                      c.acronym as conf_acronym, j.title as journal_title 
               FROM papers p 
               JOIN paper_authors pa ON p.paper_id = pa.paper_id 
               LEFT JOIN conferences c ON p.conf_id = c.conf_id 
               LEFT JOIN journals j ON p.journal_id = j.journal_id 
               WHERE pa.author_id = %s 
               ORDER BY p.year DESC 
               LIMIT %s OFFSET %s""", 
            (author_id, per_page, offset)
        )
        return jsonify({
            'papers': papers,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_records': total_papers,
                'total_pages': (total_papers + per_page - 1) // per_page
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
