from flask import Blueprint, jsonify, request
from db import get_db_connection, execute_query
import re

journals_bp = Blueprint('journals', __name__)

@journals_bp.route('/lookups', methods=['GET'])
def get_lookups():
    """Get available quartiles and subject areas for filters."""
    conn = get_db_connection()
    try:
        quartiles = [{'id': q, 'name': q} for q in ['Q1', 'Q2', 'Q3', 'Q4']]
        subject_areas = execute_query(conn, "SELECT area_id as id, area_name as name FROM best_subject_area ORDER BY name")
        return jsonify({
            'quartiles': quartiles,
            'subject_areas': subject_areas
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@journals_bp.route('/', methods=['GET'])
def list_journals():
    """List all journals for autocomplete and browsing with pagination."""
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=50, type=int)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        count_res = execute_query(conn, "SELECT COUNT(*) as total FROM journals", fetchone=True)
        total_journals = count_res['total'] if count_res else 0

        journals = execute_query(
            conn,
            "SELECT journal_id, title, publisher, best_quartile, sjr_index FROM journals ORDER BY sjr_index DESC LIMIT %s OFFSET %s",
            (per_page, offset)
        )
        return jsonify({
            'journals': journals,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_records': total_journals,
                'total_pages': (total_journals + per_page - 1) // per_page
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@journals_bp.route('/<int:journal_id>/profile', methods=['GET'])
def get_profile(journal_id):
    conn = get_db_connection()
    try:
        profile = execute_query(conn, "SELECT * FROM vw_journal_profile WHERE journal_id = %s", (journal_id,), fetchone=True)
        if not profile:
            return jsonify({'error': 'Not found'}), 404

        yearly_stats = execute_query(
            conn,
            "SELECT * FROM vw_journal_yearly_stats WHERE journal_id = %s ORDER BY year ASC",
            (journal_id,)
        )
        has_dblp_coverage = bool((profile.get('total_papers') or 0) > 0)

        return jsonify({
            'profile': profile,
            'yearly_stats': yearly_stats,
            'has_dblp_coverage': has_dblp_coverage,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@journals_bp.route('/<int:journal_id>/papers', methods=['GET'])
def get_papers(journal_id):
    start_year = request.args.get('start_year', type=int)
    end_year = request.args.get('end_year', type=int)
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=50, type=int)
    offset = (page - 1) * per_page

    base_query = "FROM papers WHERE journal_id = %s"
    params = [journal_id]

    if start_year:
        base_query += " AND year >= %s"
        params.append(start_year)
    if end_year:
        base_query += " AND year <= %s"
        params.append(end_year)

    conn = get_db_connection()
    try:
        # Get total for this filter
        count_query = "SELECT COUNT(*) as total " + base_query
        count_res = execute_query(conn, count_query, tuple(params), fetchone=True)
        total_records = count_res['total'] if count_res else 0

        # Get data
        data_query = "SELECT paper_id, title, year, volume, number, pages, ee, url " + base_query + " ORDER BY year DESC LIMIT %s OFFSET %s"
        data_params = params + [per_page, offset]

        papers = execute_query(conn, data_query, tuple(data_params))
        return jsonify({
            'papers': papers,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_records': total_records,
                'total_pages': (total_records + per_page - 1) // per_page
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@journals_bp.route('/search', methods=['GET'])
def search_journals():
    """Server-side search for journals with filters and pagination."""
    q = request.args.get('q', '').strip()
    quartile = request.args.get('quartile', '').strip()
    area_id = request.args.get('subject_area', '').strip()
    publisher = request.args.get('publisher', '').strip()
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=10, type=int)
    offset = (page - 1) * per_page

    where_clauses = []
    params = []

    if q:
        safe_q = re.sub(r'[^\w\s]', ' ', q).strip()
        if not safe_q:
            return jsonify({
                'results': [],
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total_records': 0,
                    'total_pages': 0
                }
            })
            
        if len(safe_q) <= 3:
            where_clauses.append("title LIKE %s")
            params.append(f"%{safe_q}%")
        else:
            where_clauses.append("MATCH(title) AGAINST(%s IN BOOLEAN MODE)")
            terms = [f"+{term}*" for term in safe_q.split() if term]
            params.append(" ".join(terms))
    
    if quartile:
        where_clauses.append("best_quartile = %s")
        params.append(quartile)
    if area_id:
        where_clauses.append("best_subject_area = %s")
        params.append(area_id)
    if publisher:
        where_clauses.append("publisher LIKE %s")
        params.append(f"%{publisher}%")

    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    conn = get_db_connection()
    try:
        # Get total for pagination
        count_sql = f"SELECT COUNT(*) as total FROM journals{where_sql}"
        count_res = execute_query(conn, count_sql, tuple(params), fetchone=True)
        total_records = count_res['total'] if count_res else 0

        # Get results
        query_sql = f"SELECT journal_id, title, publisher, best_quartile, sjr_index FROM journals{where_sql} ORDER BY title LIMIT %s OFFSET %s"
        results = execute_query(conn, query_sql, tuple(params + [per_page, offset]))

        return jsonify({
            'results': results,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_records': total_records,
                'total_pages': (total_records + per_page - 1) // per_page
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
