"""create_recommendation_foundation_tables

Revision ID: e5ba185f0ea9
Revises: 99a16ab0c49b
Create Date: 2026-11-03 21:33:40.987151

"""
from typing import Sequence, Union

from alembic import op
import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5ba185f0ea9'
down_revision: Union[str, Sequence[str], None] = '99a16ab0c49b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Ensure pgvector extension is enabled
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 1. Update user_interactions check constraint to support recommendation feedback events
    op.drop_constraint('ck_user_interactions_type', 'user_interactions', type_='check')
    op.create_check_constraint(
        'ck_user_interactions_type',
        'user_interactions',
        "interaction_type IN ('view', 'click', 'save', 'dismiss', 'rate', 'start', 'progress', 'complete', 'abandon', 'recommendation_click', 'recommendation_helpful', 'recommendation_not_helpful')"
    )

    # 2. Create reading_goals table
    op.create_table(
        'reading_goals',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('goal_type', sa.String(length=40), nullable=False),
        sa.Column('target_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "goal_type IN ('learn_subject', 'read_more', 'explore_genre', 'career_development', 'skill_depth', 'custom')",
            name='ck_reading_goals_goal_type'
        ),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'paused', 'abandoned')",
            name='ck_reading_goals_status'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'user_id', name='uq_reading_goals_id_user')
    )
    op.create_index('ix_reading_goals_user_status', 'reading_goals', ['user_id', 'status'], unique=False)

    # 3. Create book_embeddings table
    op.create_table(
        'book_embeddings',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('work_id', sa.Uuid(), nullable=False),
        sa.Column('model_name', sa.String(length=160), nullable=False),
        sa.Column('model_revision', sa.String(length=160), nullable=False),
        sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False),
        sa.Column('source_text_hash', sa.String(length=128), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['work_id'], ['book_works.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('work_id', 'model_name', 'model_revision', 'source_text_hash', name='uq_book_embeddings_work_model_hash')
    )
    op.create_index(
        'ix_book_embeddings_hnsw',
        'book_embeddings',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'}
    )
    op.create_index(op.f('ix_book_embeddings_work_id'), 'book_embeddings', ['work_id'], unique=False)
    op.create_index(
        'uq_book_embeddings_active_work',
        'book_embeddings',
        ['work_id', 'model_name', 'model_revision'],
        unique=True,
        postgresql_where=sa.text('is_active = true'),
        sqlite_where=sa.text('is_active = 1')
    )

    # 4. Create user_interest_profiles table
    op.create_table(
        'user_interest_profiles',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('model_name', sa.String(length=160), nullable=False),
        sa.Column('model_revision', sa.String(length=160), nullable=False),
        sa.Column('interest_vector', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
        sa.Column('inferred_topics', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('interaction_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_calculated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('interaction_count >= 0', name='ck_user_interest_profiles_interaction_count'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_user_interest_profiles_user_id')
    )

    # 5. Create recommendation_requests table
    op.create_table(
        'recommendation_requests',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('request_type', sa.String(length=40), nullable=False),
        sa.Column('request_context', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('algorithm_version', sa.String(length=40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "request_type IN ('next_read', 'discover', 'tbr_priority', 'goal_based', 'mood_based', 'new_author')",
            name='ck_recommendation_requests_type'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_recommendation_requests_user_created',
        'recommendation_requests',
        ['user_id', sa.literal_column('created_at DESC')],
        unique=False
    )

    # 6. Create recommendation_items table
    op.create_table(
        'recommendation_items',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('work_id', sa.Uuid(), nullable=False),
        sa.Column('rank_position', sa.Integer(), nullable=False),
        sa.Column('score', sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column('score_breakdown', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('reason_codes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('feedback_type', sa.String(length=30), nullable=True),
        sa.Column('feedback_reason', sa.Text(), nullable=True),
        sa.Column('feedback_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "feedback_type IS NULL OR feedback_type IN ('helpful', 'not_helpful', 'save', 'dismiss')",
            name='ck_recommendation_items_feedback_type'
        ),
        sa.CheckConstraint('rank_position >= 1', name='ck_recommendation_items_rank_position'),
        sa.CheckConstraint('score >= 0.000000 AND score <= 1.000000', name='ck_recommendation_items_score_range'),
        sa.ForeignKeyConstraint(['request_id'], ['recommendation_requests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['work_id'], ['book_works.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_id', 'rank_position', name='uq_recommendation_items_request_rank'),
        sa.UniqueConstraint('request_id', 'work_id', name='uq_recommendation_items_request_work')
    )
    op.create_index(op.f('ix_recommendation_items_work_id'), 'recommendation_items', ['work_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_recommendation_items_work_id'), table_name='recommendation_items')
    op.drop_table('recommendation_items')
    op.drop_index('ix_recommendation_requests_user_created', table_name='recommendation_requests')
    op.drop_table('recommendation_requests')
    op.drop_table('user_interest_profiles')
    op.drop_index('uq_book_embeddings_active_work', table_name='book_embeddings', postgresql_where=sa.text('is_active = true'), sqlite_where=sa.text('is_active = 1'))
    op.drop_index(op.f('ix_book_embeddings_work_id'), table_name='book_embeddings')
    op.drop_index('ix_book_embeddings_hnsw', table_name='book_embeddings', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('book_embeddings')
    op.drop_index('ix_reading_goals_user_status', table_name='reading_goals')
    op.drop_table('reading_goals')

    # Revert user_interactions check constraint
    op.drop_constraint('ck_user_interactions_type', 'user_interactions', type_='check')
    op.create_check_constraint(
        'ck_user_interactions_type',
        'user_interactions',
        "interaction_type IN ('view', 'click', 'save', 'dismiss', 'rate', 'start', 'progress', 'complete', 'abandon', 'recommendation_click')"
    )
