import json
import re

from db.models.homework_tasks import HomeworkTask, TaskStatus
from db.crud.main import (
    get_homework,
    get_homework_tasks,
    get_materials,
    get_subject,
    rewrite_homework_tasks,
)
from db.session import SessionLocal
from ai.client import AIClient, aiclient, search_client
from ai import promts
from ai.utils import extract_json
from file_handling.main import (
    BOOKS_DIR,
    find_pages_by_targets,
    get_page_unit_from_index,
    get_pages_for_paragraphs,
    get_textbook_pages,
    load_structure,
    normalize_target,
    printed_to_pdf,
)


def build_analyse_prompt(homework_text: str, subject_name: str, materials: list[dict]) -> list[dict]:
    input_data = {
        "homework_text": homework_text,
        "subject": subject_name,
        "materials": materials,
    }
    return [
        {"role": "system", "content": promts.ANALYSE_HOMEWORK_PROMT},
        {"role": "user", "content": json.dumps(
            input_data, ensure_ascii=False)},
    ]


async def analyse(homework_text: str, subject_name: str, materials: list[dict]) -> list[dict]:
    """Вызов ИИ №1 — разбиение ДЗ на задачи."""
    prompt = build_analyse_prompt(homework_text, subject_name, materials)
    for _ in range(3):
        try:
            response = await aiclient.generate(prompt)
            data = extract_json(response)
            return data.get("tasks", [])
        except Exception as e:
            print(f"{homework_text} {subject_name} error {type(e).__name__}: {e}")
    return []


def _normalize(s: str) -> str:
    """Приводит элемент к сравниваемому виду: строчные, без «№»/«#» и лишних пробелов."""
    return normalize_target(s)


def _clean_target(target: str) -> str:
    """Убирает ведущий «№»/«#»: модель может вернуть «№43.4» вместо «43.4»."""
    text = str(target).strip()
    while text[:1] in ("№", "#"):
        text = text[1:].lstrip()
    return text


def _paragraphs_from_targets(targets: list[str]) -> list[int]:
    """Для задачников с номерами вида «N.M» извлекает номера параграфов N."""
    result: list[int] = []
    for t in targets:
        m = re.match(r"^\s*[№#]?\s*(\d{1,3})\s*\.\s*\d", t)
        if m:
            num = int(m.group(1))
            if num not in result:
                result.append(num)
    return result


def _unit_from_text(*texts: str) -> str | None:
    """Ищет «Unit 2» / «Unit Two» / «юнит 2» в тексте задания.

    В английском учебнике нумерация заданий начинается заново в каждом
    Unit'е, поэтому без номера юнита индекс страниц неоднозначен.
    """
    for text in texts:
        match = re.search(r"\b(?:unit|юнит)\s+([A-Za-zА-Яа-я]+|\d+)", text or "",
                          re.IGNORECASE)
        if match:
            return match.group(0)
    return None


async def find_targets_on_page(client: AIClient, instruction: str, targets: list[str],
                               textbook_path: str, page: int) -> list[str]:
    """Вызов ИИ №2 — по одной странице определяет, какие из targets на ней есть."""
    pdf_path = str(BOOKS_DIR / f"{textbook_path.strip('/')}.pdf")
    images = get_textbook_pages(pdf_path, [page])
    user_data = {
        "instruction": instruction,
        "targets": targets,
    }
    prompt = [
        {"role": "system", "content": promts.FIND_TARGETS_ON_PAGE_PROMT},
        {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
    ]
    for _ in range(3):
        try:
            response = await client.generate_with_images(
                prompt, {f"page_{page}.png": images[page]})
            data = extract_json(response)
            found_raw = data.get("found", [])
            found_norm = {_normalize(x) for x in found_raw}
            return [t for t in targets if _normalize(t) in found_norm]
        except Exception as e:
            print(f"{instruction} {targets} error {type(e).__name__}: {e}")


async def _search_pages(client: AIClient, instruction: str, targets: list[str],
                        textbook_path: str, pages: list[int]) -> tuple[list[int], set[str]]:
    """Проходит страницы с конца, возвращает найденные страницы и совпавшие targets."""
    found_pages: list[int] = []
    found_norm: set[str] = set()
    target_norm = {_normalize(t) for t in targets}

    for page in sorted(pages, reverse=True):
        try:
            page_found = await find_targets_on_page(
                client, instruction, targets, textbook_path, page)
        except Exception:
            page_found = []
        if page_found:
            found_pages.append(page)
            found_norm.update(_normalize(x) for x in page_found)
            if target_norm <= found_norm:
                break
    return found_pages, found_norm


async def search_task_pages(instruction: str, targets: list[str], textbook_path: str,
                            paragraphs: list[int], unit: str | None = None) -> list[int]:
    """Ищет страницы с номерами: сначала готовый индекс, потом ИИ.

    Индекс (`scan.py`) отвечает мгновенно и бесплатно, поэтому им проверяем
    в первую очередь. Если индекса нет или он нашёл не всё — идём по страницам
    параграфа бесплатным GigaChat, при промахе — GLM. Если не помогла
    и вторая модель — возвращаем весь диапазон параграфа.
    """
    total_pages = _total_pages(textbook_path)

    index_pages, missing = ([], [str(t) for t in targets])
    if targets:
        index_pages, missing = find_pages_by_targets(textbook_path, targets, unit)
        if index_pages and not missing:
            return _add_continuation_pages(index_pages, total_pages)

    paragraph_pages = get_pages_for_paragraphs(textbook_path, paragraphs)
    if not paragraph_pages:
        # индекс нашёл хоть что-то, а сузить больше нечем
        return _add_continuation_pages(index_pages, total_pages) if index_pages else []
    if not targets:
        return paragraph_pages

    found_pages, found_norm = await _search_pages(
        search_client, instruction, targets, textbook_path, paragraph_pages)

    remaining = [t for t in targets if _normalize(t) not in found_norm]
    if remaining and search_client is not aiclient:
        # GigaChat не нашёл всё — перепроверяем те же страницы моделью посильнее
        extra_pages, extra_norm = await _search_pages(
            aiclient, instruction, remaining, textbook_path, paragraph_pages)
        found_pages += extra_pages
        found_norm |= extra_norm

    if all(_normalize(t) in found_norm for t in targets):
        return _add_continuation_pages(found_pages, total_pages)
    return paragraph_pages


def _total_pages(textbook_path: str) -> int:
    structure = load_structure(textbook_path)
    return int(structure["total_pages"]) if structure else 0


def _add_continuation_pages(found_pages: list[int], last_page: int) -> list[int]:
    """Добавляет следующую страницу: задание может обрываться и продолжаться.

    Номер задания печатают только один раз — на странице, где оно начинается,
    поэтому поиск по номерам не увидит продолжение. Берём страницу следом.
    """
    pages = set(found_pages)
    for page in found_pages:
        if page + 1 <= last_page:
            pages.add(page + 1)
    return sorted(pages)


async def prepare_homework(homework_id: int, force: bool = False) -> list[HomeworkTask]:
    """Полный пайплайн подготовки одного домашнего задания.

    Если задачи у ДЗ уже есть — возвращает их и ничего не делает: повторный
    разбор тратил бы ИИ и перезаписывал сохранённые решения. `force=True`
    перечитывает ДЗ заново (нужно, если страницы были определены неверно).

    Если разбор не дал ни одной задачи (ИИ не ответил за три попытки или
    вернул мусор), БД не трогаем и отдаём то, что там уже лежит: иначе
    прогон с `force=True` стёр бы старые задачи, не сохранив новых.
    """
    # Сессию не держим, пока работает ИИ (это минуты): вычитываем всё нужное
    # и закрываем, а на сохранение открываем новую.
    async with SessionLocal() as session:
        homework = await get_homework(session, homework_id)
        if homework is None:
            return []
        existing = await get_homework_tasks(session, homework_id)
        if existing and not force:
            print(f"ДЗ {homework_id}: задачи уже есть, разбор пропущен", flush=True)
            return existing
        subject = await get_subject(session, homework.subject_id)
        files, links = await get_materials(session, homework_id)

        homework_text = homework.text
        subject_name = (subject.name_ru or subject.name) if subject else ""
        # Дальше работаем с простыми значениями: ORM-объекты вне сессии
        # трогать нельзя.
        textbook_path = subject.textbook_path if subject else None
        has_files = bool(files)

        materials = []
        for f in files:
            materials.append({"kind": "file", "title": f.title, "url": f.url})
        for link in links:
            materials.append(
                {"kind": "link", "title": link.title, "url": link.url})

    if not homework_text.strip():
        return []

    tasks_data = await analyse(homework_text, subject_name, materials)

    result_tasks: list[HomeworkTask] = []
    for item in tasks_data:
        result = await _resolve_task(item, textbook_path, has_files)
        if result is not None:
            result_tasks.append(result)

    if not result_tasks:
        print(f"ДЗ {homework_id}: разбор не дал задач, оставляю как было",
              flush=True)
        return existing

    async with SessionLocal() as session:
        await rewrite_homework_tasks(session, homework_id, result_tasks,
                                     force=force)
    return result_tasks


def _parse_page_numbers(raw: list) -> list[int]:
    """Достаёт номера страниц даже из «238-239» или «стр. 60»."""
    pages: list[int] = []
    for value in raw:
        pages.extend(int(number) for number in re.findall(r"\d+", str(value)))
    return pages


def _pages_to_pdf(pages: list[int], textbook_path: str | None) -> list[int]:
    """Страницы из текста ДЗ — напечатанные номера, а рендер ждёт PDF-страницы."""
    if not pages or not textbook_path:
        return pages
    return [printed_to_pdf(textbook_path, page) for page in pages]


async def _resolve_task(item: dict, textbook_path: str | None, has_files: bool = False) -> HomeworkTask | None:
    instruction = (item.get("instruction") or "").strip()
    if not instruction:
        return None
    status = (item.get("status") or "").strip().lower()
    pages = _pages_to_pdf(
        _parse_page_numbers(item.get("textbook_pages") or []), textbook_path)
    raw_paragraphs = item.get("textbook_paragraphs") or []
    paragraphs = [int(p) for p in raw_paragraphs if str(p).strip().isdigit()]
    unit = _unit_from_text(instruction, *(str(p) for p in raw_paragraphs))
    if unit is None and pages and textbook_path:
        # Страницы из текста ДЗ могут быть словарём/приложением: заголовок
        # Unit'а на них подскажет, в каком Unit'е искать номера заданий.
        unit = get_page_unit_from_index(textbook_path, pages)
    targets = [t for t in (_clean_target(x)
                           for x in (item.get("targets") or [])) if t]
    if not paragraphs and targets:
        paragraphs = _paragraphs_from_targets(targets)

    if status in ("ready", "needs_materials") and targets and textbook_path:
        # Индекс знает, где номера напечатаны, — он точнее страниц из текста
        # ДЗ (там могут быть страницы словаря или приложения).
        found, missing = find_pages_by_targets(
            textbook_path, targets, unit)
        if found and not missing:
            # Страницы из текста ДЗ не выбрасываем: в английском задание
            # «Упр.22-27. Стр.238-239» ссылается и на упражнения, и на
            # словарь, который как раз на 238–239.
            pages = sorted(
                set(pages)
                | set(_add_continuation_pages(
                    found, _total_pages(textbook_path))))

    if status == "ready":
        return HomeworkTask(
            instruction=instruction,
            status=TaskStatus.READY,
            textbook_paragraphs=paragraphs or None,
            textbook_pages=pages or None,
            targets=targets or None,
        )

    if status in ("not_solvable", "needs_clarification"):
        return HomeworkTask(
            instruction=instruction,
            status=TaskStatus.NOT_SOLVABLE,
            textbook_paragraphs=paragraphs or None,
            textbook_pages=None,
            targets=targets or None,
        )

    # needs_materials — пробуем обеспечить учебником или приложенным файлом
    if status == "needs_materials":
        if pages:
            return HomeworkTask(
                instruction=instruction,
                status=TaskStatus.READY,
                textbook_paragraphs=paragraphs or None,
                textbook_pages=sorted(set(pages)) or None,
                targets=targets or None,
            )
        if paragraphs and textbook_path:
            try:
                final_pages = await search_task_pages(
                    instruction, targets, textbook_path, paragraphs, unit)
            except Exception:
                final_pages = []
            if final_pages:
                return HomeworkTask(
                    instruction=instruction,
                    status=TaskStatus.READY,
                    textbook_paragraphs=paragraphs,
                    textbook_pages=sorted(set(final_pages)) or None,
                    targets=targets or None,
                )
        if has_files:
            # материал — приложенный файл, его содержимое прочитает solve.py
            return HomeworkTask(
                instruction=instruction,
                status=TaskStatus.READY,
                textbook_paragraphs=paragraphs or None,
                textbook_pages=None,
                targets=targets or None,
            )
        # ни учебника, ни файлов — решить не можем
        return HomeworkTask(
            instruction=instruction,
            status=TaskStatus.NOT_SOLVABLE,
            textbook_paragraphs=paragraphs or None,
            textbook_pages=None,
            targets=targets or None,
        )

    # неразобранные статусы — в not_solvable
    return HomeworkTask(
        instruction=instruction,
        status=TaskStatus.NOT_SOLVABLE,
        targets=targets or None,
    )


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Разбор одного ДЗ на задачи")
    parser.add_argument("homework_id", type=int, help="id ДЗ в БД")
    parser.add_argument(
        "--force", action="store_true",
        help="перечитать ДЗ даже если задачи уже есть")
    args = parser.parse_args()
    asyncio.run(prepare_homework(args.homework_id, force=args.force))
