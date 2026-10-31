from db.session import Base
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from schedule import Schedule
    from homeworks import Homework


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Короткий слаг (algebra, english) — по нему строится textbook_path.
    name: Mapped[str]
    # Русское название для бота и промптов (совпадает с предметом в дневнике).
    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    textbook_path: Mapped[str | None] = mapped_column(
        default=None, nullable=True)

    schedules: Mapped[list["Schedule"]
                      ] = relationship(back_populates="subject")
    homeworks: Mapped[list["Homework"]] = relationship(
        back_populates="subject")
