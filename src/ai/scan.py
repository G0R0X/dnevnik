"""Скан учебника: выписываем задания со всех страниц через GigaChat.

Результат лежит рядом с PDF — `<N>.exercises.json`:

    {"textbook_path": "/algebra/1", "total_pages": 478, "models": {...},
     "scanned_at": "...", "pages": {"336": {"unit": null, "items": [
         {"block": null, "number": "42.15"}]}}}

Скан можно прерывать и продолжать: готовые страницы пропускаются.
Запуск:

    python -m ai.scan algebra/1
    python -m ai.scan --all --concurrency 2
    python -m ai.scan english/1 --from 100 --to 140
    python -m ai.scan physics/1 --force --concurrency 2
"""

import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pymupdf

from ai import promts
from ai.client import build_client
from ai.utils import extract_json
from file_handling.main import BOOKS_DIR, get_textbook_pages

# Текстовый слой страницы читаем, только если он содержательный:
# отдельные подписи под картинкой заданий не описывают.
TEXT_PAGE_MIN_CHARS = 200
# В промпт больше не надо — страница учебника столько не занимает.
PAGE_TEXT_LIMIT = 8000

# Текст разбирает быстрая модель, картинки — та, что умеет смотреть сканы.
DEFAULT_TEXT_MODEL = "GigaChat-2-Max"
DEFAULT_IMAGE_MODEL = "GigaChat-2-Max"


def exercises_path(textbook_path: str) -> Path:
    stem = textbook_path.strip("/")
    return BOOKS_DIR / f"{stem}.exercises.json"


def load_scan(path: Path, textbook_path: str, total_pages: int) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("pages", {})
        data.pop("model", None)
        return data
    return {
        "textbook_path": f"/{textbook_path.strip('/')}",
        "total_pages": total_pages,
        "models": None,
        "scanned_at": None,
        "pages": {},
    }


def save_scan(path: Path, data: dict) -> None:
    """Пишем через временный файл: скан длинный, обрыв не должен его портить."""
    data["scanned_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _clean_items(raw: list) -> list[dict]:
    """Приводит ответ модели к [{"block": ...|None, "number": "..."}]."""
    items: list[dict] = []
    seen: set[tuple[str | None, str]] = set()
    for item in raw or []:
        if isinstance(item, str):
            block, number = None, item
        elif isinstance(item, dict):
            block = item.get("block")
            number = item.get("number")
        else:
            continue

        number = str(number or "").strip().lstrip("№#").strip().rstrip(".").strip()
        if not re.search(r"[0-9A-Za-zА-Яа-я]", number):
            continue
        block = str(block).strip() if block else None
        key = (block, number)
        if key in seen:
            continue
        seen.add(key)
        items.append({"block": block, "number": number})
    return items


def _clean_unit(value) -> str | None:
    """Название Unit'а со страницы («Unit Two») или None."""
    if not value:
        return None
    text = " ".join(str(value).split())
    return text or None


async def scan_page(text_client, image_client, pdf_path: Path, page: int) -> tuple[list[dict], str | None]:
    """Один вызов ИИ: задания одной страницы и её Unit."""
    with pymupdf.open(pdf_path) as document:
        text = document[page - 1].get_text().strip()
    if len(text) >= TEXT_PAGE_MIN_CHARS:
        user_data = {"page": page, "text": text[:PAGE_TEXT_LIMIT]}
        response = await text_client.generate([
            {"role": "system", "content": promts.SCAN_PAGE_PROMT},
            {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
        ])
        try:
            data = extract_json(response)
            return _clean_items(data.get("items", [])), _clean_unit(data.get("unit"))
        except ValueError:
            # На часть текстов GigaChat отвечает шаблонным отказом («не обладает
            # собственным мнением»), поэтому такую страницу читаем картинкой.
            pass
    image = get_textbook_pages(str(pdf_path), [page])[page]
    user_data = {"page": page, "text": "страница приложена картинкой"}
    response = await image_client.generate_with_images(
        [
            {"role": "system", "content": promts.SCAN_PAGE_PROMT},
            {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
        ],
        {f"page_{page}.png": image},
    )
    data = extract_json(response)
    return _clean_items(data.get("items", [])), _clean_unit(data.get("unit"))


async def scan_page_with_retries(text_client, image_client, pdf_path: Path, page: int) -> tuple[list[dict], str | None]:
    last_error = None
    for attempt in range(4):
        try:
            return await scan_page(text_client, image_client, pdf_path, page)
        except Exception as e:
            last_error = e
            # GigaChat на бесплатном тарифе часто отвечает 429 — ждём дольше.
            text = str(e)
            if "429" in text or "Too Many Requests" in text:
                await asyncio.sleep(30 * (attempt + 1))
            else:
                await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError(f"page {page}: {type(last_error).__name__}: {last_error}")


async def scan_book(textbook_path: str, page_from: int = 1, page_to: int | None = None,
                    force: bool = False, concurrency: int = 1, delay: float = 0.0) -> dict:
    pdf_path = BOOKS_DIR / f"{textbook_path.strip('/')}.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    text_client = build_client(
        "gigachat", os.getenv("SCAN_TEXT_MODEL") or DEFAULT_TEXT_MODEL)
    image_client = build_client(
        "gigachat", os.getenv("SCAN_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL)

    with pymupdf.open(pdf_path) as document:
        total_pages = document.page_count

    path = exercises_path(textbook_path)
    data = load_scan(path, textbook_path, total_pages)
    data["total_pages"] = total_pages
    data["models"] = {
        "text": getattr(text_client, "model", None),
        "image": getattr(image_client, "model", None),
    }

    page_to = min(page_to or total_pages, total_pages)
    pages = [
        page for page in range(max(1, page_from), page_to + 1)
        if force or str(page) not in data["pages"]
    ]
    if not pages:
        print(f"{textbook_path}: всё уже просканировано ({len(data['pages'])} стр.)")
        return data

    print(f"{textbook_path}: страниц к скану {len(pages)} "
          f"(уже готово {len(data['pages'])}, всего {total_pages})")

    semaphore = asyncio.Semaphore(max(1, concurrency))
    write_lock = asyncio.Lock()
    started = time.monotonic()
    done = 0

    async def worker(page: int) -> None:
        nonlocal done
        async with semaphore:
            if delay:
                await asyncio.sleep(delay)
            items, unit = await scan_page_with_retries(
                text_client, image_client, pdf_path, page)
        async with write_lock:
            data["pages"][str(page)] = {"unit": unit, "items": items}
            save_scan(path, data)
            done += 1
            elapsed = time.monotonic() - started
            eta = elapsed / done * (len(pages) - done)
            print(f"  [{done}/{len(pages)}] стр. {page}: заданий {len(items)} "
                  f"(осталось ~{eta / 60:.0f} мин)", flush=True)

    results = await asyncio.gather(
        *(worker(page) for page in pages), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    for error in errors:
        print(f"  ошибка: {error}")
    print(f"{textbook_path}: готово, ошибок {len(errors)}")
    return data


def all_textbooks() -> list[str]:
    """Все учебники: «algebra/1», «english/1» и т.д."""
    return sorted(
        str(path.relative_to(BOOKS_DIR).with_suffix("")).replace("\\", "/")
        for path in BOOKS_DIR.glob("*/*.pdf")
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Выписать задания со страниц учебника через GigaChat")
    parser.add_argument("textbook", nargs="?",
                        help="путь без .pdf, например algebra/1")
    parser.add_argument("--all", action="store_true",
                        help="просканировать все учебники из files/books")
    parser.add_argument("--from", dest="page_from", type=int, default=1,
                        help="первая PDF-страница (по умолчанию 1)")
    parser.add_argument("--to", dest="page_to", type=int, default=None,
                        help="последняя PDF-страница (по умолчанию конец книги)")
    parser.add_argument("--force", action="store_true",
                        help="пересканировать уже готовые страницы")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="сколько страниц обрабатывать одновременно")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="пауза перед каждым запросом, секунды")
    args = parser.parse_args()

    if not args.all and not args.textbook:
        parser.error("укажи учебник или --all")

    textbooks = all_textbooks() if args.all else [args.textbook]
    for textbook in textbooks:
        print(f"=== {textbook} ===", flush=True)
        asyncio.run(scan_book(
            textbook,
            page_from=args.page_from,
            page_to=args.page_to,
            force=args.force,
            concurrency=args.concurrency,
            delay=args.delay,
        ))


if __name__ == "__main__":
    main()
