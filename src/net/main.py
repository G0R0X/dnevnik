from pathlib import Path
from db.crud.main import get_homeworks_by_kiasuo_ids, get_subject_ids_by_name, write_homeworks
from net.schemas import ScheduleResponse, Lesson, Homework
from dotenv import load_dotenv
import asyncio
from aiohttp import ClientSession
from db.session import SessionLocal
from db.models import homeworks, homework_files, homework_links, schedule, subjects, homework_tasks
import logging
import os
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, timedelta
from ai.prepare import prepare_homework
from file_handling.main import HOMEWORK_DIR, PROJECT_ROOT
import re
import time
load_dotenv()

logger = logging.getLogger(__name__)


url_refresh = "https://diaryapi.kiasuo.ru/diary/refresh"


def update_env(updates: dict[str, str], path: Path | None = None) -> None:
    """Перезаписывает значения в .env по ключам, остальные строки не трогает.

    Если файла нет (контейнер, где секреты приходят переменными окружения),
    он создаётся: иначе обновление токенов падало бы на чтении.
    """
    env_path = Path(path) if path else PROJECT_ROOT / ".env"
    lines = (env_path.read_text(encoding="utf-8").splitlines()
             if env_path.exists() else [])
    result = []
    replaced = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in updates:
            result.append(f"{key}={updates[key]}")
            replaced.add(key)
        else:
            result.append(line)
    result.extend(f"{key}={value}"
                  for key, value in updates.items() if key not in replaced)
    env_path.write_text("\n".join(result) + "\n", encoding="utf-8")


class Token():
    def __init__(self):
        self.access_token = os.getenv("ACCESS_TOKEN")

        self.refresh_token = os.getenv("REFRESH_TOKEN")

    async def refresh(self, session: ClientSession):
        async with session.post(url_refresh, data={"refresh-token": f"{self.refresh_token}"}, headers={"Authorization": f"Bearer {self.access_token}"}) as resp:
            if resp.status != 200:
                raise Exception()

            d = await resp.json()
            acc = d.get("accessToken")
            ref = d.get("refreshToken")

            self.access_token = acc
            self.refresh_token = ref

            # os.environ обновляем вместе с файлом: синк создаёт новый Token()
            # на каждый прогон, и без этого через 20 минут он прочитал бы
            # стартовые (уже недействительные) токены из окружения.
            os.environ["ACCESS_TOKEN"] = acc
            os.environ["REFRESH_TOKEN"] = ref
            try:
                update_env({"ACCESS_TOKEN": acc, "REFRESH_TOKEN": ref})
            except OSError as error:
                # Файл только для чтения (контейнер): в этом процессе токены
                # уже обновлены, но после перезапуска понадобится свежий .env.
                logger.warning("не удалось записать токены в .env: %s", error)


def week_of(day: date) -> tuple[int, int]:
    """ISO-год и номер недели даты."""
    iso = day.isocalendar()
    return iso[0], iso[1]


def get_url_for_week(year: int, week: int) -> str:
    diary_id = os.getenv("DIARY_ID")
    if not diary_id:
        raise RuntimeError("DIARY_ID не задан — добавь id дневника в .env")
    return (f"https://diaryapi.kiasuo.ru/diary/api/schedule"
            f"?id={diary_id}&week={week}&year={year}")


async def get_data_for_week(year: int, week: int, token: Token, session: ClientSession) -> ScheduleResponse:
    url = get_url_for_week(year, week)
    for _ in range(2):
        async with session.get(url, headers={"Authorization": f"Bearer {token.access_token}"}) as response:
            if response.status == 401:
                await token.refresh(session)
                continue
            response.raise_for_status()
            data = await response.json()
            result = ScheduleResponse.model_validate(data)
            return result


async def get_data_for_day(day: date, token: Token, session, data: ScheduleResponse | None = None) -> list[Lesson]:
    if data is None:
        data = await get_data_for_week(*week_of(day), token, session)
    lessons = []
    for lesson in data.schedule:
        if lesson.lesson_date == day:
            lessons.append(lesson)
    return lessons


async def get_homework_for_week(year, week, token, http_session, subject_ids: dict[str, int]):
    homs = []

    downloaded_weeks = {}
    downloaded_days = {}
    unique_homeworks = set()

    data = await get_data_for_week(year, week, token, http_session)
    downloaded_weeks[(year, week)] = data
    for h in data.homeworks:
        homework_year, homework_week = week_of(h.lesson_date)

        if h.lesson_date in downloaded_days:
            data_for_day = downloaded_days[h.lesson_date]
        elif (homework_year, homework_week) in downloaded_weeks:
            data_for_day = await get_data_for_day(h.lesson_date, token, http_session, downloaded_weeks[(homework_year, homework_week)])
            downloaded_days[h.lesson_date] = data_for_day
        else:
            data_for_week = await get_data_for_week(homework_year, homework_week, token, http_session)
            downloaded_weeks[(homework_year, homework_week)] = data_for_week
            data_for_day = await get_data_for_day(h.lesson_date, token, http_session, data_for_week)
            downloaded_days[h.lesson_date] = data_for_day

        sub = None
        for lesson in data_for_day:
            if h.id == lesson.created_homework_id:
                sub = lesson
                break
        if sub is None:
            continue
        subject_id = subject_ids.get(sub.subject)
        if subject_id is None:
            continue
        hom = homeworks.Homework(
            kiasuo_id=h.id, subject_id=subject_id, text=h.text,
            lesson_date=sub.lesson_date, check_at=h.check_at)
        uniq = (hom.subject_id, hom.lesson_date, hom.check_at, hom.text.lower(), tuple(
            sorted(f.title for f in h.files)), tuple(sorted(l.url for l in h.links)))
        if uniq in unique_homeworks:
            continue
        unique_homeworks.add(uniq)
        homs.append(hom)
        for file in h.files:
            fl = homework_files.HomeworkFile(url=file.url, title=file.title)
            hom.homework_files.append(fl)
        for link in h.links:
            lk = homework_links.HomeworkLink(url=link.url, title=link.title)
            hom.homework_links.append(lk)
    return homs


async def write_new_data(year: int, week: int, token: Token, http_session: ClientSession, db_session: AsyncSession):
    """Догружает неделю: новые ДЗ добавляет, у старых обновляет даты и текст."""
    subject_ids = await get_subject_ids_by_name(db_session)
    data_from_api = await get_homework_for_week(
        year, week, token, http_session, subject_ids)
    data_from_api_dict = {d.kiasuo_id: d for d in data_from_api}
    existing = await get_homeworks_by_kiasuo_ids(
        db_session, data_from_api_dict)
    for kiasuo_id, homework in existing.items():
        api_homework = data_from_api_dict[kiasuo_id]
        # Даты в дневнике правят задним числом, поэтому перезаписываем
        # их при каждой синхронизации недели.
        homework.lesson_date = api_homework.lesson_date
        homework.check_at = api_homework.check_at
        homework.text = api_homework.text
    homs = [hw for kiasuo_id, hw in data_from_api_dict.items()
            if kiasuo_id not in existing]
    await write_homeworks(homs, db_session, http_session)
    return homs


async def save_file(file: homework_files.HomeworkFile, session: ClientSession):

    def sanitize_filename(filename: str) -> str:
        filename = Path(filename).name

        filename = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ._-]", "_", filename)

        if filename in {"", ".", ".."}:
            raise ValueError("Invalid filename")

        return filename

    # Путь от корня проекта: синк запускают и из src, и из корня,
    # а get_attachment_path ищет вложение в files/homework/<id>.
    folder_path = HOMEWORK_DIR / str(file.id)
    folder_path.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_filename(file.title)

    file_path = (folder_path / safe_name).resolve()
    base_path = folder_path.resolve()

    if not file_path.is_relative_to(base_path):
        raise ValueError("Invalid file path")

    max_size = 20 * 1024 * 1024
    total_size = 0
    file_url = "https://diaryapi.kiasuo.ru" + file.url
    logger.debug("скачиваю вложение %s -> %s", file.title, file_path)

    try:
        async with session.get(file_url) as response:
            response.raise_for_status()

            with open(file_path, "wb") as f:
                async for chunk in response.content.iter_chunked(64*1024):
                    total_size += len(chunk)

                    if total_size > max_size:
                        raise ValueError("File is too large")

                    f.write(chunk)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    logger.info("вложение %s сохранено: %d КБ", file.title, total_size // 1024)


def default_weeks() -> list[tuple[int, int]]:
    """Недели по умолчанию: прошлая, текущая и следующая."""
    today = date.today()
    return [week_of(today + timedelta(weeks=offset))
            for offset in (-1, 0, 1)]


async def sync_weeks(weeks: list[tuple[int, int]] | None = None, prepare: bool = True) -> list[homeworks.Homework]:
    """Догружает недели дневника и готовит задачи для новых ДЗ.

    `weeks` — список (год, номер недели). Существующие ДЗ обновляются
    (даты, текст), но заново не готовятся.
    Возвращает только новые ДЗ — их же и готовит.
    """
    weeks = weeks or default_weeks()
    names = ", ".join(f"{year}-W{week:02d}" for year, week in weeks)
    logger.info("синк недель: %s", names)
    started = time.monotonic()

    tkn = Token()
    homs = []
    async with ClientSession() as http_session:
        async with SessionLocal() as db_session:
            for year, week in weeks:
                homs.extend(await write_new_data(
                    year, week, tkn, http_session, db_session))

    logger.info("синк недель %s: новых ДЗ %d, %.1f с",
                names, len(homs), time.monotonic() - started)

    if prepare:
        for homework in homs:
            try:
                await prepare_homework(homework.id)
            except Exception:
                # Одно сломанное ДЗ не должно оставлять без задач остальные.
                # exception() пишет и трейсбек — по нему видно, где упало.
                logger.exception("ДЗ %d: подготовка не удалась", homework.id)
    return homs


if __name__ == "__main__":
    import argparse

    from logging_setup import setup_logging

    parser = argparse.ArgumentParser(description="Загрузка ДЗ из дневника")
    parser.add_argument(
        "--week", type=int, action="append",
        help="номер недели; можно повторять. По умолчанию — прошлая, текущая и следующая")
    parser.add_argument(
        "--year", type=int,
        help="ISO-год для --week (по умолчанию текущий)")
    parser.add_argument(
        "--no-prepare", action="store_true",
        help="только загрузить ДЗ, без подготовки задач через ИИ")
    args = parser.parse_args()
    if args.week:
        year = args.year or date.today().isocalendar()[0]
        weeks = [(year, week) for week in args.week]
    else:
        weeks = None
    setup_logging()
    asyncio.run(sync_weeks(weeks, prepare=not args.no_prepare))
