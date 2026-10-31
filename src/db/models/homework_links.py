from db.session import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from homeworks import Homework


class HomeworkLink(Base):
    __tablename__ = "homework_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    homework_id: Mapped[int] = mapped_column(ForeignKey("homeworks.id"))
    url: Mapped[str]
    title: Mapped[str]

    homework: Mapped["Homework"] = relationship(
        back_populates="homework_links")
