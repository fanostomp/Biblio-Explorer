import logging
import os
import time
from flask import Flask, g, jsonify, render_template, request

if __package__:
    from backend.config import DB_CONFIG, CACHE_CONFIG, FLASK_DEBUG
    from backend.db import get_db_connection, init_pool
    from backend.extensions import cache, limiter
    from backend.routes.conferences import conferences_bp
    from backend.routes.journals import journals_bp
    from backend.routes.authors import authors_bp
    from backend.routes.years import years_bp
    from backend.routes.charts import charts_bp, stats_bp
    from backend.routes.papers import papers_bp
else:
    from config import DB_CONFIG, CACHE_CONFIG, FLASK_DEBUG
    from db import get_db_connection, init_pool
    from extensions import cache, limiter
    from routes.conferences import conferences_bp
    from routes.journals import journals_bp
    from routes.authors import authors_bp
    from routes.years import years_bp
    from routes.charts import charts_bp, stats_bp
    from routes.papers import papers_bp

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
logger = logging.getLogger(__name__)


def configure_logging(level=logging.INFO):
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    else:
        root_logger.setLevel(level)


def create_app():
    configure_logging()
    # Point Flask to our structured frontend/ folders
    base_dir = os.path.abspath(os.path.dirname(__file__))
    frontend_dir = os.path.join(base_dir, '..', 'frontend')
    
    app = Flask(__name__, 
                template_folder=os.path.join(frontend_dir, 'templates'),
                static_folder=os.path.join(frontend_dir, 'static'))

    app.config['DB_CONFIG'] = DB_CONFIG
    app.config.from_mapping(CACHE_CONFIG)
    
    # Initialize the database connection pool
    init_pool(app.config['DB_CONFIG'], pool_size=10)

    # Initialize Cache
    cache.init_app(app)
    limiter.init_app(app)

    # Register API blueprints
    app.register_blueprint(conferences_bp, url_prefix='/api/conference')
    app.register_blueprint(journals_bp, url_prefix='/api/journal')
    app.register_blueprint(authors_bp, url_prefix='/api/author')
    app.register_blueprint(years_bp, url_prefix='/api/year')
    app.register_blueprint(charts_bp, url_prefix='/api/charts')
    app.register_blueprint(stats_bp, url_prefix='/api/stats')
    app.register_blueprint(papers_bp, url_prefix='/api/paper')

    # Frontend routes
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/conference')
    def conference_page():
        return render_template('conference.html')

    @app.route('/journal')
    def journal_page():
        return render_template('journal.html')

    @app.route('/author')
    def author_page():
        return render_template('author.html')

    @app.route('/year')
    def year_page():
        return render_template('year.html')

    @app.route('/charts')
    def charts_page():
        return render_template('charts.html')
    
    @app.route('/trends')
    def trends_page():
        return render_template('trends.html')

    @app.route('/papers')
    def papers_page():
        return render_template('papers.html')

    @app.route('/health')
    def health():
        try:
            conn = get_db_connection()
            conn.ping(reconnect=True)
            conn.close()
            return jsonify({'status': 'ok', 'db': 'connected'}), 200
        except Exception as e:
            logger.exception("Health check failed")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.before_request
    def log_request_start():
        g.request_started = time.perf_counter()

    @app.after_request
    def add_header(r):
        started = getattr(g, "request_started", None)
        duration_ms = ((time.perf_counter() - started) * 1000) if started is not None else -1.0
        r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        r.headers["Pragma"] = "no-cache"
        r.headers["Expires"] = "0"
        logger.info(
            "Request %s %s -> %s in %.2f ms",
            request.method,
            request.path,
            r.status_code,
            duration_ms,
        )
        return r

    logger.info("Flask app initialized")
    return app

if __name__ == '__main__':
    app = create_app()
    logger.info("Starting Flask app on port 5000")
    app.run(debug=FLASK_DEBUG, port=5000)
