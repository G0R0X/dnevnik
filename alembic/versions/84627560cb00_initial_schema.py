"""initial schema

Полная схема БД одним куском: история миграций свёрнута, чтобы на новой
машине хватало `alembic upgrade head`. Дальше схема меняется новыми
миграциями.

Revision ID: 84627560cb00
Revises:
Create Date: 2026-09-20 20:56:28.399921

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '84627560cb00'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('subjects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('name_ru', sa.Text(), nullable=True),
    sa.Column('textbook_path', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('homeworks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kiasuo_id', sa.Integer(), nullable=False),
    sa.Column('subject_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.String(), nullable=False),
    sa.Column('lesson_date', sa.Date(), nullable=True),
    sa.Column('check_at', sa.Date(), nullable=False),
    sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kiasuo_id')
    )
    op.create_table('schedule',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=False),
    sa.Column('lesson_number', sa.Integer(), nullable=False),
    sa.Column('subject_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('weekday', 'lesson_number')
    )
    op.create_table('homework_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('homework_id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['homework_id'], ['homeworks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('homework_links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('homework_id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['homework_id'], ['homeworks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('homework_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('homework_id', sa.Integer(), nullable=False),
    sa.Column('instruction', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('ready', 'not_solvable', name='taskstatus'), nullable=False),
    sa.Column('textbook_paragraphs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('textbook_pages', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('solution', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['homework_id'], ['homeworks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_homework_tasks_homework_id'),
                    'homework_tasks', ['homework_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_homework_tasks_homework_id'),
                  table_name='homework_tasks')
    op.drop_table('homework_tasks')
    op.drop_table('homework_links')
    op.drop_table('homework_files')
    op.drop_table('schedule')
    op.drop_table('homeworks')
    op.drop_table('subjects')
