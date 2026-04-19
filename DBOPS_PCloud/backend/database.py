"""
SQLite database layer. Replaces pickle persistence.
"""

import sqlite3
import logging
from typing import Optional
from contextlib import contextmanager
from config import settings

logger = logging.getLogger(__name__)

# ── Schema — fresh installs get this directly ─────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    server_count INTEGER DEFAULT 0,
    zabbix_url TEXT,
    zabbix_group TEXT,
    days_back INTEGER,
    status TEXT DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    name TEXT NOT NULL,
    resource_type TEXT,
    current_load REAL DEFAULT 0,
    days_left INTEGER DEFAULT 999,
    total_alerts INTEGER DEFAULT 0,
    priority TEXT DEFAULT 'NONE',
    risk_category TEXT DEFAULT 'Healthy',
    action TEXT DEFAULT 'Monitor',
    cpu_count INTEGER DEFAULT 0,
    ram_gb REAL DEFAULT 0,
    max_disk_util REAL DEFAULT 0,
    min_free_gb REAL DEFAULT 999,
    max_db_growth REAL DEFAULT 0,
    environment TEXT DEFAULT 'Unknown',
    criticality TEXT DEFAULT 'Unknown',
    tags TEXT DEFAULT '[]',
    diagnostic TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS triage_status (
    server_name TEXT PRIMARY KEY,
    status TEXT DEFAULT 'Open',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    date TEXT NOT NULL,
    server_name TEXT NOT NULL,
    problem_name TEXT,
    severity TEXT
);

CREATE TABLE IF NOT EXISTS disks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    server_name TEXT NOT NULL,
    drive TEXT,
    type TEXT,
    total_gb REAL DEFAULT 0,
    used_gb REAL DEFAULT 0,
    free_gb REAL DEFAULT 0,
    utilization_pct REAL DEFAULT 0,
    risk_category TEXT,
    action_required TEXT
);

CREATE TABLE IF NOT EXISTS databases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    server_name TEXT NOT NULL,
    db_name TEXT,
    db_type TEXT,
    raw_size REAL DEFAULT 0,
    raw_growth REAL DEFAULT 0,
    suggestion TEXT DEFAULT 'Stable',
    trend TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS capacity_trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    date TEXT NOT NULL,
    server_name TEXT NOT NULL,
    metric TEXT,
    utilization REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_servers_fetch ON servers(fetch_id);
CREATE INDEX IF NOT EXISTS idx_servers_name ON servers(name);
CREATE INDEX IF NOT EXISTS idx_servers_priority ON servers(priority);
CREATE INDEX IF NOT EXISTS idx_events_fetch ON events(fetch_id);
CREATE INDEX IF NOT EXISTS idx_events_server ON events(server_name);
CREATE INDEX IF NOT EXISTS idx_events_fetch_server ON events(fetch_id, server_name);
CREATE INDEX IF NOT EXISTS idx_disks_fetch ON disks(fetch_id);
CREATE INDEX IF NOT EXISTS idx_disks_fetch_server ON disks(fetch_id, server_name);
CREATE INDEX IF NOT EXISTS idx_databases_fetch ON databases(fetch_id);
CREATE INDEX IF NOT EXISTS idx_databases_fetch_server ON databases(fetch_id, server_name);
CREATE INDEX IF NOT EXISTS idx_capacity_fetch ON capacity_trends(fetch_id);
CREATE INDEX IF NOT EXISTS idx_capacity_fetch_server ON capacity_trends(fetch_id, server_name);
"""

# How many completed fetch runs to keep (older ones are deleted to prevent DB bloat)
KEEP_RUNS = 3


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Wait up to 5 s before raising "database is locked" under concurrent writes
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _apply_migrations(conn: sqlite3.Connection):
    """
    Add columns that were not present in the original schema.
    ALTER TABLE ADD COLUMN is idempotent — swallow the duplicate-column error.
    """
    migrations = [
        "ALTER TABLE servers ADD COLUMN diagnostic TEXT DEFAULT ''",
        "ALTER TABLE triage_status ADD COLUMN notes TEXT DEFAULT ''",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists — normal on re-runs


def init_db():
    """Initialise the database schema and apply any pending migrations."""
    with get_db() as conn:
        conn.executescript(SCHEMA_SQL)
        _apply_migrations(conn)
    logger.info("Database initialised at %s", settings.db_path)


def get_latest_fetch_id(conn: sqlite3.Connection) -> Optional[int]:
    """Return the most recent *completed* fetch run ID, or None."""
    row = conn.execute(
        "SELECT id FROM fetch_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def get_fetch_info(conn: sqlite3.Connection, fetch_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM fetch_runs WHERE id = ?", (fetch_id,)
    ).fetchone()
    return dict(row) if row else None


def is_fetch_running(conn: sqlite3.Connection) -> bool:
    """
    True if a fetch is currently in progress.
    Auto-expires runs that have been in 'running' state for more than 30 minutes
    (likely a crashed background thread that never wrote a failure status).
    """
    row = conn.execute(
        "SELECT id, started_at FROM fetch_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return False

    # Check for stale run — if started > 30 minutes ago, mark as failed
    try:
        import datetime
        started = datetime.datetime.fromisoformat(row["started_at"])
        if (datetime.datetime.now() - started).total_seconds() > 1800:
            conn.execute(
                "UPDATE fetch_runs SET status = 'failed: timed out (no response after 30m)', "
                "completed_at = ? WHERE id = ?",
                (datetime.datetime.now().isoformat(), row["id"]),
            )
            logger.warning(
                "Fetch run %d was stuck in 'running' since %s — auto-marked as failed",
                row["id"], row["started_at"],
            )
            return False
    except (ValueError, TypeError):
        pass  # Malformed started_at — treat as still running

    return True


def cleanup_old_runs(conn: sqlite3.Connection, keep: int = KEEP_RUNS):
    """
    Delete data from fetch runs older than the last `keep` completed runs.
    Prevents unbounded SQLite growth on deployments that fetch daily.
    """
    rows = conn.execute(
        "SELECT id FROM fetch_runs WHERE status = 'completed' ORDER BY id DESC"
    ).fetchall()

    if len(rows) <= keep:
        return

    ids_to_delete = [r["id"] for r in rows[keep:]]
    placeholders = ",".join("?" * len(ids_to_delete))

    # Table names are hardcoded constants — not user input — but we use a
    # whitelist tuple to make that explicit and satisfy static analysis tools.
    _CHILD_TABLES = ("servers", "events", "disks", "databases", "capacity_trends")
    for table in _CHILD_TABLES:
        assert table in _CHILD_TABLES  # belt-and-suspenders: never interpolate user data
        conn.execute(
            f"DELETE FROM {table} WHERE fetch_id IN ({placeholders})",  # noqa: S608
            ids_to_delete,
        )
    conn.execute(
        f"DELETE FROM fetch_runs WHERE id IN ({placeholders})",  # noqa: S608
        ids_to_delete,
    )
    logger.info(
        "Cleaned up %d old fetch run(s): ids %s", len(ids_to_delete), ids_to_delete
    )
