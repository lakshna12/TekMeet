"""Initial Database Schema for TekMeet (scheduled_meetings, meeting_recordings, meeting_summaries)

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-26 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Table 1: scheduled_meetings
    op.create_table(
        'scheduled_meetings',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('meeting_link_or_id', sa.String(length=500), nullable=False),
        sa.Column('scheduled_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organizer_email', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='Scheduled'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_scheduled_meetings_id', 'scheduled_meetings', ['id'], unique=False)
    op.create_index('ix_scheduled_meetings_scheduled_time', 'scheduled_meetings', ['scheduled_time'], unique=False)
    op.create_index('ix_scheduled_meetings_organizer_email', 'scheduled_meetings', ['organizer_email'], unique=False)
    op.create_index('ix_scheduled_meetings_status', 'scheduled_meetings', ['status'], unique=False)
    op.create_index('ix_scheduled_meetings_status_time', 'scheduled_meetings', ['status', 'scheduled_time'], unique=False)

    # Table 2: meeting_recordings
    op.create_table(
        'meeting_recordings',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('meeting_id', sa.String(length=100), nullable=False),
        sa.Column('audio_storage_path', sa.String(length=500), nullable=False),
        sa.Column('transcript_text', sa.Text(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['meeting_id'], ['scheduled_meetings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_meeting_recordings_id', 'meeting_recordings', ['id'], unique=False)
    op.create_index('ix_meeting_recordings_meeting_id', 'meeting_recordings', ['meeting_id'], unique=False)

    # Table 3: meeting_summaries
    op.create_table(
        'meeting_summaries',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('meeting_id', sa.String(length=100), nullable=False),
        sa.Column('summary_text_json', sa.Text(), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['meeting_id'], ['scheduled_meetings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_meeting_summaries_id', 'meeting_summaries', ['id'], unique=False)
    op.create_index('ix_meeting_summaries_meeting_id', 'meeting_summaries', ['meeting_id'], unique=False)
    op.create_index('ix_meeting_summaries_delivered_at', 'meeting_summaries', ['delivered_at'], unique=False)


def downgrade() -> None:
    op.drop_table('meeting_summaries')
    op.drop_table('meeting_recordings')
    op.drop_table('scheduled_meetings')
