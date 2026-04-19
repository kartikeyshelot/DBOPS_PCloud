"""
POST /api/fetch  — Trigger a Zabbix data refresh (non-blocking).
GET  /api/fetch/status  — Poll the latest fetch run status.
GET  /api/fetch/history — Recent fetch run history.

The fetch now runs in a background thread so the HTTP request returns
immediately with {fetch_id, status: "running"}.  The frontend polls
/api/fetch/status every few seconds until status = "completed" or "failed".

Anomaly detection (IsolationForest) and workload profiles (KMeans) are run
once here — immediately after process_data() — and the Diagnostic column is
persisted to the servers table.  GET /api/servers simply reads the stored
value instead of re-training the model on every page load.
"""

import datetime
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from models.schemas import FetchRequest, FetchStatusResponse
from services.zabbix_client import ZabbixClient, ZabbixAPIError
from services.analytics import process_data
from services.anomaly import detect_anomalies
from services.persistence import (
    create_fetch_run, complete_fetch_run, fail_fetch_run,
    save_servers, save_events, save_disks, save_databases, save_capacity_trends,
)
from database import get_db, is_fetch_running, cleanup_old_runs

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Background worker ─────────────────────────────────────────────────────────

def _run_fetch(fetch_id: int, req: FetchRequest):
    """
    The actual fetch work — runs in a daemon thread so the HTTP request
    can return immediately.  All state is tracked in the fetch_runs table.
    """
    try:
        client = ZabbixClient(req.zabbix_url, req.zabbix_token)
        data = client.fetch_all(req.zabbix_group, req.days_back)

        cap_df, summary_df = process_data(
            data["cap_df"], data["problems_df"], data["host_tags"],
            data["disk_df"], data["db_df"], data["hw_df"],
        )

        if summary_df is None or summary_df.empty:
            with get_db() as conn:
                fail_fetch_run(conn, fetch_id, "No processable data returned from Zabbix")
            return

        # ── Run anomaly detection once here, store result — never recomputed ──
        summary_df = detect_anomalies(summary_df)

        with get_db() as conn:
            save_servers(conn, fetch_id, summary_df)
            save_events(conn, fetch_id, data["events_df"])
            save_disks(conn, fetch_id, data["disk_df"])
            save_databases(conn, fetch_id, data["db_df"])
            save_capacity_trends(conn, fetch_id, data["cap_df"])
            complete_fetch_run(conn, fetch_id, len(summary_df))

        # ── Prune old runs so the DB doesn't grow unboundedly ────────────────
        with get_db() as conn:
            cleanup_old_runs(conn)

        logger.info("Fetch %d completed: %d servers", fetch_id, len(summary_df))

    except ZabbixAPIError as e:
        logger.error("Fetch %d Zabbix error: %s", fetch_id, e)
        with get_db() as conn:
            fail_fetch_run(conn, fetch_id, f"Zabbix API error: {e}")
    except Exception as e:
        logger.exception("Fetch %d failed unexpectedly", fetch_id)
        with get_db() as conn:
            fail_fetch_run(conn, fetch_id, str(e))


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/fetch", response_model=FetchStatusResponse, status_code=202)
def trigger_fetch(req: FetchRequest, background_tasks: BackgroundTasks):
    """
    Start a Zabbix fetch.  Returns immediately (202 Accepted) with the
    fetch_id so the client can poll /api/fetch/status.

    Returns 409 if a fetch is already in progress to prevent overlapping runs.
    """
    with get_db() as conn:
        if is_fetch_running(conn):
            raise HTTPException(
                status_code=409,
                detail="A fetch is already in progress. Poll /api/fetch/status for updates.",
            )
        fetch_id = create_fetch_run(conn, req.zabbix_url, req.zabbix_group, req.days_back)

    # Kick off in a daemon thread — FastAPI BackgroundTasks run after the
    # response is sent, which is exactly what we want here.
    background_tasks.add_task(_run_fetch, fetch_id, req)

    return FetchStatusResponse(
        fetch_id=fetch_id,
        status="running",
        started_at=datetime.datetime.now().isoformat(),
        server_count=0,
    )


@router.get("/fetch/status")
def get_fetch_status():
    """Return the status of the most recent fetch run (any status)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM fetch_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"status": "no_data", "message": "No fetch has been run yet"}
        return dict(row)


@router.get("/fetch/history")
def get_fetch_history(limit: int = 10):
    """Return recent fetch run history (capped at 100 rows)."""
    limit = min(limit, 100)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM fetch_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
