from flask import Blueprint, jsonify, request
from db import get_db_connection, execute_query

conferences_bp = Blueprint('conferences', __name__)

@conferences_bp.route('/', methods=['GET'])
def list_conferences():
    """List all mapped conferences with pagination."""
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=50, type=int)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        count_res = execute_query(conn, "SELECT COUNT(*) as total FROM conferences", fetchone=True)
        total_confs = count_res['total'] if count_res else 0

        confs = execute_query(
            conn, 
            "SELECT conf_id, title, acronym, rank FROM conferences ORDER BY acronym LIMIT %s OFFSET %s",
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
def search_conferences():
    """Server-side search for conferences using Full-Text index."""
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    
    conn = get_db_connection()
    try:
        results = execute_query(
            conn,
            "SELECT conf_id, title, acronym FROM conferences WHERE MATCH(title, acronym) AGAINST(%s IN BOOLEAN MODE) ORDER BY acronym LIMIT 15",
            (f"+{q}*",)
        )
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
