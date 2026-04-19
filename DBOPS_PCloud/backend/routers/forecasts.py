"""
GET /api/forecasts/{server_name} — Per-server runway projection.
GET /api/forecasts/runway — Top servers by shortest runway.
"""

import logging
from fastapi import APIRouter
from typing import Optional
from database import get_db, get_latest_fetch_id
from utils import safe_float as _sf, get_filtered_server_names

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/forecasts/runway")
def get_runway_overview(
    limit: int = 15,
    search: Optional[str] = None, priority: Optional[str] = None,
    environment: Optional[str] = None, app_code: Optional[str] = None,
):
    """Top servers with shortest runway — feeds the horizontal bar chart."""
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return []
        names = get_filtered_server_names(conn, fetch_id, search, priority, environment, app_code)
        sql = ("SELECT name, days_left, current_load, priority, resource_type "
               "FROM servers WHERE fetch_id = ? AND days_left < 999")
        if names is not None:
            if not names:
                return []
            placeholders = ",".join("?" * len(names))
            sql += f" AND name IN ({placeholders})"
            rows = conn.execute(sql + " ORDER BY days_left ASC", [fetch_id] + names).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY days_left ASC", (fetch_id,)).fetchall()

        # Deduplicate: keep shortest runway per server
        seen = {}
        for r in rows:
            d = dict(r)
            name = d["name"]
            if name not in seen or d["days_left"] < seen[name]["days_left"]:
                seen[name] = d
        deduped = sorted(seen.values(), key=lambda x: x["days_left"])[:limit]

        result = []
        for d in deduped:
            d["current_load"] = _sf(d.get("current_load"))
            d["days_left"] = int(_sf(d.get("days_left"), 999))
            result.append(d)
        return result


@router.get("/forecasts/{server_name}")
def get_server_forecast(server_name: str):
    """Detailed forecast for a single server — feeds the runway projection chart."""
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return {"error": "No data available"}

        row = conn.execute(
            "SELECT * FROM servers WHERE fetch_id = ? AND name = ? LIMIT 1",
            (fetch_id, server_name)
        ).fetchone()
        if not row:
            return {"error": f"Server '{server_name}' not found"}

        srv = dict(row)
        min_free_gb = _sf(srv.get("min_free_gb"), 0)
        max_db_growth = max(_sf(srv.get("max_db_growth"), 0), 0.001)
        max_disk_util = _sf(srv.get("max_disk_util"), 0)
        current_load = _sf(srv.get("current_load"), 0)
        days_left = int(_sf(srv.get("days_left"), 999))

        if max_db_growth > 0.001:
            linear_days = min_free_gb / max_db_growth
        else:
            linear_days = 9999
        final_days = min(linear_days, 9999)

        # Build projection curve
        projection = []
        if 0 < final_days < 999:
            day_range = list(range(0, min(int(final_days) + 30, 400)))
            for d in day_range:
                projection.append({
                    "day": d,
                    "free_gb": round(max(0, min_free_gb - max_db_growth * d), 2)
                })

        return {
            "server_name": server_name,
            "min_free_gb": round(min_free_gb, 1),
            "max_db_growth": round(max_db_growth, 2),
            "max_disk_util": round(max_disk_util, 1),
            "estimated_runway_days": round(final_days, 0),
            "current_load": current_load,
            "days_left": days_left,
            "priority": srv.get("priority") or "NONE",
            "projection": projection,
        }
