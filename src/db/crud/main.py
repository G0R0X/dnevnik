from collections import defaultdict
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import homeworks, homework_files, homework_links, homework_tasks, schedule, subjects
from sqlalchemy import select, and_, delete, or_
from datetime import date
from aiohttp import ClientSession


async def write_homeworks(homs: list[homeworks.Homework], db_session: AsyncSession, http_session: ClientSession):
    from net.main import save_file
    files = []
    for hom in homs:
        for file in hom.homework_files:
            files.append(file)
        db_session.add(hom)
    await db_session.flush()
    for f in files:
        await save_file(f, http_session)
    await db_session.commit()


async def get_homeworks_id(session: AsyncSession):
    result = await session.execute(select(homeworks.Homework.kiasuo_id))
    return set(result.scalars().all())


async def get_homeworks_by_kiasuo_ids(session: AsyncSession, kiasuo_ids) -> dict[int, homeworks.Homework]:
    """Строки ДЗ по их id в Киасуо — чтобы обновить даты у уже загруженных."""
    ids = list(kiasuo_ids)
    if not ids:
        return {}
    result = await session.execute(
        select(homeworks.Homework).where(
            homeworks.Homework.kiasuo_id.in_(ids))
    )
    return {hw.kiasuo_id: hw for hw in result.scalars().all()}


async def get_schedule_for_day(session: AsyncSession, weekday: int):
    result = await session.execute(select(schedule.Schedule.subject_id).where(schedule.Schedule.weekday == weekday))
    return result.scalars().all()


def _homework_days(day: date):
    """ДЗ относится ко дню, если это день урока или день, к которому сдать.

    В дневнике ДЗ висит на уроке, где его задали (`lesson_date`), а «сдать к»
    может быть позже (`check_at`). Показываем в оба дня: иначе задания,
    выданные раньше, пропадают из дня сдачи.
    """
    return or_(
        homeworks.Homework.lesson_date == day,
        homeworks.Homework.check_at == day,
    )


async def get_homework_for_day(session: AsyncSession, day: date):
    result = await session.execute(select(homeworks.Homework.id, homeworks.Homework.text, homeworks.Homework.subject_id).where(_homework_days(day)))
    homs = defaultdict(dict)
    for homework in result:
        if homework[1].strip():
            homs[homework[2]]["text"] = True
        current_files, current_links = await asyncio.gather(session.execute(select(homework_files.HomeworkFile).where(homework_files.HomeworkFile.homework_id == homework[0])), session.execute(select(homework_links.HomeworkLink).where(homework_links.HomeworkLink.homework_id == homework[0])))
        if current_files.all():
            homs[homework[2]]["files"] = True
        if current_links.all():
            homs[homework[2]]["links"] = True
    return homs


async def get_homework_for_lesson(session: AsyncSession, day: date, subject_id: int):
    result = await session.execute(
        select(
            homeworks.Homework.id,
            homeworks.Homework.text,
            homeworks.Homework.check_at,
            homeworks.Homework.lesson_date,
        ).where(and_(
            homeworks.Homework.subject_id == subject_id,
            _homework_days(day),
        ))
    )
    homeworks_lst = []
    files = []
    links = []
    homework_id = None
    for homework in result:
        homeworks_lst.append(homework)
        current_files, current_links = await asyncio.gather(session.execute(select(homework_files.HomeworkFile).where(homework_files.HomeworkFile.homework_id == homework[0])), session.execute(select(homework_links.HomeworkLink).where(homework_links.HomeworkLink.homework_id == homework[0])))
        files.extend(current_files.all())
        links.extend(current_links.all())
        if not homework_id:
            homework_id = homework[0]
    return (homeworks_lst, files, links, homework_id)


async def write_homework_tasks(session: AsyncSession, task: homework_tasks.HomeworkTask):
    session.add(task)
    await session.commit()


async def get_homework(session: AsyncSession, homework_id: int) -> homeworks.Homework | None:
    result = await session.execute(
        select(homeworks.Homework).where(homeworks.Homework.id == homework_id)
    )
    return result.scalar_one_or_none()


async def get_subject(session: AsyncSession, subject_id: int) -> subjects.Subject | None:
    result = await session.execute(
        select(subjects.Subject).where(subjects.Subject.id == subject_id)
    )
    return result.scalar_one_or_none()


async def get_subject_names(session: AsyncSession) -> dict[int, str]:
    """id предмета -> русское название (слаг, если name_ru ещё не заполнен)."""
    result = await session.execute(
        select(
            subjects.Subject.id,
            subjects.Subject.name,
            subjects.Subject.name_ru,
        )
    )
    return {subject_id: name_ru or name
            for subject_id, name, name_ru in result}


async def get_subject_ids_by_name(session: AsyncSession) -> dict[str, int]:
    """Русское название предмета -> id (по нему дневник отдаёт предмет в ДЗ)."""
    result = await session.execute(
        select(subjects.Subject.id, subjects.Subject.name_.u)
    )
    return {name_ru: subject_id
            for subject_id, name_ru in result if name_ru}


async def get_materials(session: AsyncSession, homework_id: int) -> tuple[list[homework_files.HomeworkFile], list[homework_links.HomeworkLink]]:
    files = (await session.execute(
        select(homework_files.HomeworkFile).where(
            homework_files.HomeworkFile.homework_id == homework_id)
    )).scalars().all()
    links = (await session.execute(
        select(homework_links.HomeworkLink).where(
            homework_links.HomeworkLink.homework_id == homework_id)
    )).scalars().all()
    return list(files), list(links)


async def rewrite_homework_tasks(session: AsyncSession, homework_id: int, tasks: list[homework_tasks.HomeworkTask], force: bool = False) -> bool:
    """Перезаписывает задачи ДЗ.

    Без `force` существующие задачи не трогает: при переподготовке у них
    терялись сохранённые решения и менялись id, а на старые id ссылаются
    кнопки в уже отправленных сообщениях бота.
    """
    if not force:
        existing = await get_homework_tasks(session, homework_id)
        if existing:
            return False
    await session.execute(
        delete(homework_tasks.HomeworkTask).where(
            homework_tasks.HomeworkTask.homework_id == homework_id)
    )
    for task in tasks:
        task.homework_id = homework_id
        session.add(task)
    await session.commit()
    return True


async def get_homework_tasks(session: AsyncSession, homework_id: int) -> list[homework_tasks.HomeworkTask]:
    result = await session.execute(
        select(homework_tasks.HomeworkTask).where(
            homework_tasks.HomeworkTask.homework_id == homework_id
        )
    )
    return result.scalars().all()


async def get_solvable_homework_tasks(session: AsyncSession, homework_id: int) -> list[homework_tasks.HomeworkTask]:
    result = await session.execute(
        select(homework_tasks.HomeworkTask).where(and_(
            homework_tasks.HomeworkTask.homework_id == homework_id, homework_tasks.HomeworkTask.status == "ready"))
    )
    return result.scalars().all()


async def get_homework_task(session: AsyncSession, task_id: int) -> homework_tasks.HomeworkTask | None:
    result = await session.execute(
        select(homework_tasks.HomeworkTask).where(
            homework_tasks.HomeworkTask.id == task_id
        )
    )
    return result.scalar_one_or_none()


async def save_task_solution(session: AsyncSession, task_id: int, answer: str):
    task = await session.get(homework_tasks.HomeworkTask, task_id)
    if task is None:
        return
    task.solution = answer
    await session.commit()


async def main():
    from db.session import SessionLocal
    async with SessionLocal() as session:
        res = await get_homework_for_day(session, date(2026, 10, 13))
        print(res)


if __name__ == "__main__":
    asyncio.run(main())
