"""v0.3.1 hardening: job runs, investigation search document + list indexes, evidence scan/quarantine

Additive only: new table ``job_runs``, new nullable/defaulted columns, new indexes. Existing rows are
preserved; existing evidence files are marked ``not_scanned`` (quarantined) so they become available to
ordinary users only after a successful scan or an explicit administrator release.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18 17:00:00
"""
import logging
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# Frozen copy of app/services/search.py at the time of this migration (migrations must not import app code
# whose behaviour may change later).
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})


def _normalize(text):
    if not text:
        return ""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _document(*parts):
    base = _normalize(" │ ".join(p for p in parts if p))
    alt = base.translate(_TRANSLIT)
    return base if alt == base else f"{base}\n{alt}"


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    op.create_table(
        'job_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job', sa.String(length=60), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('trigger', sa.String(length=120), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('worker', sa.String(length=120), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed', 'skipped', 'abandoned')", name='ck_job_status'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_job_runs_job_started', 'job_runs', ['job', 'company_id', 'started_at'], unique=False)
    op.create_index('ix_job_runs_status', 'job_runs', ['status'], unique=False)

    # ---- investigations: search document + list indexes
    with op.batch_alter_table('investigations') as b:
        b.add_column(sa.Column('search_text', sa.Text(), nullable=True))
    inv = sa.table('investigations', sa.column('id', sa.Integer), sa.column('number', sa.String),
                   sa.column('title', sa.String), sa.column('description', sa.Text), sa.column('search_text', sa.Text))
    rows = bind.execute(sa.select(inv.c.id, inv.c.number, inv.c.title, inv.c.description)).all()
    for i in range(0, len(rows), 1000):
        chunk = rows[i:i + 1000]
        bind.execute(inv.update().where(inv.c.id == sa.bindparam('_id')).values(search_text=sa.bindparam('_doc')),
                     [{"_id": r.id, "_doc": _document(r.number, r.title, r.description)} for r in chunk])
    op.create_index('ix_inv_company_due', 'investigations', ['company_id', 'due_date'], unique=False)
    op.create_index('ix_inv_company_updated', 'investigations', ['company_id', 'updated_at'], unique=False)
    op.create_index('ix_inv_company_owner_status', 'investigations', ['company_id', 'owner_id', 'status'], unique=False)
    op.create_index('ix_inv_company_line_status', 'investigations', ['company_id', 'line_id', 'status'], unique=False)
    op.create_index('ix_inv_company_resolved', 'investigations', ['company_id', 'resolved_at'], unique=False)
    if pg:
        # pg_trgm is a "trusted" extension (PostgreSQL >= 13): the database owner may create it.
        try:
            with bind.begin_nested():
                bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            op.create_index('ix_inv_search_trgm', 'investigations', ['search_text'], unique=False,
                            postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
        except Exception as exc:  # pragma: no cover - depends on database privileges
            log.warning("pg_trgm not available (%s); investigation search works without the trigram index", exc)

    # ---- evidence: malware scan / quarantine state
    with op.batch_alter_table('investigation_attachments') as b:
        b.add_column(sa.Column('scan_status', sa.String(length=20), server_default='not_scanned', nullable=False))
        b.add_column(sa.Column('scan_detail', sa.String(length=500), nullable=True))
        b.add_column(sa.Column('scanner', sa.String(length=60), nullable=True))
        b.add_column(sa.Column('scanned_at', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('scan_attempts', sa.Integer(), server_default='0', nullable=False))
        b.add_column(sa.Column('released_at', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('released_by_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('release_note', sa.String(length=500), nullable=True))
        b.create_foreign_key('fk_attachment_released_by', 'users', ['released_by_id'], ['id'], ondelete='SET NULL')
        b.create_check_constraint('ck_attachment_scan_status',
                                  "scan_status IN ('pending', 'clean', 'infected', 'failed', 'not_scanned')")
    op.create_index('ix_attachment_scan_status', 'investigation_attachments', ['scan_status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_attachment_scan_status', table_name='investigation_attachments')
    with op.batch_alter_table('investigation_attachments') as b:
        b.drop_constraint('ck_attachment_scan_status', type_='check')
        b.drop_constraint('fk_attachment_released_by', type_='foreignkey')
        for col in ('release_note', 'released_by_id', 'released_at', 'scan_attempts', 'scanned_at', 'scanner',
                    'scan_detail', 'scan_status'):
            b.drop_column(col)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_inv_search_trgm")
    for ix in ('ix_inv_company_resolved', 'ix_inv_company_line_status', 'ix_inv_company_owner_status',
               'ix_inv_company_updated', 'ix_inv_company_due'):
        op.drop_index(ix, table_name='investigations')
    with op.batch_alter_table('investigations') as b:
        b.drop_column('search_text')
    op.drop_index('ix_job_runs_status', table_name='job_runs')
    op.drop_index('ix_job_runs_job_started', table_name='job_runs')
    op.drop_table('job_runs')
