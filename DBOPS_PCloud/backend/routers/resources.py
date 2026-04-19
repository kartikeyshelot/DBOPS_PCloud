"""
GET /api/resources/servers  — Per-server resource breakdown (CPU, RAM, Disk, DB separate).
GET /api/resources/fleet    — Fleet-wide totals and utilization.
GET /api/resources/actions  — Grouped action items based on concrete thresholds.

These endpoints power the "Resource Overview" dashboard that shows simple,
actionable numbers: provisioned vs utilised for every resource type.
"""

import math
import json
import logging
from typing import Optional

import pandas as pd
from fastapi import APIRouter

from config import settings
from database import get_db, get_latest_fetch_id
from utils import safe_float, safe_int, load_servers_df, dedup_servers_df, apply_filters_df

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sf(val, default=0.0):
    return safe_float(val, default)


def _si(val, default=0):
    return safe_int(val, default)


def _load_disk_agg(conn, fetch_id, server_names=None):
    """Aggregate disk data per server: total, used, free, drive count."""
    sql = (
        "SELECT server_name, "
        "  SUM(total_gb) as total_gb, "
        "  SUM(used_gb) as used_gb, "
        "  SUM(free_gb) as free_gb, "
        "  MAX(utilization_pct) as max_util, "
        "  COUNT(*) as drive_count "
        "FROM disks WHERE fetch_id = ?"
    )
    params = [fetch_id]
    if server_names is not None:
        if not server_names:
            return {}
        placeholders = ",".join("?" * len(server_names))
        sql += f" AND server_name IN ({placeholders})"
        params.extend(server_names)
    sql += " GROUP BY server_name"
    rows = conn.execute(sql, params).fetchall()
    return {r["server_name"]: dict(r) for r in rows}


def _load_db_agg(conn, fetch_id, server_names=None):
    """Aggregate database data per server: count, total size, total growth."""
    sql = (
        "SELECT server_name, "
        "  COUNT(*) as db_count, "
        "  SUM(raw_size) as total_size_bytes, "
        "  SUM(raw_growth) as total_growth_bytes "
        "FROM databases WHERE fetch_id = ?"
    )
    params = [fetch_id]
    if server_names is not None:
        if not server_names:
            return {}
        placeholders = ",".join("?" * len(server_names))
        sql += f" AND server_name IN ({placeholders})"
        params.extend(server_names)
    sql += " GROUP BY server_name"
    rows = conn.execute(sql, params).fetchall()
    return {r["server_name"]: dict(r) for r in rows}


def _load_alert_counts(conn, fetch_id, server_names=None):
    """Count alerts per server."""
    sql = "SELECT server_name, COUNT(*) as alert_count FROM events WHERE fetch_id = ?"
    params = [fetch_id]
    if server_names is not None:
        if not server_names:
            return {}
        placeholders = ",".join("?" * len(server_names))
        sql += f" AND server_name IN ({placeholders})"
        params.extend(server_names)
    sql += " GROUP BY server_name"
    rows = conn.execute(sql, params).fetchall()
    return {r["server_name"]: r["alert_count"] for r in rows}


def _build_resource_row(name, cpu_row, mem_row, disk_info, db_info, alert_count):
    """Build a single server resource record from CPU row, Memory row, disk agg, db agg."""
    cpu_count = _si(cpu_row.get("CPU_Count") if cpu_row else 0)
    cpu_load = _sf(cpu_row.get("Current_Load") if cpu_row else 0)
    cpu_days = _si(cpu_row.get("Days_Left", 999) if cpu_row else 999, 999)
    ram_gb = _sf(cpu_row.get("RAM_GB") if cpu_row else 0)
    mem_load = _sf(mem_row.get("Current_Load") if mem_row else 0)
    mem_days = _si(mem_row.get("Days_Left", 999) if mem_row else 999, 999)

    # Compute used values from percentage
    cpu_used = round(cpu_count * cpu_load / 100, 1) if cpu_count > 0 else 0
    ram_used_gb = round(ram_gb * mem_load / 100, 1) if ram_gb > 0 else 0

    # Disk
    disk_total = _sf(disk_info.get("total_gb") if disk_info else 0)
    disk_used = _sf(disk_info.get("used_gb") if disk_info else 0)
    disk_free = _sf(disk_info.get("free_gb") if disk_info else 0)
    disk_max_util = _sf(disk_info.get("max_util") if disk_info else 0)
    drive_count = _si(disk_info.get("drive_count") if disk_info else 0)

    # DB
    db_count = _si(db_info.get("db_count") if db_info else 0)
    db_total_size_gb = _sf(db_info.get("total_size_bytes", 0) if db_info else 0) / (1024 ** 3)
    db_growth_bytes = _sf(db_info.get("total_growth_bytes", 0) if db_info else 0)
    db_growth_gb_day = db_growth_bytes / (1024 ** 3)

    # Storage runway: free disk / DB growth rate
    if db_growth_gb_day > 0.001:
        storage_runway_days = min(int(disk_free / db_growth_gb_day), 9999)
    else:
        storage_runway_days = 9999

    # Priority & environment from whichever row is available
    src = cpu_row or mem_row or {}
    priority = str(src.get("Priority", "NONE") or "NONE")
    environment = str(src.get("Environment", "Unknown") or "Unknown")
    criticality = str(src.get("PAASDB_CRTICALITY", "Unknown") or "Unknown")
    risk_category = str(src.get("Risk_Category", "Healthy") or "Healthy")
    action = str(src.get("Action", "Monitor") or "Monitor")
    diagnostic = str(src.get("Diagnostic", "") or "")

    return {
        "name": name,
        "environment": environment,
        "criticality": criticality,
        "priority": priority,
        "risk_category": risk_category,
        "action": action,
        "diagnostic": diagnostic,
        # CPU
        "cpu_count": cpu_count,
        "cpu_load_pct": round(cpu_load, 1),
        "cpu_used": cpu_used,
        "cpu_days_left": cpu_days,
        # RAM
        "ram_gb": round(ram_gb, 1),
        "ram_load_pct": round(mem_load, 1),
        "ram_used_gb": ram_used_gb,
        "ram_days_left": mem_days,
        # Disk (aggregate across all drives)
        "disk_total_gb": round(disk_total, 1),
        "disk_used_gb": round(disk_used, 1),
        "disk_free_gb": round(disk_free, 1),
        "disk_max_util_pct": round(disk_max_util, 1),
        "drive_count": drive_count,
        # Databases
        "db_count": db_count,
        "db_total_size_gb": round(db_total_size_gb, 2),
        "db_growth_gb_day": round(db_growth_gb_day, 3),
        "storage_runway_days": storage_runway_days,
        # Alerts
        "total_alerts": alert_count,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/resources/servers")
def get_resource_servers(
    search: Optional[str] = None,
    priority: Optional[str] = None,
    environment: Optional[str] = None,
    app_code: Optional[str] = None,
    criticality: Optional[str] = None,
    sort_by: Optional[str] = "priority",
    sort_dir: Optional[str] = "asc",
):
    """
    Per-server resource breakdown with CPU, RAM, Disk, and DB as separate columns.
    Unlike GET /servers, this does NOT collapse CPU/Memory rows — it exposes both.
    """
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return []

        df = load_servers_df(conn, fetch_id)
        if df.empty:
            return []

        df = apply_filters_df(df, search, priority, environment, app_code, criticality)
        if df.empty:
            return []

        # Get unique server names from filtered data
        server_names = df["Server Name"].unique().tolist()

        # Split into CPU and Memory rows
        cpu_df = df[df["Resource_Type"].str.upper() == "CPU"]
        mem_df = df[df["Resource_Type"].str.upper() == "MEMORY"]

        cpu_map = {}
        for _, row in cpu_df.iterrows():
            name = row["Server Name"]
            if name not in cpu_map:
                cpu_map[name] = row.to_dict()

        mem_map = {}
        for _, row in mem_df.iterrows():
            name = row["Server Name"]
            if name not in mem_map:
                mem_map[name] = row.to_dict()

        # Load aggregated disk, db, alert data
        disk_agg = _load_disk_agg(conn, fetch_id, server_names)
        db_agg = _load_db_agg(conn, fetch_id, server_names)
        alert_counts = _load_alert_counts(conn, fetch_id, server_names)

        # Triage statuses
        triage_rows = conn.execute("SELECT server_name, status FROM triage_status").fetchall()
        triage_map = {r["server_name"]: r["status"] for r in triage_rows}

    # Build one row per unique server
    unique_names = sorted(set(list(cpu_map.keys()) + list(mem_map.keys())))
    result = []
    for name in unique_names:
        row = _build_resource_row(
            name,
            cpu_map.get(name),
            mem_map.get(name),
            disk_agg.get(name),
            db_agg.get(name),
            alert_counts.get(name, 0),
        )
        row["triage_status"] = triage_map.get(name, "Open")
        result.append(row)

    # Sort
    PRIORITY_ORDER = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "NONE": 4}
    reverse = sort_dir == "desc"

    if sort_by == "priority":
        result.sort(key=lambda x: PRIORITY_ORDER.get(x["priority"], 5), reverse=reverse)
    elif sort_by == "cpu_load":
        result.sort(key=lambda x: x["cpu_load_pct"], reverse=not reverse)
    elif sort_by == "ram_load":
        result.sort(key=lambda x: x["ram_load_pct"], reverse=not reverse)
    elif sort_by == "disk_util":
        result.sort(key=lambda x: x["disk_max_util_pct"], reverse=not reverse)
    elif sort_by == "storage_runway":
        result.sort(key=lambda x: x["storage_runway_days"], reverse=reverse)
    elif sort_by == "alerts":
        result.sort(key=lambda x: x["total_alerts"], reverse=not reverse)
    elif sort_by == "name":
        result.sort(key=lambda x: x["name"].lower(), reverse=reverse)

    return result


@router.get("/resources/fleet")
def get_fleet_summary(
    search: Optional[str] = None,
    priority: Optional[str] = None,
    environment: Optional[str] = None,
    app_code: Optional[str] = None,
):
    """
    Fleet-wide resource totals: sum of all provisioned and utilised resources.
    """
    with get_db() as conn:
        fetch_id = get_latest_fetch_id(conn)
        if not fetch_id:
            return {
                "server_count": 0,
                "cpu_total": 0, "cpu_used": 0, "cpu_avg_pct": 0,
                "ram_total_gb": 0, "ram_used_gb": 0, "ram_avg_pct": 0,
                "disk_total_gb": 0, "disk_used_gb": 0, "disk_free_gb": 0, "disk_avg_pct": 0,
                "db_count": 0, "db_total_size_gb": 0, "db_growth_gb_day": 0,
                "total_alerts": 0,
            }

        df = load_servers_df(conn, fetch_id)
        if df.empty:
            return {
                "server_count": 0,
                "cpu_total": 0, "cpu_used": 0, "cpu_avg_pct": 0,
                "ram_total_gb": 0, "ram_used_gb": 0, "ram_avg_pct": 0,
                "disk_total_gb": 0, "disk_used_gb": 0, "disk_free_gb": 0, "disk_avg_pct": 0,
                "db_count": 0, "db_total_size_gb": 0, "db_growth_gb_day": 0,
                "total_alerts": 0,
            }

        df = apply_filters_df(df, search, priority, environment, app_code)
        server_names = df["Server Name"].unique().tolist()
        has_filter = any([search, priority, environment, app_code])

        # CPU rows
        cpu_df = df[df["Resource_Type"].str.upper() == "CPU"]
        # Deduplicate: one row per server for CPU
        if not cpu_df.empty:
            cpu_dedup = cpu_df.groupby("Server Name").agg({
                "CPU_Count": "max",
                "Current_Load": "max",
                "RAM_GB": "max",
            }).reset_index()
            cpu_total = int(cpu_dedup["CPU_Count"].sum())
            cpu_avg_pct = round(float(cpu_dedup["Current_Load"].mean()), 1)
            cpu_used = round(float((cpu_dedup["CPU_Count"] * cpu_dedup["Current_Load"] / 100).sum()), 1)
            ram_total = round(float(cpu_dedup["RAM_GB"].sum()), 1)
        else:
            cpu_total = 0
            cpu_avg_pct = 0
            cpu_used = 0
            ram_total = 0

        # Memory rows for RAM utilisation
        mem_df = df[df["Resource_Type"].str.upper() == "MEMORY"]
        if not mem_df.empty:
            mem_dedup = mem_df.groupby("Server Name").agg({
                "Current_Load": "max",
                "RAM_GB": "max",
            }).reset_index()
            ram_avg_pct = round(float(mem_dedup["Current_Load"].mean()), 1)
            ram_used = round(float((mem_dedup["RAM_GB"] * mem_dedup["Current_Load"] / 100).sum()), 1)
            # If ram_total was 0 from CPU rows, try memory rows
            if ram_total == 0:
                ram_total = round(float(mem_dedup["RAM_GB"].sum()), 1)
        else:
            ram_avg_pct = 0
            ram_used = 0

        # Server count (unique)
        server_count = len(server_names)

        # Disk totals
        disk_sql = (
            "SELECT SUM(total_gb) as total, SUM(used_gb) as used, SUM(free_gb) as free "
            "FROM disks WHERE fetch_id = ?"
        )
        disk_params = [fetch_id]
        if has_filter and server_names:
            placeholders = ",".join("?" * len(server_names))
            disk_sql += f" AND server_name IN ({placeholders})"
            disk_params.extend(server_names)
        elif has_filter and not server_names:
            disk_row = None
        else:
            pass
        disk_row = conn.execute(disk_sql, disk_params).fetchone()

        disk_total = _sf(disk_row["total"]) if disk_row else 0
        disk_used = _sf(disk_row["used"]) if disk_row else 0
        disk_free = _sf(disk_row["free"]) if disk_row else 0
        disk_avg_pct = round(disk_used / max(disk_total, 0.01) * 100, 1) if disk_total > 0 else 0

        # DB totals
        db_sql = (
            "SELECT COUNT(*) as cnt, SUM(raw_size) as total_size, SUM(raw_growth) as total_growth "
            "FROM databases WHERE fetch_id = ?"
        )
        db_params = [fetch_id]
        if has_filter and server_names:
            placeholders = ",".join("?" * len(server_names))
            db_sql += f" AND server_name IN ({placeholders})"
            db_params.extend(server_names)
        db_row = conn.execute(db_sql, db_params).fetchone()

        db_count = _si(db_row["cnt"]) if db_row else 0
        db_total_size_gb = _sf(db_row["total_size"]) / (1024 ** 3) if db_row else 0
        db_growth_gb_day = _sf(db_row["total_growth"]) / (1024 ** 3) if db_row else 0

        # Alert count
        alert_sql = "SELECT COUNT(*) as cnt FROM events WHERE fetch_id = ?"
        alert_params = [fetch_id]
        if has_filter and server_names:
            placeholders = ",".join("?" * len(server_names))
            alert_sql += f" AND server_name IN ({placeholders})"
            alert_params.extend(server_names)
        alert_row = conn.execute(alert_sql, alert_params).fetchone()
        total_alerts = _si(alert_row["cnt"]) if alert_row else 0

        # Environment breakdown
        env_data = []
        deduped = dedup_servers_df(df)
        for env in sorted(deduped["Environment"].unique()):
            env_df = deduped[deduped["Environment"] == env]
            env_cpu = df[(df["Resource_Type"].str.upper() == "CPU") & (df["Server Name"].isin(env_df["Server Name"]))]
            env_mem = df[(df["Resource_Type"].str.upper() == "MEMORY") & (df["Server Name"].isin(env_df["Server Name"]))]

            e_cpu_total = int(env_cpu.groupby("Server Name")["CPU_Count"].max().sum()) if not env_cpu.empty else 0
            e_cpu_avg = round(float(env_cpu.groupby("Server Name")["Current_Load"].max().mean()), 1) if not env_cpu.empty else 0
            e_ram_total = round(float(env_cpu.groupby("Server Name")["RAM_GB"].max().sum()), 1) if not env_cpu.empty else 0
            e_ram_avg = round(float(env_mem.groupby("Server Name")["Current_Load"].max().mean()), 1) if not env_mem.empty else 0

            env_names = env_df["Server Name"].tolist()
            e_disk_rows = conn.execute(
                f"SELECT SUM(total_gb) as total, SUM(used_gb) as used FROM disks WHERE fetch_id = ? AND server_name IN ({','.join('?' * len(env_names))})",
                [fetch_id] + env_names,
            ).fetchone() if env_names else None

            env_data.append({
                "environment": env,
                "server_count": len(env_df),
                "cpu_total": e_cpu_total,
                "cpu_avg_pct": e_cpu_avg,
                "ram_total_gb": e_ram_total,
                "ram_avg_pct": e_ram_avg,
                "disk_total_gb": round(_sf(e_disk_rows["total"]), 1) if e_disk_rows else 0,
                "disk_used_gb": round(_sf(e_disk_rows["used"]), 1) if e_disk_rows else 0,
            })

    return {
        "server_count": server_count,
        "cpu_total": cpu_total,
        "cpu_used": round(cpu_used, 1),
        "cpu_avg_pct": cpu_avg_pct,
        "ram_total_gb": ram_total,
        "ram_used_gb": round(ram_used, 1),
        "ram_avg_pct": ram_avg_pct,
        "disk_total_gb": round(disk_total, 1),
        "disk_used_gb": round(disk_used, 1),
        "disk_free_gb": round(disk_free, 1),
        "disk_avg_pct": disk_avg_pct,
        "db_count": db_count,
        "db_total_size_gb": round(db_total_size_gb, 2),
        "db_growth_gb_day": round(db_growth_gb_day, 3),
        "total_alerts": total_alerts,
        "by_environment": env_data,
    }


@router.get("/resources/actions")
def get_action_items(
    search: Optional[str] = None,
    priority: Optional[str] = None,
    environment: Optional[str] = None,
    app_code: Optional[str] = None,
):
    """
    Concrete action items grouped by type.
    Every item has a clear reason and suggested action.
    No ML scores — just threshold-based rules from config.
    """
    # Reuse the resource server data
    servers = get_resource_servers(search, priority, environment, app_code)
    if not servers:
        return {"disk_critical": [], "overloaded": [], "underutilised": [], "fast_growing_db": [], "alert_storms": []}

    disk_critical = []
    overloaded = []
    underutilised = []
    fast_growing_db = []
    alert_storms = []

    for s in servers:
        # Disk running out (< 90 days runway or >90% full)
        if (0 < s["storage_runway_days"] < 90) or s["disk_max_util_pct"] > 90:
            disk_critical.append({
                "name": s["name"],
                "environment": s["environment"],
                "priority": s["priority"],
                "disk_free_gb": s["disk_free_gb"],
                "disk_used_gb": s["disk_used_gb"],
                "disk_total_gb": s["disk_total_gb"],
                "disk_util_pct": s["disk_max_util_pct"],
                "db_growth_gb_day": s["db_growth_gb_day"],
                "runway_days": s["storage_runway_days"],
                "reason": f"{'Disk >90% full' if s['disk_max_util_pct'] > 90 else ''}"
                          f"{' + ' if s['disk_max_util_pct'] > 90 and 0 < s['storage_runway_days'] < 90 else ''}"
                          f"{'Fills in ' + str(s['storage_runway_days']) + 'd at current DB growth' if 0 < s['storage_runway_days'] < 90 else ''}",
            })

        # Overloaded (CPU > 75% OR RAM > 80%)
        cpu_high = s["cpu_load_pct"] > settings.overload_pct
        ram_high = s["ram_load_pct"] > 80
        if cpu_high or ram_high:
            parts = []
            if cpu_high:
                parts.append(f"CPU {s['cpu_load_pct']}%")
            if ram_high:
                parts.append(f"RAM {s['ram_load_pct']}%")
            overloaded.append({
                "name": s["name"],
                "environment": s["environment"],
                "priority": s["priority"],
                "cpu_count": s["cpu_count"],
                "cpu_load_pct": s["cpu_load_pct"],
                "ram_gb": s["ram_gb"],
                "ram_load_pct": s["ram_load_pct"],
                "cpu_days_left": s["cpu_days_left"],
                "ram_days_left": s["ram_days_left"],
                "bottleneck": "CPU + RAM" if cpu_high and ram_high else ("CPU" if cpu_high else "RAM"),
                "reason": " and ".join(parts) + " above threshold",
            })

        # Underutilised (CPU < 15% AND vcpu >= 8, or RAM < 20% AND ram >= 16GB)
        cpu_idle = s["cpu_load_pct"] < settings.zombie_load_pct and s["cpu_count"] >= settings.zombie_min_vcpu
        ram_idle = s["ram_load_pct"] > 0 and s["ram_load_pct"] < 20 and s["ram_gb"] >= 16
        if cpu_idle or ram_idle:
            parts = []
            if cpu_idle:
                parts.append(f"CPU {s['cpu_load_pct']}% on {s['cpu_count']} vCPU")
            if ram_idle:
                parts.append(f"RAM {s['ram_load_pct']}% on {s['ram_gb']}GB")
            underutilised.append({
                "name": s["name"],
                "environment": s["environment"],
                "priority": s["priority"],
                "cpu_count": s["cpu_count"],
                "cpu_load_pct": s["cpu_load_pct"],
                "ram_gb": s["ram_gb"],
                "ram_load_pct": s["ram_load_pct"],
                "total_alerts": s["total_alerts"],
                "reason": " and ".join(parts),
            })

        # Fast-growing databases (> 500 MB/day)
        if s["db_growth_gb_day"] > 0.5:
            fast_growing_db.append({
                "name": s["name"],
                "environment": s["environment"],
                "priority": s["priority"],
                "db_count": s["db_count"],
                "db_total_size_gb": s["db_total_size_gb"],
                "db_growth_gb_day": s["db_growth_gb_day"],
                "disk_free_gb": s["disk_free_gb"],
                "runway_days": s["storage_runway_days"],
                "reason": f"DB growing {s['db_growth_gb_day']:.1f} GB/day",
            })

        # Alert storms (many alerts but low load — noisy triggers)
        if s["total_alerts"] > settings.alert_storm_threshold and s["cpu_load_pct"] < 30:
            alert_storms.append({
                "name": s["name"],
                "environment": s["environment"],
                "priority": s["priority"],
                "total_alerts": s["total_alerts"],
                "cpu_load_pct": s["cpu_load_pct"],
                "reason": f"{s['total_alerts']} alerts but only {s['cpu_load_pct']}% CPU — likely noisy triggers",
            })

    # Sort each group by severity
    disk_critical.sort(key=lambda x: x["runway_days"])
    overloaded.sort(key=lambda x: max(x["cpu_load_pct"], x["ram_load_pct"]), reverse=True)
    underutilised.sort(key=lambda x: x["cpu_count"], reverse=True)
    fast_growing_db.sort(key=lambda x: x["db_growth_gb_day"], reverse=True)
    alert_storms.sort(key=lambda x: x["total_alerts"], reverse=True)

    return {
        "disk_critical": disk_critical,
        "overloaded": overloaded,
        "underutilised": underutilised,
        "fast_growing_db": fast_growing_db,
        "alert_storms": alert_storms,
    }
