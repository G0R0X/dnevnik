from db.session import Base
from enum import StrEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import ForeignKey, Enum, Text
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from homeworks import Homework


class TaskStatus(StrEnum):
    READY = "ready"
    NOT_SOLVABLE = "not_solvable"


def _enum_values(enum_cls):
    return [member.value for member in enum_cls]


class HomeworkTask(Base):
    __tablename__ = "homework_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    homework_id: Mapped[int] = mapped_column(
        ForeignKey("homeworks.id"), index=True)
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, values_callable=_enum_values),
        nullable=False,
    )
    textbook_paragraphs: Mapped[list[int] | None] = mapped_column(
        JSONB, nullable=True)
    textbook_pages: Mapped[list[int] | None] = mapped_column(
        JSONB, nullable=True)
    targets: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    solution:  Mapped[str | None] = mapped_column(default=None)
