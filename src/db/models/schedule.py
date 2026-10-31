from db.session import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from subjects import Subject
from sqlalchemy import UniqueConstraint


class Schedule(Base):
    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    weekday: Mapped[int]
    lesson_number: Mapped[int]
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))

    subject: Mapped["Subject"] = relationship(back_populates="schedules")
    __table_args__ = (
        UniqueConstraint("weekday", "lesson_number"),
    )
