"""v0.3.3 evidence reliability: persisted scanner health, evidence file-operation outbox

Additive. Data fix: attachments that v0.3.1/0.3.2 marked ``failed`` only because the scanner was
unreachable (connection refused / command not found / timeout) are reset to ``pending`` with 0 attempts,
because a scanner outage must not consume a file's scan attempts. They stay quarantined.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

OUTAGE_PATTERNS = ("scanner unreachable%", "FileNotFoundError%", "PermissionError%", "TimeoutExpired%",
                   "FP_EVIDENCE_SCAN_COMMAND is not set%", "OSError%")


def upgrade() -> None:
    op.create_table(
        'scanner_health',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scanner', sa.String(length=120), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_ok_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_failure_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('unavailable_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('detail', sa.String(length=500), nullable=True),
        sa.CheckConstraint("status IN ('unknown', 'healthy', 'unavailable')", name='ck_scanner_health_status'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scanner'),
    )
    op.create_table(
        'evidence_file_ops',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('attachment_id', sa.Integer(), nullable=False),
        sa.Column('op', sa.String(length=12), nullable=False),
        sa.Column('reason', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("op IN ('place')", name='ck_file_op'),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name='ck_file_op_status'),
        sa.ForeignKeyConstraint(['attachment_id'], ['investigation_attachments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_file_ops_status', 'evidence_file_ops', ['status'], unique=False)
    op.create_index(op.f('ix_evidence_file_ops_attachment_id'), 'evidence_file_ops', ['attachment_id'], unique=False)

    att = sa.table('investigation_attachments', sa.column('scan_status', sa.String), sa.column('scan_attempts', sa.Integer),
                   sa.column('scan_detail', sa.String), sa.column('released_at', sa.DateTime))
    cond = sa.or_(*[att.c.scan_detail.like(p) for p in OUTAGE_PATTERNS])
    op.get_bind().execute(att.update().where(att.c.scan_status == 'failed', att.c.released_at.is_(None), cond)
                          .values(scan_status='pending', scan_attempts=0,
                                  scan_detail=sa.func.substr(sa.literal('[reset by migration 0005: scanner outage] ')
                                                             + att.c.scan_detail, 1, 500)))


def downgrade() -> None:
    op.drop_index(op.f('ix_evidence_file_ops_attachment_id'), table_name='evidence_file_ops')
    op.drop_index('ix_file_ops_status', table_name='evidence_file_ops')
    op.drop_table('evidence_file_ops')
    op.drop_table('scanner_health')
