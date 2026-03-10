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
