"""
Centralized configuration. All thresholds and constants from the original app.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    db_path: str = Field(default="db_infra_ops.sqlite", description="SQLite database file path")

    # Zabbix defaults (overridden at runtime via API)
    zabbix_url: str = ""
    zabbix_token: str = ""
    zabbix_group: str = "PAASDB"
    days_back: int = 30

    # API timeouts
    api_timeout_short: int = 120
    api_timeout_medium: int = 300
    api_timeout_long: int = 600

    # Processing limits
    trend_chunk_size: int = 200

    # Scheduler
    fetch_interval_hours: int = 24

    # Risk thresholds (from original app, centralized here)
    system_drive_critical_pct: float = 95.0
    system_drive_warning_pct: float = 90.0
    system_drive_low_free_gb: float = 2.0
    db_drive_critical_pct: float = 95.0
    db_drive_warning_pct: float = 90.0
    cpu_runway_critical_days: int = 30
    db_growth_warning_gb: float = 5.0
    alert_storm_threshold: int = 10
    underutil_load_pct: float = 10.0
    silent_fail_load_pct: float = 75.0
    overload_pct: float = 75.0
    zombie_load_pct: float = 15.0
    zombie_min_vcpu: int = 8
    forecast_target_pct: float = 95.0


settings = Settings()
