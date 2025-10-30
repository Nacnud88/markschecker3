from flask import Flask

from .config import load_config
from .db import init_db
from .routes import api_bp, pages_bp


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    cfg = load_config()

    app.config.update(
        SECRET_KEY=cfg.secret_key,
        MARKSCHECKER_CONFIG=cfg,
    )

    with app.app_context():
        init_db(cfg.database_path)

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(pages_bp)

    return app
