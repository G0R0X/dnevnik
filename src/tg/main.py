from db.crud.main import get_solvable_homework_tasks
from ai.solve import solve_task
from file_handling.main import BOOKS_DIR, get_attachment_path, get_page_count, get_textbook_pages, pdf_to_printed
from db.crud.main import get_schedule_for_day, get_homework_for_lesson, get_homework_for_day, get_homework, get_homework_task, get_subject, get_subject_names
from db.session import SessionLocal
from db.models.homework_tasks import TaskStatus
from datetime import date, timedelta

import asyncio
import html
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, FSInputFile, BufferedInputFile, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from dotenv import load_dotenv
import os
load_dotenv()
BOT_TOKEN = os.getenv("BOT_API_KEY")
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Русские названия предметов (id -> название), загружаются из БД при старте.
subject_names: dict[int, str] = {}


def subject_name(subject_id: int) -> str:
    return subject_names.get(subject_id, f"предмет {subject_id}")


async def load_subject_names() -> None:
    async with SessionLocal() as session:
        subject_names.update(await get_subject_names(session))


int_to_month = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря"
}

int_to_weekday = {
    1: "понедельник",
    2: "вторник",
    3: "среда",
    4: "четверг",
    5: "пятница",
}

# У Telegram лимит 4096 символов на сообщение, оставляем запас.
MESSAGE_LIMIT = 4000

# Последнее сообщение бота в чате (chat_id -> message_id). Экран должен быть
# именно им: иначе ниже экрана уже висит отправленный файл, и длинный ответ
# разрывается — часть 1 в экране сверху, часть 2 под файлом.
_last_message_id: dict[int, int] = {}


def _remember(message: Message) -> None:
    """Запоминает сообщение как последнее в чате."""
    _last_message_id[message.chat.id] = message.message_id


def _is_last(message: Message) -> bool:
    """Это сообщение — последнее, что бот отправил в чат?"""
    last = _last_message_id.get(message.chat.id)
    return last is None or last == message.message_id


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Режет длинный ответ на сообщения по строкам, не разрывая слова."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            cut = line.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(line[:cut])
            line = line[cut:].lstrip()
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def to_html(text: str) -> str:
    """Превращает **жирный** в <b>, остальной текст экранирует.

    Сообщения уходят с parse_mode="HTML": так модель может выделить
    изменённую часть предложения, а угловые скобки из заданий не ломают разметку.
    """
    result = []
    position = 0
    for match in _BOLD_RE.finditer(text):
        result.append(html.escape(text[position:match.start()]))
        result.append(f"<b>{html.escape(match.group(1))}</b>")
        position = match.end()
    result.append(html.escape(text[position:]))
    return "".join(result)


def split_html_message(text: str, limit: int = MESSAGE_LIMIT - 200) -> list[str]:
    """Режет ответ на сообщения и разметку не рвёт.

    Режем обычный текст по строкам, а потом размечаем каждый кусок: пара
    `**...**` всегда внутри строки, поэтому границы кусков её не разрывают.
    Запас 200 символов — на HTML-теги и экранирование.
    Если модель всё же оставила непарный маркер — кусок уходит без жирного.
    """
    chunks = []
    for chunk in split_message(text, limit):
        if chunk.count("**") % 2:
            chunks.append(html.escape(chunk))
        else:
            chunks.append(to_html(chunk))
    return chunks


def shorten_button_text(text: str, limit: int = 60) -> str:
    """Короткая подпись для кнопки: длинные инструкции не влезают в клавиатуру."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def get_first_last_weekdays(year: int, week: int) -> str:
    start = date.fromisocalendar(year, week, 1)
    end = date.fromisocalendar(year, week, 7)

    s = f"{start.day} {int_to_month[start.month]} - {end.day} {int_to_month[end.month]}"
    return s


def calculate_week(year: int, week: int, operation: int):
    current_week_day = date.fromisocalendar(year, week, 1)
    new_week_day = current_week_day + timedelta(weeks=1) * operation
    new_year, new_week, _ = new_week_day.isocalendar()
    return new_year, new_week


def calculate_day(year: int, week: int, day: int, operation: int):
    current_week_day = date.fromisocalendar(year, week, day)
    new_week_day = current_week_day + timedelta(days=1) * operation
    new_year, new_week, new_day = new_week_day.isocalendar()
    return new_year, new_week, new_day


def generate_InlineKeyboardMarkup_for_week(year: int, week: int):
    lst = [[
        InlineKeyboardButton(
            text="Понедельник",
            callback_data=f"day:{year}:{week}:1"
        )
    ],
        [
            InlineKeyboardButton(
                text="Вторник",
                callback_data=f"day:{year}:{week}:2"
            )
    ],
        [
            InlineKeyboardButton(
                text="Среда",
                callback_data=f"day:{year}:{week}:3"
            )
    ],
        [
            InlineKeyboardButton(
                text="Четверг",
                callback_data=f"day:{year}:{week}:4"
            )
    ],
        [
            InlineKeyboardButton(
                text="Пятница",
                callback_data=f"day:{year}:{week}:5"
            )
    ],]
    future_year, future_week = calculate_week(year, week, 1)
    past_year, past_week = calculate_week(year, week, -1)
    lst.append([
        InlineKeyboardButton(
            text="<-",
            callback_data=f"week:{past_year}:{past_week}"
        ),
        InlineKeyboardButton(
            text="->",
            callback_data=f"week:{future_year}:{future_week}"
        ),
    ])
    return InlineKeyboardMarkup(
        inline_keyboard=lst)


def generate_InlineKeyboardMarkup_for_day(schedule, homs_files_links, year, week, day):
    lst = []
    for n, subject_id in enumerate(schedule, 1):
        s = ''
        obj = homs_files_links.get(subject_id, None)
        if obj:
            if obj.get("text", False):
                s += '📄'
            if obj.get("files", False):
                s += '📎'
            if obj.get("links", False):
                s += '➚'
        lst.append([
            InlineKeyboardButton(
                text=(s + ' ' + subject_name(subject_id)
                      ) if s else subject_name(subject_id),
                callback_data=f"lesson:{year}:{week}:{day}:{subject_id}"
            )
        ])
    future_year, future_week, future_day = calculate_day(year, week, day, 1)
    while future_day in (6, 7):
        future_year, future_week, future_day = calculate_day(
            future_year, future_week, future_day, 1)

    past_year, past_week, past_day = calculate_day(year, week, day, -1)
    while past_day in (6, 7):
        past_year, past_week, past_day = calculate_day(
            past_year, past_week, past_day, -1)

    lst.append(
        [
            InlineKeyboardButton(
                text="<-",
                callback_data=f"day:{past_year}:{past_week}:{past_day}"
            ),
            InlineKeyboardButton(
                text="->",
                callback_data=f"day:{future_year}:{future_week}:{future_day}"
            ),
        ]
    )
    lst.append(
        [
            InlineKeyboardButton(
                text="<---",
                callback_data=f"week:{year}:{week}"
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=lst)


def generate_InlineKeyboardMarkup_for_lesson(files, links, year, week, day, subject_id, tasks):
    lst = []
    for file in files:
        f = file[0]
        lst.append([
            InlineKeyboardButton(
                text=f.title,
                callback_data=f"file:{f.id}")
        ])
    for link in links:
        l = link[0]
        lst.append([
            InlineKeyboardButton(
                text=l.title,
                url=l.url
            )
        ])
    for task in tasks:
        lst.append([
            InlineKeyboardButton(
                text="📝 " + shorten_button_text(task.instruction),
                callback_data=f"task:{year}:{week}:{day}:{subject_id}:{task.id}")
        ])
    lst.append(
        [
            InlineKeyboardButton(
                text="<---",
                callback_data=f"day:{year}:{week}:{day}"
            )
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=lst)


def generate_InlineKeyboardMarkup_for_card(year, week, day, subject_id, task, textbook_path=None):
    lst = []
    if task.status == TaskStatus.READY:
        lst.append([
            InlineKeyboardButton(
                text="🧠 Решить с ИИ",
                callback_data=f"ai:{year}:{week}:{day}:{subject_id}:{task.homework_id}:{task.id}")
        ])
    if task.textbook_pages:
        pages = list(task.textbook_pages)
        prefix = f"pg:{year}:{week}:{day}:{subject_id}:{task.id}"
        lst.append([
            InlineKeyboardButton(
                text=f"📖 Страницы учебника ({len(pages)})",
                callback_data=f"{prefix}:{pages[0]}")
        ])
        # Отдельная кнопка на каждую страницу задания: в английском задание
        # ссылается и на упражнения, и на словарь далеко в конце книги.
        row = []
        for page in pages:
            label = pdf_to_printed(textbook_path, page) if textbook_path else page
            row.append(InlineKeyboardButton(
                text=str(label), callback_data=f"{prefix}:{page}"))
            if len(row) == 4:
                lst.append(row)
                row = []
        if row:
            lst.append(row)
    lst.append(
        [
            InlineKeyboardButton(
                text="<---",
                callback_data=f"lesson:{year}:{week}:{day}:{subject_id}"
            )
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=lst)


def generate_InlineKeyboardMarkup_for_page(year, week, day, subject_id, task_id, page, total):
    lst = []
    arrows = []
    if page > 1:
        arrows.append(InlineKeyboardButton(
            text="<-",
            callback_data=f"pg:{year}:{week}:{day}:{subject_id}:{task_id}:{page - 1}"))
    if page < total:
        arrows.append(InlineKeyboardButton(
            text="->",
            callback_data=f"pg:{year}:{week}:{day}:{subject_id}:{task_id}:{page + 1}"))
    if arrows:
        lst.append(arrows)
    lst.append([
        InlineKeyboardButton(
            text="<---",
            callback_data=f"task:{year}:{week}:{day}:{subject_id}:{task_id}")
    ])
    return InlineKeyboardMarkup(
        inline_keyboard=lst)


def generate_InlineKeyboardMarkup_for_answer(year, week, day, subject_id, task_id):
    lst = []
    lst.append(
        [
            InlineKeyboardButton(
                text="<---",
                callback_data=f"task:{year}:{week}:{day}:{subject_id}:{task_id}"
            )
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=lst)


async def _show_text(message: Message, text: str, markup=None,
                     rendered: bool = False) -> Message:
    """Показывает текстовый экран, возвращает сообщение с экраном.

    Telegram не умеет превращать сообщение с фото в текст, поэтому если
    текущий экран — картинка, её приходится заменить (удалить и отправить).
    `rendered=True` — текст уже размечен под parse_mode=HTML.

    Если ниже экрана бот уже что-то отправил (файл вложения), экран
    переносится вниз: иначе следующий за ним ответ окажется «выше» файла.
    """
    if not rendered:
        text = to_html(text)
    if _is_last(message) and not (message.photo or message.document or message.video):
        try:
            await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                raise
        _remember(message)
        return message
    await message.delete()
    sent = await message.answer(text, reply_markup=markup, parse_mode="HTML")
    _remember(sent)
    return sent


async def _show_photo(message: Message, image: bytes, filename: str,
                      caption: str, markup=None) -> Message:
    """Показывает страницу учебника, возвращает сообщение с экраном.

    `edit_media` работает только если в сообщении уже фото и оно последнее.
    Из текстовой карточки страницу показать можно только заменой сообщения.
    """
    photo = BufferedInputFile(image, filename=filename)
    if message.photo and _is_last(message):
        await message.edit_media(
            media=InputMediaPhoto(media=photo, caption=caption),
            reply_markup=markup,
        )
        _remember(message)
        return message
    await message.delete()
    sent = await message.answer_photo(
        photo, caption=caption, reply_markup=markup)
    _remember(sent)
    return sent


def week_screen(year: int, week: int) -> tuple[str, InlineKeyboardMarkup]:
    return (get_first_last_weekdays(year, week),
            generate_InlineKeyboardMarkup_for_week(year, week))


def day_screen(schedule, homs_files_links, year: int, week: int,
               day: int) -> tuple[str, InlineKeyboardMarkup]:
    keyboard_markup = generate_InlineKeyboardMarkup_for_day(
        schedule, homs_files_links, year, week, day)
    day_date = date.fromisocalendar(year, week, day)
    text = f"{day_date.day} {int_to_month[day_date.month]} {int_to_weekday[day]}"
    return text, keyboard_markup


@dp.message(Command("start"))
async def start(message: Message):
    year, week, _ = message.date.isocalendar()
    text, markup = week_screen(year, week)
    sent = await message.answer(to_html(text), reply_markup=markup, parse_mode="HTML")
    _remember(sent)


@dp.callback_query(F.data.startswith("week:"))
async def week(callback: CallbackQuery):
    _, year, week = callback.data.split(":")
    year = int(year)
    week = int(week)
    text, markup = week_screen(year, week)
    await _show_text(callback.message, text, markup)
    await callback.answer()


@dp.callback_query(F.data.startswith("day:"))
async def day(callback: CallbackQuery):
    _, year, week, day = callback.data.split(":")
    year = int(year)
    week = int(week)
    day = int(day)

    async with SessionLocal() as session:
        schedule = await get_schedule_for_day(session, day)
        homs_files_links = await get_homework_for_day(
            session, date.fromisocalendar(year, week, day))
    text, markup = day_screen(schedule, homs_files_links, year, week, day)
    await _show_text(callback.message, text, markup)
    await callback.answer()


@dp.callback_query(F.data.startswith("file:"))
async def read_file(callback: CallbackQuery):
    _, file_id = callback.data.split(":")
    file_path = get_attachment_path(int(file_id))
    if file_path is None:
        sent = await callback.message.answer("Файл не найден.")
    else:
        sent = await callback.message.answer_document(FSInputFile(str(file_path)))
    _remember(sent)
    await callback.answer()


@dp.callback_query(F.data.startswith("lesson:"))
async def lesson(callback: CallbackQuery):
    _, year, week, day, subject_id = callback.data.split(":")
    year = int(year)
    week = int(week)
    day = int(day)
    subject_id = int(subject_id)
    async with SessionLocal() as session:
        homeworks_list, files, links, _ = await get_homework_for_lesson(session, date.fromisocalendar(year, week, day), subject_id)
        tasks = []
        for homework_id, *_ in homeworks_list:
            tasks.extend(await get_solvable_homework_tasks(session, homework_id))
    answer = []
    day_date = date.fromisocalendar(year, week, day)
    for _homework_id, homework_text, check_at, _lesson_date in homeworks_list:
        if homework_text.strip():
            answer.append(homework_text)
        if check_at and check_at != day_date:
            # У задания может быть другой срок сдачи, чем день урока.
            answer.append(
                f"Сдать к {check_at.day} {int_to_month[check_at.month]}")
    if not answer:
        answer = ["Нет задания"]
    keyboard = generate_InlineKeyboardMarkup_for_lesson(
        files, links, year, week, day, subject_id, tasks)
    s = f"{day_date.day} {int_to_month[day_date.month]} {int_to_weekday[day]} {subject_name(subject_id)}\n\n"
    await _show_text(callback.message, s + '\n'.join(answer), keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("task:"))
async def show_task(callback: CallbackQuery):
    _, year, week, day, subject_id, task_id = callback.data.split(":")
    year = int(year)
    week = int(week)
    day = int(day)
    subject_id = int(subject_id)
    task_id = int(task_id)

    async with SessionLocal() as session:
        task = await get_homework_task(session, task_id)
        if task is None:
            await callback.answer("Задание не найдено", show_alert=True)
            return
        homework = await get_homework(session, task.homework_id)
        subject = await get_subject(session, homework.subject_id)

    text = task.instruction
    if task.targets:
        text += f"\n\nНомера: {', '.join(task.targets)}"
    if task.textbook_pages and subject and subject.textbook_path:
        printed = [pdf_to_printed(subject.textbook_path, page)
                   for page in task.textbook_pages]
        text += f"\n\nСтраницы: {', '.join(str(page) for page in printed)}"
    if not task.textbook_pages and task.status == TaskStatus.NOT_SOLVABLE:
        text += "\n\nЭто задание нельзя выполнить текстом."

    keyboard = generate_InlineKeyboardMarkup_for_card(
        year, week, day, subject_id, task,
        subject.textbook_path if subject else None)
    await _show_text(callback.message, text, keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("pg:"))
async def show_page(callback: CallbackQuery):
    _, year, week, day, subject_id, task_id, page = callback.data.split(":")
    year = int(year)
    week = int(week)
    day = int(day)
    subject_id = int(subject_id)
    task_id = int(task_id)
    page = int(page)

    pdf_path, textbook_path, task_pages, total = await _task_book(task_id)
    if not task_pages or not total:
        await callback.answer("У этого задания нет страниц учебника", show_alert=True)
        return

    page = max(1, min(page, total))
    image = get_textbook_pages(pdf_path, [page]).get(page)
    if image is None:
        await callback.answer("Не удалось отрисовать страницу", show_alert=True)
        return

    # Нумерация в подписи — как напечатано на скане, а не PDF-страница.
    printed = pdf_to_printed(textbook_path, page)
    printed_total = pdf_to_printed(textbook_path, total)
    caption = f"Стр. {printed} из {printed_total}"
    if page in task_pages:
        caption += " · страница задания"
    keyboard = generate_InlineKeyboardMarkup_for_page(
        year, week, day, subject_id, task_id, page, total)
    await _show_photo(callback.message, image, f"page_{page}.png", caption, keyboard)
    await callback.answer()


async def _task_book(task_id: int) -> tuple[str | None, str | None, list[int], int]:
    """Путь к PDF, путь учебника (для печатных номеров), страницы задания, всего страниц."""
    async with SessionLocal() as session:
        task = await get_homework_task(session, task_id)
        if task is None or not task.textbook_pages:
            return None, None, [], 0
        homework = await get_homework(session, task.homework_id)
        subject = await get_subject(session, homework.subject_id)
        if subject is None or not subject.textbook_path:
            return None, None, [], 0
        pdf_path = str(BOOKS_DIR / f"{subject.textbook_path.strip('/')}.pdf")
        if not os.path.exists(pdf_path):
            return None, None, [], 0
        return (pdf_path, subject.textbook_path,
                list(task.textbook_pages), get_page_count(pdf_path))


@dp.callback_query(F.data.startswith("ai:"))
async def show_answer(callback: CallbackQuery):
    _, year, week, day, subject_id, homework_id, task_id = callback.data.split(
        ":")
    year = int(year)
    week = int(week)
    day = int(day)
    subject_id = int(subject_id)
    homework_id = int(homework_id)
    task_id = int(task_id)

    # закрываем «часики» сразу: решение может занять минуту
    await callback.answer()
    # Если ниже экрана уже лежит файл вложения, экран переносится вниз:
    # иначе ответ разорвётся на «часть 1 → файл → часть 2».
    message = await _show_text(callback.message, "Решаю задание…")

    keyboard = generate_InlineKeyboardMarkup_for_answer(
        year, week, day, subject_id, task_id)
    try:
        answer = await solve_task(task_id)
    except Exception as error:
        # Обрыв сети, лимит токенов, кривой ответ модели: без перехвата
        # экран навсегда остаётся с «Решаю задание…».
        print(f"задание {task_id}: решить не удалось: "
              f"{type(error).__name__}: {error}", flush=True)
        await _show_text(message, "Не получилось решить задание. "
                                  "Попробуй ещё раз.", keyboard)
        return

    if not answer.strip():
        answer = "Не удалось решить задание."

    day_date = date.fromisocalendar(year, week, day)
    header = f"{day_date.day} {int_to_month[day_date.month]} {int_to_weekday[day]} {subject_name(subject_id)}\n\n"

    chunks = split_html_message(header + answer)
    # Первую часть показываем в том же сообщении, остальные — новыми:
    # длинный ответ в одно сообщение Telegram не влезает.
    message = await _show_text(
        message, chunks[0],
        keyboard if len(chunks) == 1 else None, rendered=True)
    rest = chunks[1:]
    for number, chunk in enumerate(rest):
        last = number == len(rest) - 1
        message = await message.answer(
            chunk, reply_markup=keyboard if last else None, parse_mode="HTML")
        _remember(message)


async def main():
    # Названия предметов кэшируем один раз: экраны рисуются из памяти,
    # без запроса в БД на каждое нажатие.
    await load_subject_names()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
