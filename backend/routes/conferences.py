from flask import Blueprint, jsonify, request
import re

if __package__ and __package__.startswith("backend."):
    from ..db import get_db_connection, execute_query
    from ..extensions import limiter
else:
    from db import get_db_connection, execute_query
    from extensions import limiter

conferences_bp = Blueprint('conferences', __name__)

@conferences_bp.route('/lookups', methods=['GET'])
def get_lookups():
    """Get available ranks and categories for filters."""
    conn = get_db_connection()
    try:
        ranks = [{'id': r, 'name': r} for r in ['A*', 'A', 'B', 'C']]
        categories = execute_query(conn, "SELECT for_code as id, description as name FROM primary_for ORDER BY name")
        return jsonify({
            'ranks': ranks,
            'categories': categories
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@conferences_bp.route('/', methods=['GET'])
def list_conferences():
    """List all mapped conferences with pagination."""
    page = max(request.args.get('page', default=1, type=int), 1)
    per_page = min(max(request.args.get('per_page', default=50, type=int), 1), 200)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        count_res = execute_query(conn, "SELECT COUNT(*) as total FROM conferences", fetchone=True)
        total_confs = count_res['total'] if count_res else 0

        confs = execute_query(
            conn, 
            "SELECT conf_id, title, acronym, `rank` FROM conferences ORDER BY acronym LIMIT %s OFFSET %s",
            (per_page, offset)
        )
        return jsonify({
            'conferences': confs,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_records': total_confs,
                'total_pages': (total_confs + per_page - 1) // per_page
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@conferences_bp.route('/<int:conf_id>/profile', methods=['GET'])
def get_profile(conf_id):
    conn = get_db_connection()
    try:
        profile = execute_query(conn, "SELECT * FROM vw_conf_profile WHERE conf_id = %s", (conf_id,), fetchone=True)
        if not profile:
            return jsonify({'error': 'Not found'}), 404
            
        yearly_stats = execute_query(
            conn, 
            "SELECT * FROM vw_conf_yearly_stats WHERE conf_id = %s ORDER BY year ASC", 
            (conf_id,)
        )
        
        return jsonify({
            'profile': profile,
            'yearly_stats': yearly_stats
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@conferences_bp.route('/<int:conf_id>/papers', methods=['GET'])
def get_papers(conf_id):
    start_year = request.args.get('start_year', type=int)
    end_year = request.args.get('end_year', type=int)
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=50, type=int)
    offset = (page - 1) * per_page
    
    base_query = "FROM papers WHERE conf_id = %s"
    params = [conf_id]
    
    if start_year:
        base_query += " AND year >= %s"
        params.append(start_year)
    if end_year:
        base_query += " AND year <= %s"
        params.append(end_year)
        
    conn = get_db_connection()
    try:
        # Get total
        count_query = "SELECT COUNT(*) as total " + base_query
        count_res = execute_query(conn, count_query, tuple(params), fetchone=True)
        total_records = count_res['total'] if count_res else 0

        # Get data
        data_query = "SELECT paper_id, title, year, pages, ee, url " + base_query + " ORDER BY year DESC LIMIT %s OFFSET %s"
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

@conferences_bp.route('/search', methods=['GET'])
@limiter.limit("30 per minute")
def search_conferences():
    """Server-side search for conferences with filters and pagination."""
    q = request.args.get('q', '').strip()
    rank = request.args.get('rank', '').strip()
    category = request.args.get('category', '').strip()
    with_dblp_coverage = request.args.get('with_dblp_coverage', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    page = max(request.args.get('page', default=1, type=int), 1)
    per_page = min(max(request.args.get('per_page', default=10, type=int), 1), 100)
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
            where_clauses.append("(acronym LIKE %s OR title LIKE %s)")
            params.extend([f"%{safe_q}%", f"%{safe_q}%"])
        else:
            where_clauses.append("MATCH(title, acronym) AGAINST(%s IN BOOLEAN MODE)")
            terms = [f"+{term}*" for term in safe_q.split() if term]
            params.append(" ".join(terms))
    
    if rank:
        where_clauses.append("`rank` = %s")
        params.append(rank)
    if category:
        # DB stores 4-digit codes, but lookups may provide 6-digit subcategories.
        # Truncate to 4 digits to match the parent group.
        short_cat = category[:4]
        where_clauses.append("primary_for = %s")
        params.append(short_cat)
    if with_dblp_coverage:
        where_clauses.append("EXISTS (SELECT 1 FROM papers p WHERE p.conf_id = c.conf_id)")

    # SAFETY: where_sql is built entirely from hardcoded column names;
    # all user-supplied values go through parameterized %s placeholders.
    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    conn = get_db_connection()
    try:
        # Get total for pagination
        count_sql = f"SELECT COUNT(*) as total FROM conferences c{where_sql}"
        count_res = execute_query(conn, count_sql, tuple(params), fetchone=True)
        total_records = count_res['total'] if count_res else 0

        # Get results
        query_sql = f"""
            SELECT
                c.conf_id,
                c.title,
                c.acronym,
                c.`rank`,
                c.primary_for,
                EXISTS (SELECT 1 FROM papers p WHERE p.conf_id = c.conf_id) AS has_dblp_coverage
            FROM conferences c{where_sql}
            ORDER BY c.acronym
            LIMIT %s OFFSET %s
        """
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

@conferences_bp.route('/<int:conf_id>/top_authors', methods=['GET'])
def get_top_authors(conf_id):
    limit = request.args.get('limit', default=10, type=int)
    conn = get_db_connection()
    try:
        query = """
            SELECT a.author_id, a.name, COUNT(pa.paper_id) as paper_count
            FROM authors a
            JOIN paper_authors pa ON a.author_id = pa.author_id
            JOIN papers p ON pa.paper_id = p.paper_id
            WHERE p.conf_id = %s
            GROUP BY a.author_id, a.name
            ORDER BY paper_count DESC
            LIMIT %s
        """
        results = execute_query(conn, query, (conf_id, limit))
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
