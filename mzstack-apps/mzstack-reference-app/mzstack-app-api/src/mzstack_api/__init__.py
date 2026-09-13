"""The mzstack web API.

A thin HTTP layer over the `mzstack` library: it finds datasets, exposes the
library's filters, queries and peak arrays as JSON, and runs the long ingests
in the background. It stores nothing of its own -- the datasets on disk are
the state.

Run it with::

    flask --app mzstack_api run --port 5000
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from .config import Config
from .errors import register_error_handlers
from .extensions import init_extensions

__all__ = ["create_app"]
__version__ = "0.1.0"


def create_app(config: Config | None = None) -> Flask:
    """Build the application.

    Args:
        config: overrides the environment-derived defaults. Tests pass one
            pointing at a temporary workspace.
    """
    app = Flask(__name__)
    cfg = config or Config()
    cfg.ensure_workspace()

    logging.basicConfig(
        level=os.environ.get("MZSTACK_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # The UI is normally served by Vite, on another port, which makes every
    # call cross-origin. In production it is served from `dist` below and
    # this does nothing.
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    init_extensions(app, cfg)
    register_error_handlers(app)

    from .api import register_blueprints

    register_blueprints(app)
    _serve_ui(app)

    app.logger.info("workspace: %s", cfg.workspace)
    return app


def _serve_ui(app: Flask) -> None:
    """Serve the built frontend, when there is one.

    In development Vite serves the UI and proxies `/api` here, so this is
    inert. After `npm run build` the same server serves both, which makes a
    deployment one process.
    """
    dist = Path(
        os.environ.get(
            "MZSTACK_UI_DIST",
            Path(__file__).resolve().parents[3] / "mzstack-app-ui" / "dist",
        )
    )

    @app.get("/")
    @app.get("/<path:requested>")
    def ui(requested: str = ""):
        if not dist.is_dir():
            return (
                jsonify(
                    {
                        "error": {
                            "type": "NotBuilt",
                            "message": (
                                "The UI is not built. Run 'npm run dev' in "
                                "mzstack-app-ui, or 'npm run build' to serve "
                                "it from here."
                            ),
                        }
                    }
                ),
                404,
            )
        candidate = dist / requested
        if requested and candidate.is_file():
            return send_from_directory(dist, requested)
        # Any other path is a client-side route: hand back the shell and let
        # the router resolve it.
        return send_from_directory(dist, "index.html")
