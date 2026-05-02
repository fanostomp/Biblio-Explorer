from flask import Blueprint, jsonify, request

if __package__ and __package__.startswith("backend."):
    from ..db import get_db_connection, execute_query
else:
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
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', type=int)
    if per_page is None:
        per_page = request.args.get('limit', default=100, type=int)

    page = max(page or 1, 1)
    per_page = max(per_page or 100, 1)
    offset = (page - 1) * per_page

    conf_id = request.args.get('conf_id', type=int)
    journal_id = request.args.get('journal_id', type=int)
    author_id = request.args.get('author_id', type=int)

    joins = []
    where_clauses = ["p.year = %s"]
    params = [year]

    if conf_id is not None:
        where_clauses.append("p.conf_id = %s")
        params.append(conf_id)

    if journal_id is not None:
        where_clauses.append("p.journal_id = %s")
        params.append(journal_id)

    if author_id is not None:
        joins.append("JOIN paper_authors pa ON pa.paper_id = p.paper_id")
        where_clauses.append("pa.author_id = %s")
        params.append(author_id)

    from_clause = f"""
        FROM papers p
        {' '.join(joins)}
        LEFT JOIN conferences c ON p.conf_id = c.conf_id
        LEFT JOIN journals j ON p.journal_id = j.journal_id
        WHERE {' AND '.join(where_clauses)}
    """
    conn = get_db_connection()
    try:
        count_query = f"""
            SELECT COUNT(DISTINCT p.paper_id) AS total
            {from_clause}
        """
        count_res = execute_query(conn, count_query, tuple(params), fetchone=True)
        total_records = count_res['total'] if count_res else 0

        data_query = f"""
            SELECT DISTINCT p.paper_id, p.title, p.type,
                   IFNULL(c.acronym, j.title) AS venue_name
            {from_clause}
            ORDER BY p.paper_id DESC LIMIT %s OFFSET %s
        """
        papers = execute_query(conn, data_query, tuple(params + [per_page, offset]))

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
