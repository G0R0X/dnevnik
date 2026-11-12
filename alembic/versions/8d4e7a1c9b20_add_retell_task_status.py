"""add retell task status

Новое значение `retell` в enum `taskstatus`: задание сводится к чтению
или пересказу параграфа, и ИИ готовит пересказ по тексту страниц.

Revision ID: 8d4e7a1c9b20
Revises: 1f3c9b7a2e50
Create Date: 2026-11-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8d4e7a1c9b20"
down_revision: Union[str, Sequence[str], None] = "1f3c9b7a2e50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE нельзя использовать в той же транзакции, где появилось
    # значение (до PostgreSQL 12 — вообще нельзя), поэтому выходим в autocommit.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'retell'")


def downgrade() -> None:
    # Убрать значение из enum можно только пересозданием типа. Сначала
    # переводим существующие задачи в not_solvable, иначе приведение упадёт.
    op.execute(
        "UPDATE homework_tasks SET status = 'not_solvable' "
        "WHERE status = 'retell'"
    )
    op.execute("ALTER TYPE taskstatus RENAME TO taskstatus_old")
    op.execute("CREATE TYPE taskstatus AS ENUM ('ready', 'not_solvable')")
    op.execute(
        "ALTER TABLE homework_tasks ALTER COLUMN status TYPE taskstatus "
        "USING status::text::taskstatus"
    )
    op.execute("DROP TYPE taskstatus_old")
