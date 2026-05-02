from flask import Blueprint, current_app, jsonify, request
import re

if __package__ and __package__.startswith("backend."):
    from ..db import get_db_connection, execute_query
else:
    from db import get_db_connection, execute_query

papers_bp = Blueprint('papers', __name__)

# H-2 / M-4: whitelist of accepted venue_type values
_VALID_VENUE_TYPES = {'', 'conference', 'journal'}


@papers_bp.route('/search', methods=['GET'])
def search_papers():
    """Cross-venue paper/article title search with optional filters."""
    q = request.args.get('q', '').strip()

    # M-4: reject unknown venue_type values
    venue_type = request.args.get('venue_type', '').strip().lower()
    if venue_type not in _VALID_VENUE_TYPES:
        return jsonify({'error': 'Invalid venue_type. Must be conference, journal, or empty.'}), 400

    start_year = request.args.get('start_year', type=int)
    end_year   = request.args.get('end_year',   type=int)

    # M-3: silently swap inverted year range
    if start_year and end_year and start_year > end_year:
        start_year, end_year = end_year, start_year

    page     = max(request.args.get('page',     default=1,  type=int), 1)
    per_page = min(max(request.args.get('per_page', default=15, type=int), 1), 100)
    offset   = (page - 1) * per_page

    if not any([q, venue_type, start_year, end_year]):
        return jsonify({
            'results': [],
            'pagination': {
                'page': page, 'per_page': per_page,
                'total_records': 0, 'total_pages': 0
            }
        })

    where_clauses = []
    params        = []

    # --- Title keyword filter ---
    if q:
        safe_q = re.sub(r'[^\w\s]', ' ', q).strip()
        if not safe_q:
            return jsonify({
                'results': [],
                'pagination': {
                    'page': page, 'per_page': per_page,
                    'total_records': 0, 'total_pages': 0
                }
            })
        if len(safe_q) <= 3:
            where_clauses.append("p.title LIKE %s")
            params.append(f"%{safe_q}%")
        else:
            where_clauses.append("MATCH(p.title) AGAINST(%s IN BOOLEAN MODE)")
            terms = [f"+{term}*" for term in safe_q.split() if term]
            params.append(" ".join(terms))

    # --- Venue-type filter ---
    if venue_type == 'conference':
        where_clauses.append("p.type = 'conference'")
    elif venue_type == 'journal':
        where_clauses.append("p.type = 'journal'")

    # --- Year range filter ---
    if start_year:
        where_clauses.append("p.year >= %s")
        params.append(start_year)
    if end_year:
        where_clauses.append("p.year <= %s")
        params.append(end_year)

    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    conn = get_db_connection()
    try:
        # Count
        count_sql = f"SELECT COUNT(*) as total FROM papers p{where_sql}"
        count_res = execute_query(conn, count_sql, tuple(params), fetchone=True)
        total_records = count_res['total'] if count_res else 0

        # H-1: use plain column access — COALESCE(x, NULL) was a no-op
        query_sql = f"""
            SELECT
                p.paper_id,
                p.title,
                p.year,
                p.pages,
                p.ee,
                p.url,
                p.volume,
                p.number,
                p.type AS venue_type,
                COALESCE(c.acronym, c.title, j.title) AS venue_name,
                c.conf_id    AS conf_id,
                j.journal_id AS journal_id
            FROM papers p
            LEFT JOIN conferences c ON p.conf_id    = c.conf_id
            LEFT JOIN journals    j ON p.journal_id = j.journal_id
            {where_sql}
            ORDER BY p.year DESC, p.title ASC, p.paper_id ASC
            LIMIT %s OFFSET %s
        """
        results = execute_query(conn, query_sql, tuple(params + [per_page, offset]))

        return jsonify({
            'results': results,
            'pagination': {
                'page':         page,
                'per_page':     per_page,
                'total_records': total_records,
                'total_pages':  (total_records + per_page - 1) // per_page if total_records else 0
            }
        })
    except Exception:
        # L-4: log full traceback server-side; never leak raw exception to client
        current_app.logger.exception("search_papers failed")
        return jsonify({'error': 'Internal server error'}), 500
    finally:
        conn.close()
