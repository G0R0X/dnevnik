"""Решение домашних заданий с помощью ИИ (этап 4)."""

import json
from dataclasses import dataclass, field

from ai import promts
from ai.client import aiclient
from db.crud.main import get_homework, get_homework_task, get_materials, get_subject, save_task_solution
from db.session import SessionLocal
from file_handling.main import (
    BOOKS_DIR,
    get_attachment_path,
    get_textbook_pages,
    limit_attachment_text,
    read_attachment,
)


@dataclass
class SolveContext:
    """Всё, что нужно для решения одной задачи."""

    instruction: str
    targets: list[str]
    subject_name: str
    materials: list[dict]
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


async def load_solve_context(task_id: int) -> SolveContext | None:
    """Собирает контекст решения задачи из БД и файлов."""
    async with SessionLocal() as session:
        task = await get_homework_task(session, task_id)
        if task is None:
            return None
        homework = await get_homework(session, task.homework_id)
        subject = await get_subject(session, homework.subject_id)
        files, links = await get_materials(session, task.homework_id)

        materials = [
            {"kind": "file", "title": f.title, "url": f.url} for f in files
        ] + [
            {"kind": "link", "title": link.title, "url": link.url} for link in links
        ]
        attachment_texts, attachment_images = _attachment_content(files)
        return SolveContext(
            instruction=task.instruction,
            targets=list(task.targets or []),
            subject_name=(subject.name_ru or subject.name) if subject else "",
            materials=materials,
            page_images=_page_images(
                subject.textbook_path if subject else None,
                task.textbook_pages or [],
            ),
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


async def solve_task(task_id: int, targets: list[str] | None = None) -> str:
    """Решает задачу по id. targets — решить только часть номеров этой задачи."""
    context = await load_solve_context(task_id)
    if context is None:
        return ""
    full_solve = targets is None
    if targets is None:
        targets = context.targets
        if context.solution:
            return context.solution

    prompt = build_solve_prompt(context, targets)
    if context.images:
        answer = await aiclient.generate_with_images(prompt, context.images)
    else:
        answer = await aiclient.generate(prompt)
    answer = _clean_answer(answer)

    # частичное решение не сохраняем: иначе оно затрёт ответ по всему заданию
    if full_solve and answer:
        async with SessionLocal() as session:
            await save_task_solution(session, task_id, answer)
    return answer


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(solve_task(14)))
