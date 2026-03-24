from flask import Blueprint, jsonify, request
from db import get_db_connection, execute_query
from extensions import cache

charts_bp = Blueprint('charts', __name__)
stats_bp = Blueprint('stats', __name__)


def _get_stats_overview_payload(conn):
    counts = {}
    for key, table in (
        ('total_papers', 'papers'),
        ('total_authors', 'authors'),
        ('total_conferences', 'conferences'),
        ('total_journals', 'journals'),
    ):
        row = execute_query(
            conn,
            f"SELECT COUNT(*) AS {key} FROM {table}",
            fetchone=True,
        )
        counts[key] = row[key] if row else 0
    return counts

@charts_bp.route('/overview', methods=['GET'])
@cache.cached(timeout=3600)
def get_overview():
    """Real data for the homepage chart — total papers per year."""
    conn = get_db_connection()
    try:
        query = """
        SELECT year, COUNT(*) AS num_papers
        FROM papers
        WHERE year >= 1900 AND year <= YEAR(CURRENT_DATE())
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

@charts_bp.route('/stats/overview', methods=['GET'])
@cache.cached(timeout=3600)
def get_stats_overview():
    """Compatibility alias for dashboard stats."""
    conn = get_db_connection()
    try:
        return jsonify(_get_stats_overview_payload(conn))
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()


@stats_bp.route('/overview', methods=['GET'])
@cache.cached(timeout=3600)
def get_stats_overview_api():
    """Real stats for the dashboard: total papers, authors, conferences, journals."""
    conn = get_db_connection()
    try:
        return jsonify(_get_stats_overview_payload(conn))
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


@charts_bp.route('/venues/bar', methods=['GET'])
@cache.cached(timeout=3600, query_string=True)
def get_venues_bar():
    """
    Get bar chart data for selected venues.

    Query params:
        ids: comma-separated venue IDs (e.g., "1,2,3")
        type: "conference" or "journal"
    """
    ids_param = request.args.get('ids', '')
    venue_type = request.args.get('type', 'conference')

    # Validation
    if not ids_param:
        return jsonify({'error': 'Missing ids parameter'}), 400

    # Filter to valid integers only; silently ignores non-numeric tokens
    venue_ids = [int(x.strip()) for x in ids_param.split(',') if x.strip().isdigit()]

    if not venue_ids:
        return jsonify({'error': 'No valid venue IDs provided'}), 400

    if len(venue_ids) > 20:
        return jsonify({'error': 'Maximum 20 venues allowed'}), 400

    if venue_type not in ('conference', 'journal'):
        return jsonify({'error': 'type must be conference or journal'}), 400

    # Build parameterized query with explicit IN clause marker
    in_clause = ','.join(['%s'] * len(venue_ids))

    conn = get_db_connection()
    try:
        if venue_type == 'conference':
            query = """
            SELECT
                c.conf_id AS venue_id,
                c.acronym AS venue_label,
                c.title AS venue_name,
                COALESCE(p.total_papers, 0) AS total_papers,
                COALESCE(p.avg_papers_per_year, 0) AS avg_papers_per_year,
                COALESCE(p.distinct_authors, 0) AS distinct_authors,
                ROUND(COALESCE(p.avg_authors_per_paper, 0)
                    * COALESCE(p.avg_papers_per_year, 0), 1) AS avg_authors_per_year
            FROM conferences c
            LEFT JOIN vw_conf_profile p ON p.conf_id = c.conf_id
            WHERE c.conf_id IN ({IN_CLAUSE})
            ORDER BY total_papers DESC
            """.replace('{IN_CLAUSE}', in_clause)
        else:
            query = """
            SELECT
                j.journal_id AS venue_id,
                j.title AS venue_label,
                j.title AS venue_name,
                COALESCE(p.total_papers, 0) AS total_papers,
                COALESCE(p.avg_papers_per_year, 0) AS avg_papers_per_year,
                COALESCE(p.distinct_authors, 0) AS distinct_authors,
                ROUND(COALESCE(p.avg_authors_per_paper, 0)
                    * COALESCE(p.avg_papers_per_year, 0), 1) AS avg_authors_per_year
            FROM journals j
            LEFT JOIN vw_journal_profile p ON p.journal_id = j.journal_id
            WHERE j.journal_id IN ({IN_CLAUSE})
            ORDER BY total_papers DESC
            """.replace('{IN_CLAUSE}', in_clause)

        data = execute_query(conn, query, tuple(venue_ids))
        return jsonify({'venues': data})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()


@charts_bp.route('/category/conference', methods=['GET'])
@cache.cached(timeout=3600, query_string=True)
def get_category_conference():
    """
    Get conference category data for linecharts.

    Query params:
        for_codes: optional comma-separated list of PrimaryFoR codes (e.g., "4605,4606")
        list_only: if true, returns only code and description without chart data.
    """
    for_codes = request.args.get('for_codes', '')
    list_only = request.args.get('list_only', 'false').lower() == 'true'

    conn = get_db_connection()
    try:
        if list_only:
            query = """
            SELECT DISTINCT c.primary_for AS code, pf.description 
            FROM conferences c
            JOIN primary_for pf ON pf.for_code = c.primary_for
            ORDER BY pf.description ASC
            """
            data = execute_query(conn, query)
            return jsonify({'categories': data})

        if for_codes:
            # Filter to specific categories (keep as string to preserve leading zeros)
            code_list = [c.strip() for c in for_codes.split(',') if c.strip().isalnum()]
            if not code_list:
                return jsonify({'categories': []})
            
            in_clause = ','.join(['%s'] * len(code_list))
            query = f"""
            SELECT
                for_code AS code,
                description,
                year,
                conf_count,
                paper_count
            FROM vw_category_yearly_conf
            WHERE for_code IN ({in_clause})
            ORDER BY year ASC
            """
            data = execute_query(conn, query, tuple(code_list))
        else:
            # All categories
            query = """
            SELECT
                for_code AS code,
                description,
                year,
                conf_count,
                paper_count
            FROM vw_category_yearly_conf
            ORDER BY for_code, year ASC
            """
            data = execute_query(conn, query)

        # Group by category & normalize response format
        categories = {}
        for row in data:
            code = row['code']
            if code not in categories:
                categories[code] = {
                    'code': code,
                    'description': row.get('description', ''),
                    'yearly_data': []
                }
            categories[code]['yearly_data'].append({
                'year': row['year'],
                'venue_count': row['conf_count'],  # Normalized to venue_count
                'paper_count': row['paper_count']
            })

        return jsonify({'categories': list(categories.values())})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()


@charts_bp.route('/category/journal', methods=['GET'])
@cache.cached(timeout=3600, query_string=True)
def get_category_journal():
    """
    Get journal category data for linecharts.

    Query params:
        area_ids: optional comma-separated list of subject area IDs (e.g., "3,4")
        list_only: if true, returns only code and description without chart data.
    """
    area_ids = request.args.get('area_ids', '')
    list_only = request.args.get('list_only', 'false').lower() == 'true'

    conn = get_db_connection()
    try:
        if list_only:
            query = """
            SELECT DISTINCT j.best_subject_area AS code, bsa.area_name AS description
            FROM journals j
            JOIN best_subject_area bsa ON bsa.area_id = j.best_subject_area
            ORDER BY bsa.area_name ASC
            """
            data = execute_query(conn, query)
            return jsonify({'categories': data})

        if area_ids:
            code_list = [int(c.strip()) for c in area_ids.split(',') if c.strip().isdigit()]
            if not code_list:
                return jsonify({'categories': []})

            in_clause = ','.join(['%s'] * len(code_list))
            query = f"""
            SELECT
                area_id AS code,
                area_name AS description,
                year,
                journal_count,
                paper_count
            FROM vw_category_yearly_journal
            WHERE area_id IN ({in_clause})
            ORDER BY year ASC
            """
            data = execute_query(conn, query, tuple(code_list))
        else:
            query = """
            SELECT
                area_id AS code,
                area_name AS description,
                year,
                journal_count,
                paper_count
            FROM vw_category_yearly_journal
            ORDER BY area_id, year ASC
            """
            data = execute_query(conn, query)

        # Group by category & normalize response format
        categories = {}
        for row in data:
            code = row['code']
            if code not in categories:
                categories[code] = {
                    'code': code,
                    'description': row.get('description', ''),
                    'yearly_data': []
                }
            categories[code]['yearly_data'].append({
                'year': row['year'],
                'venue_count': row['journal_count'],  # Normalized to venue_count
                'paper_count': row['paper_count']
            })

        return jsonify({'categories': list(categories.values())})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()
