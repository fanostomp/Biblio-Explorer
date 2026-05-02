import os
from flask import Flask, jsonify, render_template
from flask_caching import Cache
if __package__:
    from .config import DB_CONFIG, CACHE_CONFIG, FLASK_DEBUG
    from .db import get_db_connection, init_pool
    from .extensions import cache
    from .routes.conferences import conferences_bp
    from .routes.journals import journals_bp
    from .routes.authors import authors_bp
    from .routes.years import years_bp
    from .routes.charts import charts_bp, stats_bp
    from .routes.papers import papers_bp
else:
    from config import DB_CONFIG, CACHE_CONFIG, FLASK_DEBUG
    from db import get_db_connection, init_pool
    from extensions import cache
    from routes.conferences import conferences_bp
    from routes.journals import journals_bp
    from routes.authors import authors_bp
    from routes.years import years_bp
    from routes.charts import charts_bp, stats_bp
    from routes.papers import papers_bp

def create_app():
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
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.after_request
    def add_header(r):
        r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        r.headers["Pragma"] = "no-cache"
        r.headers["Expires"] = "0"
        return r

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=FLASK_DEBUG, port=5000)
