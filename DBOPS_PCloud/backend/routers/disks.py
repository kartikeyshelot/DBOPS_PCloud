"""
GET /api/disks — Disk usage data.
"""

import logging
from fastapi import APIRouter
from typing import Optional
from database import get_db, get_latest_fetch_id
from utils import get_filtered_server_names, scoped_query

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/disks")
def get_disks(
    server_name: Optional[str] = None,
    search: Optional[str] = None, priority: Optional[str] = None,
    environment: Optional[str] = None, app_code: Optional[str] = None,
):
    """Get disk usage data, optionally filtered by server or global filters."""
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return []

        if server_name:
            rows = conn.execute(
                "SELECT * FROM disks WHERE fetch_id = ? AND server_name = ?",
                (fetch_id, server_name)
            ).fetchall()
        else:
            names = get_filtered_server_names(conn, fetch_id, search, priority, environment, app_code)
            rows = scoped_query(conn,
                "SELECT * FROM disks WHERE fetch_id = ?", fetch_id, names)

        return [dict(r) for r in rows]
