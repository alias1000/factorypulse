"""Normalized relational schema.

Every business table carries ``company_id`` so that tenant isolation can be
added later. The MVP serves exactly one company and has NO authorization layer;
it must not be described as multi-tenant.

Time model per production record (all in minutes):

    scheduled_minutes            shift length (optional)
  - planned_downtime_minutes     breaks, planned maintenance (optional)
  = planned_production_minutes   "Planned Production Time" (PPT) – OEE denominator
  - unplanned_downtime_minutes   breakdowns, shortages, unplanned stops
  = operating_minutes            "Run Time"/"Operating Time"

Ideal cycle time is stored in SECONDS per unit.
NULL always means "not supplied" – it is never replaced by zero.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> dt.datetime:
    """Current UTC instant from the injectable application clock (see app/clock.py)."""
    from .clock import now_utc

    return now_utc()


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProductionLine(Base):
    __tablename__ = "production_lines"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_line_company_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    machines: Mapped[list[Machine]] = relationship(back_populates="line")


class Machine(Base):
    __tablename__ = "machines"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_machine_company_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("production_lines.id", ondelete="RESTRICT"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    line: Mapped[ProductionLine] = relationship(back_populates="machines")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_product_company_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_shift_company_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    start_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "06:00"
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ImportJob(Base):
    __tablename__ = "import_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255))  # uuid name inside private upload dir
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int] = mapped_column(Integer)
    file_type: Mapped[str] = mapped_column(String(10))
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    # uploaded | validated | committed | rejected | failed
    mapping: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    total_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    committed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProductionRecord(Base):
    """One machine × shift × product × date production result."""

    __tablename__ = "production_records"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "date", "machine_id", "shift_id", "product_id", name="uq_record_natural_key"
        ),
        CheckConstraint("planned_qty IS NULL OR planned_qty >= 0", name="ck_planned_qty_nonneg"),
        CheckConstraint("actual_qty >= 0", name="ck_actual_qty_nonneg"),
        CheckConstraint("good_qty IS NULL OR good_qty >= 0", name="ck_good_qty_nonneg"),
        CheckConstraint("defective_qty IS NULL OR defective_qty >= 0", name="ck_defective_qty_nonneg"),
        CheckConstraint(
            "good_qty IS NULL OR defective_qty IS NULL OR good_qty + defective_qty = actual_qty",
            name="ck_good_plus_defective",
        ),
        CheckConstraint(
            "planned_production_minutes IS NULL OR planned_production_minutes >= 0", name="ck_ppt_nonneg"
        ),
        CheckConstraint("operating_minutes IS NULL OR operating_minutes >= 0", name="ck_op_nonneg"),
        CheckConstraint(
            "unplanned_downtime_minutes IS NULL OR unplanned_downtime_minutes >= 0", name="ck_udt_nonneg"
        ),
        CheckConstraint(
            "planned_downtime_minutes IS NULL OR planned_downtime_minutes >= 0", name="ck_pdt_nonneg"
        ),
        CheckConstraint("ideal_cycle_time_sec IS NULL OR ideal_cycle_time_sec > 0", name="ck_ict_pos"),
        Index("ix_records_company_date", "company_id", "date"),
        Index("ix_records_line_date", "line_id", "date"),
        Index("ix_records_machine_date", "machine_id", "date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    date: Mapped[dt.date] = mapped_column(Date)
    line_id: Mapped[int] = mapped_column(ForeignKey("production_lines.id", ondelete="RESTRICT"))
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="RESTRICT"))
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id", ondelete="RESTRICT"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)

    planned_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_qty: Mapped[int] = mapped_column(Integer)
    good_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    defective_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)

    scheduled_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    planned_downtime_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    planned_production_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    unplanned_downtime_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    ideal_cycle_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)

    import_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    downtime_events: Mapped[list[DowntimeEvent]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )
    defect_events: Mapped[list[DefectEvent]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )


class DowntimeEvent(Base):
    """Downtime breakdown of a production record by reason.

    The sum of unplanned events of a record equals the record's
    ``unplanned_downtime_minutes`` (enforced at write time by the importer and
    the demo generator).
    """

    __tablename__ = "downtime_events"
    __table_args__ = (CheckConstraint("minutes >= 0", name="ck_dt_minutes_nonneg"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("production_records.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String(200), index=True)
    is_planned: Mapped[bool] = mapped_column(Boolean, default=False)
    minutes: Mapped[float] = mapped_column(Float)
    record: Mapped[ProductionRecord] = relationship(back_populates="downtime_events")


class DefectEvent(Base):
    """Defective units of a production record broken down by defect category."""

    __tablename__ = "defect_events"
    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_defect_qty_nonneg"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("production_records.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(200), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    record: Mapped[ProductionRecord] = relationship(back_populates="defect_events")


class ReportJob(Base):
    __tablename__ = "report_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    report_type: Mapped[str] = mapped_column(String(30))
    file_format: Mapped[str] = mapped_column(String(10))
    period_start: Mapped[dt.date] = mapped_column(Date)
    period_end: Mapped[dt.date] = mapped_column(Date)
    filters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|completed|failed
    stored_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    download_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("company_id", "key", name="uq_setting_company_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Who performed the action. Email is denormalized so the trail survives user changes.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    event: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )


ROLES = ("viewer", "analyst", "admin")


class User(Base):
    """Application user. Users are never hard-deleted (audit trail) – they are deactivated."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_user_email"),
        CheckConstraint("role IN ('viewer', 'analyst', 'admin')", name="ck_user_role"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254))  # stored lower-case
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    preferred_language: Mapped[str | None] = mapped_column(String(5), nullable=True)


class UserSession(Base):
    """Server-side session. Only the SHA-256 of the session token is stored."""

    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    user: Mapped[User] = relationship()


# =============================================================================== Action Center (v0.3)

INV_CATEGORIES = ("equipment_failure", "production_loss", "quality_issue", "excessive_downtime",
                  "process_deviation", "safety_observation", "other")
SEVERITIES = ("low", "medium", "high", "critical")
INV_STATUSES = ("open", "in_progress", "pending_verification", "resolved", "closed", "reopened")
ACTION_STATUSES = ("open", "in_progress", "done", "verified", "cancelled")
VERIFICATION_RESULTS = ("effective", "partially_effective", "not_effective")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Investigation(Base):
    """An operational issue being investigated (manually created or from a detected anomaly)."""

    __tablename__ = "investigations"
    __table_args__ = (
        UniqueConstraint("company_id", "number", name="uq_investigation_number"),
        CheckConstraint(_in("category", INV_CATEGORIES), name="ck_inv_category"),
        CheckConstraint(_in("severity", SEVERITIES), name="ck_inv_severity"),
        CheckConstraint(_in("status", INV_STATUSES), name="ck_inv_status"),
        CheckConstraint("source IN ('manual', 'anomaly')", name="ck_inv_source"),
        Index("ix_inv_company_status", "company_id", "status"),
        Index("ix_inv_company_severity", "company_id", "severity"),
        Index("ix_inv_company_created", "company_id", "created_at"),
        # v0.3.1: indexes for database-side filtering / sorting / pagination of large lists
        Index("ix_inv_company_due", "company_id", "due_date"),
        Index("ix_inv_company_updated", "company_id", "updated_at"),
        Index("ix_inv_company_owner_status", "company_id", "owner_id", "status"),
        Index("ix_inv_company_line_status", "company_id", "line_id", "status"),
        Index("ix_inv_company_resolved", "company_id", "resolved_at"),
        Index("ix_inv_search_trgm", "search_text", postgresql_using="gin",
              postgresql_ops={"search_text": "gin_trgm_ops"}).ddl_if(dialect="postgresql"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(20))  # INV-2026-0001
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(25), default="open")
    source: Mapped[str] = mapped_column(String(10), default="manual")
    line_id: Mapped[int] = mapped_column(ForeignKey("production_lines.id", ondelete="RESTRICT"), index=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id", ondelete="RESTRICT"), nullable=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    # v0.3.1: Unicode-normalised search document (NFKC + casefold, plus ä→ae/ö→oe/ü→ue/ß→ss variant),
    # maintained automatically on insert/update (see app/services/search.py). Trigram-indexed on PostgreSQL.
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    line: Mapped[ProductionLine] = relationship()
    machine: Mapped[Machine | None] = relationship()
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    actions: Mapped[list[CorrectiveAction]] = relationship(back_populates="investigation", cascade="all, delete-orphan",
                                                          order_by="CorrectiveAction.id")
    anomaly_links: Mapped[list[InvestigationAnomalyLink]] = relationship(back_populates="investigation",
                                                                         cascade="all, delete-orphan")


class InvestigationAnomalyLink(Base):
    """Links an investigation to a detected anomaly and freezes the evidence (snapshot)."""

    __tablename__ = "investigation_anomaly_links"
    __table_args__ = (UniqueConstraint("company_id", "anomaly_key", name="uq_anomaly_link_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    anomaly_key: Mapped[str] = mapped_column(String(120))
    anomaly_date: Mapped[dt.date] = mapped_column(Date)
    level: Mapped[str] = mapped_column(String(10))
    entity_code: Mapped[str] = mapped_column(String(50))
    metric: Mapped[str] = mapped_column(String(30))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    investigation: Mapped[Investigation] = relationship(back_populates="anomaly_links")


class CorrectiveAction(Base):
    __tablename__ = "corrective_actions"
    __table_args__ = (
        CheckConstraint(_in("status", ACTION_STATUSES), name="ck_action_status"),
        CheckConstraint(_in("priority", SEVERITIES), name="ck_action_priority"),
        CheckConstraint("verification_result IS NULL OR " + _in("verification_result", VERIFICATION_RESULTS),
                        name="ck_action_verification"),
        Index("ix_action_owner_status", "owner_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    status: Mapped[str] = mapped_column(String(15), default="open")
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_result: Mapped[str | None] = mapped_column(String(25), nullable=True)
    verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    investigation: Mapped[Investigation] = relationship(back_populates="actions")
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id])
    verified_by: Mapped[User | None] = relationship(foreign_keys=[verified_by_id])


class InvestigationComment(Base):
    """Comments are immutable (audit-friendly)."""

    __tablename__ = "investigation_comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_label: Mapped[str] = mapped_column(String(254))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


SCAN_STATUSES = ("pending", "clean", "infected", "failed", "not_scanned")


class InvestigationAttachment(Base):
    """Evidence file stored in the private evidence directory (never served statically). Soft-deleted only."""

    __tablename__ = "investigation_attachments"
    __table_args__ = (
        CheckConstraint(_in("scan_status", SCAN_STATUSES), name="ck_attachment_scan_status"),
        Index("ix_attachment_scan_status", "scan_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(80), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_by_label: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # v0.3.1 malware scanning / quarantine. Files stay in the quarantine directory and are not downloadable
    # for ordinary users until the scanner reports "clean" or an administrator releases them.
    scan_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="not_scanned")
    scan_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scanner: Mapped[str | None] = mapped_column(String(60), nullable=True)
    scanned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scan_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    released_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    release_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class InvestigationEvent(Base):
    """Activity timeline entry (status changes, assignments, comments, actions, evidence, ...)."""

    __tablename__ = "investigation_events"
    __table_args__ = (Index("ix_inv_event_inv_created", "investigation_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(254))
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActionCenterAlert(Base):
    """Persistent state of rule-based alerts. One row per (rule, subject) while the condition is unresolved,
    so the same condition never produces duplicate alerts."""

    __tablename__ = "action_center_alerts"
    __table_args__ = (
        Index("ix_alert_company_open", "company_id", "resolved_at"),
        Index("uq_alert_open_subject", "company_id", "rule", "subject_key", unique=True,
              postgresql_where=text("resolved_at IS NULL"), sqlite_where=text("resolved_at IS NULL")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    rule: Mapped[str] = mapped_column(String(40))
    subject_key: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(10))
    investigation_id: Mapped[int | None] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), nullable=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("corrective_actions.id", ondelete="CASCADE"), nullable=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    first_detected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


JOB_STATUSES = ("running", "succeeded", "failed", "skipped", "abandoned")


class JobRun(Base):
    """One attempt of a background job (alert evaluation, evidence re-scan, ...). Gives operators a
    persistent record of when jobs ran, how long they took, which attempt failed and why."""

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_job_status"),
        Index("ix_job_runs_job_started", "job", "company_id", "started_at"),
        Index("ix_job_runs_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(60))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="running")
    trigger: Mapped[str] = mapped_column(String(120), default="scheduler")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    worker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


HEALTH_STATUSES = ("unknown", "healthy", "unavailable")


class ScannerHealth(Base):
    """Persisted result of the explicit malware-scanner health check (v0.3.3). One row per scanner
    endpoint. Changed ONLY by a health check: a scan run that processes zero files never touches it,
    so an outage warning disappears only after a verified recovery."""

    __tablename__ = "scanner_health"
    __table_args__ = (CheckConstraint(_in("status", HEALTH_STATUSES), name="ck_scanner_health_status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    scanner: Mapped[str] = mapped_column(String(120), unique=True)  # e.g. clamd@clamav:3310, command:clamdscan
    status: Mapped[str] = mapped_column(String(12), default="unknown")
    checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unavailable_since: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


FILE_OPS = ("place",)
FILE_OP_STATUSES = ("pending", "done", "failed")


class EvidenceFileOp(Base):
    """Transactional outbox for evidence file moves (v0.3.3).

    A state change that requires a file to move (scan result clean, admin release) inserts a row in the
    SAME transaction as the state change; the file is moved only after the commit. The operation is
    idempotent and moves the file to the location implied by the CURRENT database state, so it can be
    retried after crashes, failed commits or failed moves (reconciliation job)."""

    __tablename__ = "evidence_file_ops"
    __table_args__ = (
        CheckConstraint(_in("op", FILE_OPS), name="ck_file_op"),
        CheckConstraint(_in("status", FILE_OP_STATUSES), name="ck_file_op_status"),
        Index("ix_file_ops_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    attachment_id: Mapped[int] = mapped_column(ForeignKey("investigation_attachments.id", ondelete="CASCADE"), index=True)
    op: Mapped[str] = mapped_column(String(12), default="place")
    reason: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    done_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

# --------------------------------------------------------------------------- search document maintenance
from sqlalchemy import event  # noqa: E402


@event.listens_for(Investigation, "before_insert")
@event.listens_for(Investigation, "before_update")
def _investigation_search_text(_mapper, _connection, target: Investigation) -> None:
    from .services.search import investigation_document

    target.search_text = investigation_document(target.number, target.title, target.description)
