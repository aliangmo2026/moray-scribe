"""Application factory wiring blueprints and the session hook."""

from __future__ import annotations

import os

from flask import Flask, g

from .auth import bp as auth_bp
from .orders import bp as orders_bp


def create_app() -> Flask:
    """Build the demo application."""
    app = Flask(__name__)
    # The demo never ships a real secret: read it from the environment and fall
    # back to an obviously non-production placeholder.
    app.secret_key = os.environ.get("FLASK_DEMO_SECRET_KEY", "dev-only-not-a-secret")
    app.register_blueprint(auth_bp)
    app.register_blueprint(orders_bp)

    @app.before_request
    def attach_session():
        """Expose the SQLAlchemy session on the request context."""
        g.db_session = None
        return None

    @app.route("/health")
    def health():
        """Liveness probe."""
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
