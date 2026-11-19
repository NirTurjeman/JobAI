"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-16 20:55:44.942940
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('research_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('agent_backend', sa.String(length=32), nullable=False),
    sa.Column('config_name', sa.String(length=100), nullable=False),
    sa.Column('config_hash', sa.String(length=32), nullable=False),
    sa.Column('records_returned', sa.Integer(), nullable=False),
    sa.Column('records_new', sa.Integer(), nullable=False),
    sa.Column('records_updated', sa.Integer(), nullable=False),
    sa.Column('records_invalid', sa.Integer(), nullable=False),
    sa.Column('notifications_sent', sa.Integer(), nullable=False),
    sa.Column('error_summary', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_runs'))
    )
    op.create_table('agent_errors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('error_type', sa.String(length=100), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('raw_excerpt', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['research_runs.id'], name=op.f('fk_agent_errors_run_id_research_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_errors'))
    )
    with op.batch_alter_table('agent_errors', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_errors_run_id'), ['run_id'], unique=False)

    op.create_table('records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('source_name', sa.String(length=200), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('url_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('content_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('relevance_score', sa.Float(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('discovered_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('notified_at', sa.DateTime(), nullable=True),
    sa.Column('first_run_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['first_run_id'], ['research_runs.id'], name=op.f('fk_records_first_run_id_research_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_records')),
    sa.UniqueConstraint('url_fingerprint', name=op.f('uq_records_url_fingerprint'))
    )
    with op.batch_alter_table('records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_records_content_fingerprint'), ['content_fingerprint'], unique=False)
        batch_op.create_index(batch_op.f('ix_records_discovered_at'), ['discovered_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_records_status'), ['status'], unique=False)
        batch_op.create_index('ix_records_status_score', ['status', 'relevance_score'], unique=False)

    op.create_table('record_scores',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('agent_score', sa.Float(), nullable=False),
    sa.Column('criteria_json', sa.JSON(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('scored_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['records.id'], name=op.f('fk_record_scores_record_id_records'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['research_runs.id'], name=op.f('fk_record_scores_run_id_research_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_record_scores'))
    )
    with op.batch_alter_table('record_scores', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_record_scores_record_id'), ['record_id'], unique=False)

    op.create_table('record_sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.Integer(), nullable=False),
    sa.Column('source_name', sa.String(length=200), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('url_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['record_id'], ['records.id'], name=op.f('fk_record_sources_record_id_records'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['research_runs.id'], name=op.f('fk_record_sources_run_id_research_runs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_record_sources')),
    sa.UniqueConstraint('record_id', 'url_fingerprint', name='uq_record_sources_record_url')
    )
    with op.batch_alter_table('record_sources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_record_sources_record_id'), ['record_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_record_sources_url_fingerprint'), ['url_fingerprint'], unique=False)

    op.create_table('status_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.Integer(), nullable=False),
    sa.Column('old_status', sa.String(length=32), nullable=True),
    sa.Column('new_status', sa.String(length=32), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['records.id'], name=op.f('fk_status_events_record_id_records'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_status_events'))
    )
    with op.batch_alter_table('status_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_status_events_record_id'), ['record_id'], unique=False)

    op.create_table('tracked_actions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('details_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['records.id'], name=op.f('fk_tracked_actions_record_id_records'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tracked_actions'))
    )
    with op.batch_alter_table('tracked_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tracked_actions_record_id'), ['record_id'], unique=False)

    op.create_table('user_feedback',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('feedback', sa.String(length=32), nullable=False),
    sa.Column('comment', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['records.id'], name=op.f('fk_user_feedback_record_id_records'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_feedback'))
    )
    with op.batch_alter_table('user_feedback', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_feedback_record_id'), ['record_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('user_feedback', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_feedback_record_id'))

    op.drop_table('user_feedback')
    with op.batch_alter_table('tracked_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tracked_actions_record_id'))

    op.drop_table('tracked_actions')
    with op.batch_alter_table('status_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_status_events_record_id'))

    op.drop_table('status_events')
    with op.batch_alter_table('record_sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_record_sources_url_fingerprint'))
        batch_op.drop_index(batch_op.f('ix_record_sources_record_id'))

    op.drop_table('record_sources')
    with op.batch_alter_table('record_scores', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_record_scores_record_id'))

    op.drop_table('record_scores')
    with op.batch_alter_table('records', schema=None) as batch_op:
        batch_op.drop_index('ix_records_status_score')
        batch_op.drop_index(batch_op.f('ix_records_status'))
        batch_op.drop_index(batch_op.f('ix_records_discovered_at'))
        batch_op.drop_index(batch_op.f('ix_records_content_fingerprint'))

    op.drop_table('records')
    with op.batch_alter_table('agent_errors', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_errors_run_id'))

    op.drop_table('agent_errors')
    op.drop_table('research_runs')
