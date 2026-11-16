"""Решение домашних заданий с помощью ИИ (этап 4)."""

import json
import logging
import re
import time
from dataclasses import dataclass, field

from ai import promts
from ai.client import aiclient
from ai.page_text import get_pages_text
from db.crud.main import get_homework, get_homework_task, get_materials, get_subject, save_task_solution
from db.models.homework_tasks import TaskStatus
from db.session import SessionLocal
from file_handling.main import (
    BOOKS_DIR,
    get_attachment_path,
    get_textbook_pages,
    limit_attachment_text,
    read_attachment,
)

logger = logging.getLogger(__name__)


def _model_name() -> str:
    """Модель, которой реально уходит запрос: в логе видно, кто ответил."""
    return getattr(aiclient, "model", "?")


def _usage_note() -> str:
    """Цена последнего вызова ИИ, если провайдер её вернул."""
    usage = getattr(aiclient, "last_usage", None) or {}
    cost = usage.get("cost")
    if not isinstance(cost, (int, float)):
        return ""
    return f", {cost:.3f} ₽"


@dataclass
class SolveContext:
    """Всё, что нужно для решения одной задачи."""

    instruction: str
    targets: list[str]
    subject_name: str
    materials: list[dict]
    status: TaskStatus | None = None
    textbook_path: str | None = None
    textbook_paragraphs: list[int] = field(default_factory=list)
    textbook_pages: list[int] = field(default_factory=list)
    page_images: dict[str, bytes] = field(default_factory=dict)
    attachment_images: dict[str, bytes] = field(default_factory=dict)
    attachment_texts: list[dict] = field(default_factory=list)
    solution: str | None = None

    @property
    def images(self) -> dict[str, bytes]:
        return {**self.page_images, **self.attachment_images}


def build_solve_prompt(context: SolveContext, targets: list[str]) -> list[dict]:
    """Собирает промпт: каркас + блоки под то, что реально приложено."""
    requirements = promts.SOLVE_REQUIREMENTS
    if len(targets) > 1:
        requirements += promts.SOLVE_MULTI_TARGET_REQUIREMENT

    system = promts.SOLVE_TASK_PROMT.format(
        pages_block=promts.SOLVE_PAGES_BLOCK if context.page_images else "",
        attachments_block=(
            promts.SOLVE_ATTACHMENTS_BLOCK
            if context.attachment_images or context.attachment_texts else ""),
        requirements=requirements,
    )
    user_data = {
        "subject": context.subject_name,
        "instruction": context.instruction,
        "targets": targets,
        "materials": context.materials,
    }
    if context.attachment_texts:
        user_data["attachments"] = context.attachment_texts
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
    ]


def build_retell_prompt(context: SolveContext, text: str) -> list[dict]:
    """Промпт пересказа: модель получает текст страниц, а не картинки."""
    user_data = {
        "subject": context.subject_name,
        "instruction": context.instruction,
        "paragraphs": context.textbook_paragraphs,
        "text": text,
    }
    return [
        {"role": "system", "content": promts.RETELL_PARAGRAPH_PROMT},
        {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
    ]


async def load_solve_context(task_id: int) -> SolveContext | None:
    """Собирает контекст решения задачи из БД и файлов."""
    async with SessionLocal() as session:
        task = await get_homework_task(session, task_id)
        if task is None:
            return None
        homework = await get_homework(session, task.homework_id)
        subject = await get_subject(session, homework.subject_id)
        textbook_path = subject.textbook_path if subject else None
        is_retell = task.status == TaskStatus.RETELL

        # Для пересказа не нужны ни вложения, ни картинки страниц: текст
        # параграфа читается отдельно, а PNG-рендер только тратил бы время.
        files, links = ([], []) if is_retell else await get_materials(
            session, task.homework_id)
        materials = [
            {"kind": "file", "title": f.title, "url": f.url} for f in files
        ] + [
            {"kind": "link", "title": link.title, "url": link.url} for link in links
        ]
        attachment_texts, attachment_images = (
            ([], {}) if is_retell else _attachment_content(files))
        page_images = {} if is_retell else _page_images(
            textbook_path, task.textbook_pages or [])

        return SolveContext(
            instruction=task.instruction,
            targets=list(task.targets or []),
            subject_name=(subject.name_ru or subject.name) if subject else "",
            materials=materials,
            status=task.status,
            textbook_path=textbook_path,
            textbook_paragraphs=list(task.textbook_paragraphs or []),
            textbook_pages=list(task.textbook_pages or []),
            page_images=page_images,
            attachment_images=attachment_images,
            attachment_texts=attachment_texts,
            solution=task.solution,
        )


def _page_images(textbook_path: str | None, pages: list[int]) -> dict[str, bytes]:
    if not textbook_path or not pages:
        return {}
    pdf_path = str(BOOKS_DIR / f"{textbook_path.strip('/')}.pdf")
    return {
        f"page_{page}.png": data
        for page, data in get_textbook_pages(pdf_path, pages).items()
    }


def _attachment_content(files) -> tuple[list[dict], dict[str, bytes]]:
    """Читает вложения: тексты документов и картинки."""
    texts: list[dict] = []
    images: dict[str, bytes] = {}
    for file in files:
        path = get_attachment_path(file.id)
        if path is None:
            continue
        text, file_images = read_attachment(path)
        if text.strip():
            texts.append({
                "title": file.title,
                "text": limit_attachment_text(text),
            })
        images.update(file_images)
    return texts, images


def _clean_answer(text: str) -> str:
    """Страховка: убирает решётки-заголовки, но сохраняет **жирный**."""
    return "\n".join(
        line.lstrip("#").strip() if line.startswith("#") else line
        for line in text.split("\n")
    ).strip()


# Модель иногда выводит план, даже когда промпт его не просит.
_PLAN_HEADER = re.compile(r"^[#*_\s]*план[#*_\s]*:?[#*_\s]*$", re.IGNORECASE)
_RETELL_HEADER = re.compile(r"^[#*_\s]*пересказ[#*_\s]*:?[#*_\s]*$", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^\s*(\d{1,2}[.)]|[-•*–—])\s+")


def _strip_plan(text: str) -> str:
    """Убирает план из начала ответа: он нужен модели, а не ученику.

    Если модель всё же написала «**План**», режем до заголовка «Пересказ»,
    а когда заголовка нет — до конца списка пунктов.
    """
    lines = text.split("\n")
    start = next(
        (index for index, line in enumerate(lines[:6])
         if line.strip() and _PLAN_HEADER.match(line.strip())),
        None,
    )
    if start is None:
        return text

    for index in range(start + 1, len(lines)):
        if _RETELL_HEADER.match(lines[index].strip()):
            rest = "\n".join(lines[index + 1:]).strip()
            # Если после плана ничего нет — лучше отдать как есть, чем пустоту.
            return rest or text

    index = start + 1
    while index < len(lines) and (not lines[index].strip()
                                  or _LIST_ITEM.match(lines[index])):
        index += 1
    rest = "\n".join(lines[index:]).strip()
    return rest or text


async def _solve_retell(task_id: int, context: SolveContext) -> str:
    """Читает текст параграфа и готовит по нему пересказ."""
    if not context.textbook_path or not context.textbook_pages:
        logger.warning("задача %d: пересказ без страниц учебника", task_id)
        return ""

    paragraphs = ", ".join(f"§{p}" for p in context.textbook_paragraphs) or "параграф"
    started = time.perf_counter()
    text = await get_pages_text(context.textbook_path, context.textbook_pages)
    text_seconds = time.perf_counter() - started
    if not text.strip():
        logger.warning("задача %d: не удалось получить текст параграфа", task_id)
        return ""

    logger.info("ИИ: пересказ задачи %d (%s, %s), модель %s: текст %d страниц за %.1f с",
                task_id, context.subject_name, paragraphs, _model_name(),
                len(context.textbook_pages), text_seconds)
    started = time.perf_counter()
    answer = await aiclient.generate(build_retell_prompt(context, text))
    seconds = time.perf_counter() - started
    answer = _strip_plan(_clean_answer(answer))
    if answer:
        async with SessionLocal() as session:
            await save_task_solution(session, task_id, answer)
        logger.info("ИИ: пересказ задачи %d готов за %.1f с, символов %d%s",
                    task_id, seconds, len(answer), _usage_note())
    return answer


async def solve_task(task_id: int, targets: list[str] | None = None) -> str:
    """Решает задачу по id. targets — решить только часть номеров этой задачи."""
    context = await load_solve_context(task_id)
    if context is None:
        return ""
    full_solve = targets is None
    if targets is None:
        targets = context.targets
        if context.solution:
            logger.info("задача %d: ответ уже есть в БД, ИИ не вызывается", task_id)
            return context.solution

    if context.status == TaskStatus.RETELL:
        return await _solve_retell(task_id, context)

    prompt = build_solve_prompt(context, targets)
    logger.info("ИИ: решение задачи %d (%s), модель %s, номеров %d%s",
                task_id, context.subject_name, _model_name(), len(targets),
                ", с картинками" if context.images else "")
    started = time.perf_counter()
    if context.images:
        answer = await aiclient.generate_with_images(prompt, context.images)
    else:
        answer = await aiclient.generate(prompt)
    seconds = time.perf_counter() - started
    answer = _clean_answer(answer)
    logger.info("ИИ: решение задачи %d готово за %.1f с, символов %d%s",
                task_id, seconds, len(answer), _usage_note())

    # частичное решение не сохраняем: иначе оно затрёт ответ по всему заданию
    if full_solve and answer:
        async with SessionLocal() as session:
            await save_task_solution(session, task_id, answer)
    return answer


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(solve_task(14)))
