"""seed subjects, schedule and russian subject names

Базовые справочники, без которых бот не показывает день: предметы
(фиксированные id, на них ссылаются homeworks и schedule) и недельное
расписание. На новой машине достаточно `alembic upgrade head`.

Revision ID: 1f3c9b7a2e50
Revises: da4bd57a4825
Create Date: 2026-10-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f3c9b7a2e50'
down_revision: Union[str, Sequence[str], None] = '84627560cb00'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# id -> (слаг, русское название, путь к учебнику)
SUBJECTS = [
    (31, "algebra", "Алгебра и начала математического анализа", "/algebra/1"),
    (32, "english", "Иностранный (английский) язык", "/english/1"),
    (33, "geometry", "Геометрия", "/geometry/1"),
    (34, "history", "История", "/history/1"),
    (35, "informatics", "Информатика", "/informatics/1"),
    (36, "physics", "Физика", "/physics/1"),
    (37, "obzr", "Основы безопасности и защиты Родины", None),
    (38, "rov", "Разговоры о важном", None),
    (39, "vs", "Вероятность и статистика", None),
    (40, "geography", "География", None),
    (41, "russian", "Русский язык", None),
    (42, "literature", "Литература", None),
    (43, "biology", "Биология", None),
    (44, "society", "Обществознание", None),
    (45, "gym", "Физическая культура", None),
    (46, "chemistry", "Химия", None),
]

# (день недели, номер урока, id предмета)
SCHEDULE = [
    (1, 1, 38), (1, 2, 31), (1, 3, 31), (1, 4, 34), (1, 5, 44),
    (1, 6, 44), (1, 7, 45), (1, 8, 32),
    (2, 1, 33), (2, 2, 33), (2, 3, 35), (2, 4, 35), (2, 5, 42),
    (2, 6, 32), (2, 7, 36),
    (3, 1, 46), (3, 2, 37), (3, 3, 34), (3, 4, 32), (3, 5, 32),
    (3, 6, 36), (3, 7, 36),
    (4, 1, 31), (4, 2, 31), (4, 3, 41), (4, 4, 42), (4, 5, 35),
    (4, 6, 35), (4, 7, 40),
    (5, 1, 43), (5, 2, 33), (5, 3, 39), (5, 4, 41), (5, 5, 42),
    (5, 6, 45), (5, 7, 36), (5, 8, 36),
]


def upgrade() -> None:
    """Заполняет справочники, если их ещё нет."""
    bind = op.get_bind()

    # Существующие строки не перезаписываем: textbook_path мог быть
    # настроен под другую машину, а id — те же.
    bind.execute(
        sa.text(
            "INSERT INTO subjects (id, name, name_ru, textbook_path) "
            "VALUES (:id, :name, :name_ru, :textbook_path) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        [
            {"id": id_, "name": name, "name_ru": name_ru,
             "textbook_path": textbook_path}
            for id_, name, name_ru, textbook_path in SUBJECTS
        ],
    )
    bind.execute(
        sa.text(
            "UPDATE subjects SET name_ru = :name_ru "
            "WHERE id = :id AND name_ru IS NULL"
        ),
        [
            {"id": id_, "name_ru": name_ru}
            for id_, _name, name_ru, _path in SUBJECTS
        ],
    )

    # id задавали руками — иначе sequence останется на 1 и следующий
    # INSERT в subjects упадёт на конфликте первичного ключа.
    sequence = bind.execute(
        sa.text("SELECT pg_get_serial_sequence('subjects', 'id')")
    ).scalar()
    if sequence:
        bind.execute(sa.text(
            f"SELECT setval('{sequence}', (SELECT MAX(id) FROM subjects))"))

    bind.execute(
        sa.text(
            "INSERT INTO schedule (weekday, lesson_number, subject_id) "
            "VALUES (:weekday, :lesson_number, :subject_id) "
            "ON CONFLICT (weekday, lesson_number) DO NOTHING"
        ),
        [
            {"weekday": weekday, "lesson_number": lesson_number,
             "subject_id": subject_id}
            for weekday, lesson_number, subject_id in SCHEDULE
        ],
    )


def downgrade() -> None:
    """Справочники — рабочие данные, откат их не удаляет."""
    pass
