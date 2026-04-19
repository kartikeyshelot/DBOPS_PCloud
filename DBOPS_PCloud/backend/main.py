"""
DB Infrastructure OPS — FastAPI Backend
Entry point: uvicorn main:app --reload --port 8000

The pre-built React frontend is served from the static/ directory.
No Node.js required — just Python.
"""

import json
import math
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from database import init_db
from routers import fetch, servers, forecasts, incidents, disks, databases, analytics_routes, export, resources


class NaNSafeEncoder(json.JSONEncoder):
    """JSON encoder that converts NaN/Inf to null and handles numpy types."""
    def default(self, obj):
        # Handle numpy scalar types that json.dumps doesn't know about
        try:
            import numpy as np
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                v = float(obj)
                return None if math.isnan(v) or math.isinf(v) else v
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        return super().default(obj)

    def encode(self, o):
        return super().encode(self._sanitize(o))

    def _sanitize(self, obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
        elif isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._sanitize(v) for v in obj]
        return obj


class NaNSafeJSONResponse(JSONResponse):
    """JSONResponse that handles NaN/Inf without crashing."""
    def render(self, content) -> bytes:
        return json.dumps(
            content,
            cls=NaNSafeEncoder,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Path to the pre-built frontend
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    logger.info("Initializing database...")
    init_db()

    if STATIC_DIR.exists():
        logger.info("Frontend: serving pre-built UI from %s", STATIC_DIR)
    else:
        logger.warning(
            "Frontend: static/ directory not found. "
            "API still works at /api/* and /docs, but no UI will be served."
        )

    logger.info("DB Infrastructure OPS backend ready.")
    logger.info("Open http://localhost:8000 in your browser.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="DB Infrastructure OPS",
    description="Database infrastructure monitoring and capacity planning API",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=NaNSafeJSONResponse,
)

# CORS — allow the Vite dev server and any same-host access.
# NOTE: allow_credentials=True CANNOT be combined with allow_origins=["*"]
# (browsers block such responses per the CORS spec).  We instead enumerate
# the known origins; wildcard still applies to non-credentialed requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev server
        "http://localhost:8000",   # backend serving built SPA
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes (must be registered BEFORE the static file catch-all) ──
app.include_router(fetch.router, prefix="/api", tags=["Fetch"])
app.include_router(servers.router, prefix="/api", tags=["Servers"])
app.include_router(forecasts.router, prefix="/api", tags=["Forecasts"])
app.include_router(incidents.router, prefix="/api", tags=["Incidents"])
app.include_router(disks.router, prefix="/api", tags=["Disks"])
app.include_router(databases.router, prefix="/api", tags=["Databases"])
app.include_router(analytics_routes.router, prefix="/api", tags=["Advanced Analytics"])
app.include_router(export.router, prefix="/api", tags=["Export"])
app.include_router(resources.router, prefix="/api", tags=["Resources"])


@app.get("/api/ping")
def ping():
    return {"status": "ok"}


# ── Serve pre-built frontend ──
if STATIC_DIR.exists():
    # Serve JS/CSS/assets at /assets/*
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    # Catch-all: serve index.html for any non-API route (SPA client-side routing)
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        # If a specific static file exists (e.g. favicon), serve it
        file_path = STATIC_DIR / full_path
        # Guard against path traversal (e.g. ../../etc/passwd)
        if full_path and file_path.resolve().is_relative_to(STATIC_DIR.resolve()) and file_path.is_file():
            return FileResponse(file_path)
        # Otherwise serve index.html (React handles routing)
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/")
    def root():
        return {
            "app": "DB Infrastructure OPS",
            "docs": "/docs",
            "note": "No frontend found. Place pre-built files in backend/static/ or use /docs for the API.",
            "status": "running"
        }
