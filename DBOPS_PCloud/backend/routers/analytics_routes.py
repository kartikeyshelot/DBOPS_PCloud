"""
GET /api/analytics/alert-velocity       — Servers with accelerating alerts.
GET /api/analytics/stability            — Load stability scores.
GET /api/analytics/mttr                 — Mean time to recovery.
GET /api/analytics/correlated-failures  — Servers that alert together.
GET /api/analytics/env-comparison       — Environment health comparison.
GET /api/analytics/utilization-dist     — Utilization distribution histogram.
GET /api/analytics/forecast/{server}    — Improved forecast with confidence.
GET /api/analytics/alert-heatmap        — Alerts by day-of-week x hour.
GET /api/analytics/top-alerters         — Top N noisiest servers with severity breakdown.
GET /api/analytics/bundle               — All analytics in one call (used by Analytics tab).
"""

import logging
import pandas as pd
from typing import Optional
from fastapi import APIRouter
from database import get_db, get_latest_fetch_id
from utils import load_servers_df, dedup_servers_df, apply_filters_df, scoped_query
from services.advanced_analytics import (
    forecast_with_confidence,
    compute_alert_velocity,
    compute_stability_scores,
    compute_mttr,
    detect_correlated_failures,
    compute_environment_comparison,
    compute_utilization_distribution,
    compute_alert_heatmap,
    compute_top_alerters,
    compute_alert_categories,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Shared loaders ────────────────────────────────────────────────────────────

def _load_events(conn, fetch_id, server_names=None) -> pd.DataFrame:
    rows = scoped_query(conn,
        "SELECT * FROM events WHERE fetch_id = ?", fetch_id, server_names)
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def _load_capacity(conn, fetch_id, server_names=None) -> pd.DataFrame:
    rows = scoped_query(conn,
        "SELECT * FROM capacity_trends WHERE fetch_id = ?", fetch_id, server_names)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={
        "server_name": "Server Name", "metric": "Metric",
        "date": "Date", "utilization": "Utilization",
    })
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Utilization"] = pd.to_numeric(df["Utilization"], errors="coerce")
    return df


# ── Existing endpoints ────────────────────────────────────────────────────────

@router.get("/analytics/alert-velocity")
def get_alert_velocity():
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return []
        events = _load_events(conn, fetch_id)
    return compute_alert_velocity(events)


@router.get("/analytics/stability")
def get_stability_scores():
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return []
        cap_df = _load_capacity(conn, fetch_id)
    return compute_stability_scores(cap_df)


@router.get("/analytics/mttr")
def get_mttr():
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return {"fleet_avg_hours": 0, "servers": []}
        events = _load_events(conn, fetch_id)
    return compute_mttr(events)


@router.get("/analytics/correlated-failures")
def get_correlated_failures():
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return []
        events = _load_events(conn, fetch_id)
    return detect_correlated_failures(events)


@router.get("/analytics/env-comparison")
def get_env_comparison():
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return []
        df = load_servers_df(conn, fetch_id)
    df = dedup_servers_df(df)
    return compute_environment_comparison(df)


@router.get("/analytics/utilization-dist")
def get_utilization_distribution():
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return {"buckets": [], "stats": {}}
        df = load_servers_df(conn, fetch_id)
    df = dedup_servers_df(df)
    return compute_utilization_distribution(df)


@router.get("/analytics/forecast/{server_name}")
def get_advanced_forecast(server_name: str):
    """Polynomial regression forecast with 95% confidence interval."""
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return {"error": "No data"}
        cap_df = _load_capacity(conn, fetch_id)
    if cap_df.empty:
        return {"error": "No capacity data"}

    server_data = cap_df[
        cap_df["Server Name"].str.lower() == server_name.lower()
    ].sort_values("Date")

    if server_data.empty:
        return {"error": f"No capacity data for '{server_name}'"}

    dates = server_data["Date"].tolist()
    values = server_data["Utilization"].tolist()

    result = forecast_with_confidence(dates, values)
    result["server_name"] = server_name
    # Keep dates and values aligned — filter out NaN pairs together, not independently
    aligned_dates = []
    aligned_values = []
    for d, v in zip(dates, values):
        if v == v:  # skip NaN (NaN != NaN)
            aligned_dates.append(d.strftime("%Y-%m-%d") if hasattr(d, 'strftime') else str(d))
            aligned_values.append(round(float(v), 2))
    result["actual_dates"] = aligned_dates
    result["actual_values"] = aligned_values
    return result


# ── New endpoints ─────────────────────────────────────────────────────────────

@router.get("/analytics/alert-heatmap")
def get_alert_heatmap():
    """
    Alert counts grouped by day-of-week and hour-of-day.
    Shows WHEN during the week problems tend to occur — useful for scheduling
    maintenance windows and on-call rotations.
    """
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return {"matrix": [], "max_count": 0, "total_events": 0, "peak": None}
        events = _load_events(conn, fetch_id)
    return compute_alert_heatmap(events)


@router.get("/analytics/top-alerters")
def get_top_alerters(n: int = 15):
    """
    Top N servers by total alert count with per-severity breakdown.
    Immediately actionable: shows who is the noisiest and whether the alerts
    are high-severity (investigate) or low-severity (tune thresholds).
    """
    n = min(n, 50)
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return []
        events = _load_events(conn, fetch_id)
    return compute_top_alerters(events, n)


@router.get("/analytics/alert-categories")
def get_alert_categories():
    """
    Classify all alerts by problem type (CPU / Memory / Disk / Database / Service / Network / Other).
    Answers: 'what kind of problems dominate the fleet?'
    Feeds the category donut chart in Analytics.
    """
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return {"total": 0, "categories": {}}
        events = _load_events(conn, fetch_id)
    return compute_alert_categories(events)


@router.get("/analytics/bundle")
def get_analytics_bundle(
    search: Optional[str] = None, priority: Optional[str] = None,
    environment: Optional[str] = None, app_code: Optional[str] = None,
):
    """
    Single endpoint that returns ALL analytics data in one call.

    Loads events, capacity, and servers ONCE each then fans out to every
    compute function.  Each compute function is wrapped in a try/except so
    one failing section does not crash the entire bundle response.

    Performance:
    - Events dates are pre-parsed once (not 5× independently in each function)
    - Server DataFrame is loaded once and reused for both filtering and names
    - Correlated failures is capped at 50-server windows to prevent O(n²) blowup
    """
    import time as _time

    empty_bundle = {
        "env_comparison": [], "utilization_dist": {"buckets": [], "stats": {}},
        "alert_velocity": [], "stability": [], "mttr": {"fleet_avg_hours": 0, "servers": []},
        "correlated_failures": [], "alert_heatmap": {"matrix": [], "max_count": 0, "total_events": 0, "peak": None},
        "top_alerters": [], "alert_categories": {"total": 0, "categories": {}},
    }

    t0 = _time.time()

    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return empty_bundle

        # Load server DataFrame once — used for both name filtering and compute functions
        srv_df = load_servers_df(conn, fetch_id)
        has_filter = any([search, priority, environment, app_code])

        # Extract filtered server names from the already-loaded df (no double-load)
        if has_filter:
            srv_df = apply_filters_df(srv_df, search, priority, environment, app_code)
            # Dedup once here — reused as srv_dedup below, not recomputed
            srv_dedup = dedup_servers_df(srv_df)
            names = srv_dedup["Server Name"].tolist() if not srv_df.empty else []
        else:
            srv_dedup = dedup_servers_df(srv_df)
            names = None  # None = no filtering, load everything

        # Load events and capacity ONCE, scoped to filtered servers
        events = _load_events(conn, fetch_id, names)
        cap_df = _load_capacity(conn, fetch_id, names)

    t_load = _time.time()
    logger.info("Analytics bundle: data loaded in %.1fs (%d events, %d cap rows, %d servers)",
                t_load - t0, len(events), len(cap_df), len(srv_df))

    # Pre-parse event dates ONCE — all compute functions check "Date" column
    # and skip parsing if it already exists. This eliminates 5× redundant parsing.
    if not events.empty and "date" in events.columns:
        events["Date"] = pd.to_datetime(events["date"], errors="coerce")

    def _safe(fn, default, label=""):
        """Run a compute function safely; return default on failure."""
        try:
            t1 = _time.time()
            result = fn()
            logger.debug("Analytics bundle: %s completed in %.2fs", label, _time.time() - t1)
            return result
        except Exception as e:
            logger.error("Analytics bundle: %s failed in %.2fs: %s", label, _time.time() - t1, e, exc_info=True)
            return default

    bundle = {
        "env_comparison":      _safe(lambda: compute_environment_comparison(srv_dedup),     [], "env_comparison"),
        "utilization_dist":    _safe(lambda: compute_utilization_distribution(srv_dedup),    empty_bundle["utilization_dist"], "utilization_dist"),
        "alert_velocity":      _safe(lambda: compute_alert_velocity(events),                [], "alert_velocity"),
        "stability":           _safe(lambda: compute_stability_scores(cap_df),              [], "stability"),
        "mttr":                _safe(lambda: compute_mttr(events),                          empty_bundle["mttr"], "mttr"),
        "correlated_failures": _safe(lambda: detect_correlated_failures(events),            [], "correlated_failures"),
        "alert_heatmap":       _safe(lambda: compute_alert_heatmap(events),                 empty_bundle["alert_heatmap"], "alert_heatmap"),
        "top_alerters":        _safe(lambda: compute_top_alerters(events, 15),              [], "top_alerters"),
        "alert_categories":    _safe(lambda: compute_alert_categories(events),              empty_bundle["alert_categories"], "alert_categories"),
    }

    logger.info("Analytics bundle: total %.1fs", _time.time() - t0)
    return bundle
