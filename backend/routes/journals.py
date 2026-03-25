from flask import Blueprint, jsonify, request
from db import get_db_connection, execute_query
import re

journals_bp = Blueprint('journals', __name__)

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
    """Server-side search for journals using Full-Text index with input sanitization."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])

    # Remove special chars that break MySQL Boolean Full-Text search
    safe_q = re.sub(r'[^\w\s]', ' ', q).strip()
    if not safe_q:
        return jsonify([])

    conn = get_db_connection()
    try:
        if len(safe_q) <= 3:
            # Use LIKE for short queries (safer, no full-text needed)
            query_sql = "SELECT journal_id, title FROM journals WHERE title LIKE %s ORDER BY title LIMIT 15"
            params = (f"%{safe_q}%",)
        else:
            # Use Full-Text search with sanitized input
            query_sql = "SELECT journal_id, title FROM journals WHERE MATCH(title) AGAINST(%s IN BOOLEAN MODE) ORDER BY title LIMIT 15"
            # Split into terms and prepend + to each
            terms = [f"+{term}*" for term in safe_q.split() if term]
            boolean_expr = " ".join(terms)
            params = (boolean_expr,)

        results = execute_query(conn, query_sql, params)
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
