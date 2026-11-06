"""Application configuration loaded from environment variables.

No credentials are hardcoded: DATABASE_URL must be supplied by the environment
(see .env.example). A local default is only used for the dev SQLite fallback.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FP_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default="sqlite:///./factorypulse-dev.db",
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@db:5432/factorypulse",
    )
    environment: str = "development"
    # Private storage – never served statically.
    data_dir: Path = Path("./var")
    max_upload_mb: int = 10
    max_import_rows: int = 100_000
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    log_level: str = "INFO"
    # Load the SYNTHETIC demo dataset on the very first start (empty database only).
    # None = automatic: only for FP_ENVIRONMENT=development or demo. A production installation
    # never receives synthetic records unless FP_SEED_DEMO_ON_STARTUP=true is set explicitly.
    seed_demo_on_startup: bool | None = None
    # Demo tools (reload demo data / delete all data). Defaults to off in production.
    enable_demo_tools: bool | None = None
    timezone: str = "Europe/Berlin"
    build_sha: str = "dev"

    # --- authentication / sessions -------------------------------------------------
    session_idle_minutes: int = 120
    session_absolute_hours: int = 12
    # None = automatic: secure cookies everywhere except development/test (plain-HTTP localhost)
    cookie_secure: bool | None = None
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    login_ip_max_attempts: int = 60  # generous: whole shifts may sign in behind one NAT address
    login_ip_window_minutes: int = 5
    password_min_length: int = 12
    # Optional one-time bootstrap of the first administrator (only when no user exists).
    initial_admin_email: str | None = None
    initial_admin_password: str | None = None

    # --- backups ---------------------------------------------------------------------
    pg_bin_dir: str | None = None  # directory containing pg_dump / pg_restore (default: PATH)
    backup_retention: int = 14  # number of backups kept (older ones are pruned)

    # --- action center ---------------------------------------------------------------
    max_evidence_mb: int = 10

    # --- background jobs (app.worker) -------------------------------------------------
    alert_interval_seconds: int = Field(300, ge=30, le=86_400)  # rule evaluation schedule
    job_max_attempts: int = Field(3, ge=1, le=10)               # attempts per job run (incl. first)
    job_retry_base_seconds: float = Field(10.0, ge=0, le=600)   # exponential backoff: base, 2×base, ...
    job_history_days: int = Field(30, ge=1, le=3650)            # job_runs older than this are pruned

    # --- evidence malware scanning ------------------------------------------------------
    # none     – no scanner: uploads stay quarantined ("not_scanned") until an admin releases them
    # clamd    – ClamAV daemon via TCP (INSTREAM), e.g. the clamav/clamav container
    # command  – external command, file path appended; exit 0 = clean, 1 = infected, other = error
    evidence_scanner: str = Field("none", pattern="^(none|clamd|command)$")
    clamd_host: str = "clamav"
    clamd_port: int = 3310
    clamd_timeout_seconds: float = 60.0
    evidence_scan_command: str | None = None
    evidence_scan_max_attempts: int = Field(5, ge=1, le=100)
    # v0.3.3: the explicit health check also scans the harmless EICAR test sample and requires a detection
    evidence_scanner_selftest_eicar: bool = True
    # unreferenced files (crash between file write and DB commit) are moved out of the quarantine after this
    evidence_orphan_grace_seconds: int = Field(3600, ge=60, le=30 * 86_400)

    @field_validator("seed_demo_on_startup", "enable_demo_tools", "cookie_secure", mode="before")
    @classmethod
    def _blank_is_automatic(cls, v):
        # docker compose passes unset variables as "" – treat that as "automatic", not as an error
        return None if isinstance(v, str) and v.strip() == "" else v

    @property
    def is_dev(self) -> bool:
        return self.environment in ("development", "test")

    @property
    def is_demo(self) -> bool:
        return self.environment == "demo"

    @property
    def demo_seeding_enabled(self) -> bool:
        """Explicit setting wins; otherwise only development and demo environments are seeded."""
        if self.seed_demo_on_startup is not None:
            return self.seed_demo_on_startup
        return self.environment in ("development", "demo")

    @property
    def demo_tools_enabled(self) -> bool:
        if self.enable_demo_tools is not None:
            return self.enable_demo_tools
        return self.is_dev or self.is_demo

    @property
    def secure_cookies(self) -> bool:
        return (not self.is_dev) if self.cookie_secure is None else self.cookie_secure

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def quarantine_dir(self) -> Path:
        """Evidence that is not (yet) verified clean. Never downloadable for ordinary users."""
        return self.data_dir / "evidence" / "quarantine"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    s.report_dir.mkdir(parents=True, exist_ok=True)
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    s.evidence_dir.mkdir(parents=True, exist_ok=True)
    s.quarantine_dir.mkdir(parents=True, exist_ok=True)
    return s
