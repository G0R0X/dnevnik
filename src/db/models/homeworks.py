from db.session import Base
from datetime import date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from homework_files import HomeworkFile
    from homework_links import HomeworkLink
    from subjects import Subject


class Homework(Base):
    __tablename__ = "homeworks"

    id: Mapped[int] = mapped_column(primary_key=True)
    kiasuo_id: Mapped[int] = mapped_column(unique=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    text: Mapped[str]
    # Дата урока, к которому относится ДЗ (как в дневнике). Может быть NULL
    # у строк, загруженных до появления колонки.
    lesson_date: Mapped[date | None] = mapped_column(nullable=True)
    check_at: Mapped[date]

    homework_files: Mapped[list["HomeworkFile"]
                           ] = relationship(back_populates="homework")
    homework_links: Mapped[list["HomeworkLink"]
                           ] = relationship(back_populates="homework")
    subject: Mapped["Subject"] = relationship(back_populates="homeworks")
