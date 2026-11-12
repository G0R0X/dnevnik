"""Текст страниц учебника для пересказа.

Сначала пробуем текстовый слой PDF — это бесплатно и мгновенно. Если слой
отсутствует (сканы: алгебра, геометрия, информатика, английский) или
испорчен (в физике формулы рассыпаются на символы), страницу распознаёт
бесплатная GigaChat. Результат нигде не кэшируется: пересказ сохраняется
в БД, а повторно страницы нужны только при перерешивании, которого пока нет.
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import pymupdf

from ai import promts
from ai.client import build_client
from file_handling.main import BOOKS_DIR, get_textbook_pages

logger = logging.getLogger(__name__)

# Слой из одних подписей к картинкам заданием не является.
TEXT_PAGE_MIN_CHARS = 200
# В физике текстовый слой формально есть, но слова разорваны пробелами
# («Движущ иеся»), а формулы рассыпаются на символы. Такой слой для пересказа
# хуже OCR, поэтому физику распознаём картинкой всегда.
FORCE_OCR_SUBJECTS = {"physics"}
# Чем распознаём страницы-сканы:
#   routerai — платно (~0.077 ₽ за страницу), зато держит 4 запроса параллельно;
#   gigachat — бесплатно, но free-тариф пропускает только один запрос
#              одновременно (второй сразу получает 429), поэтому там
#              параллельность не даёт выигрыша.
OCR_PROVIDER = (os.getenv("OCR_PROVIDER") or "routerai").strip().lower()
OCR_IS_GIGACHAT = OCR_PROVIDER == "gigachat"
# Модель OCR. По умолчанию — та же, что у разбора и решения (AI_MODEL):
# она быстрее всех проверенных (~3 с на страницу). Дешевле (вдвое) только
# nex-agi/nex-n2.5-mini, но он медленнее в 3–4 раза и иногда не укладывается
# в таймаут, поэтому дешёвый вариант включается вручную через OCR_MODEL.
OCR_MODEL = os.getenv("OCR_MODEL") or os.getenv("SCAN_IMAGE_MODEL") or ""
OCR_CONCURRENCY = max(1, min(8, int(
    os.getenv("OCR_CONCURRENCY") or (1 if OCR_IS_GIGACHAT else 4))))
OCR_ATTEMPTS = 3
# При 429 паузу держат все воркеры: иначе каждый долбит API своими повторами.
OCR_COOLDOWN = 10.0
# Пауза перед повторной загрузкой страницы после 429 на files API (GigaChat).
OCR_UPLOAD_PAUSE = 5.0
# GigaChat free режет залп запросов, поэтому старты страниц разносим по времени.
OCR_START_INTERVAL = float(
    os.getenv("OCR_START_INTERVAL") or (0.5 if OCR_IS_GIGACHAT else 0))
# Больше в промпт не отдаём: несколько параграфов могут дать очень много текста.
RETELL_TEXT_LIMIT = 120_000

# Распознавание — механическая работа: reasoning модели тут не нужен, а платим
# мы именно за него. Без него страница дешевле вдвое (0,047 против 0,094 ₽)
# и быстрее (7 с против 12 с), текст тот же.
_ocr_client = build_client(OCR_PROVIDER, OCR_MODEL or None, reasoning_effort="none")
_cooldown_until = 0.0
_next_start = 0.0
_start_lock = asyncio.Lock()


def _read_text_layers(pdf_path: Path, pages: list[int]) -> dict[int, str]:
    """Текстовый слой страниц одним открытием PDF."""
    with pymupdf.open(pdf_path) as document:
        return {
            page: document[page - 1].get_text().strip()
            for page in pages
        }


def _page_count(pdf_path: Path) -> int:
    with pymupdf.open(pdf_path) as document:
        return document.page_count


def _is_text_layer_usable(text: str) -> bool:
    """Годится ли текстовый слой страницы или лучше распознать картинкой.

    В физике слой формально есть, но формулы выходят как «В = IAI», а слова
    разорваны мягкими переносами. Такие страницы надёжнее отдать OCR.
    """
    if len(text) < TEXT_PAGE_MIN_CHARS:
        return False
    if "\ufffd" in text or "■" in text or "□" in text or "~" in text:
        return False
    return True


def _clean_text(text: str) -> str:
    """Убирает мягкие переносы, колонтитулы и лишние пробелы."""
    text = text.replace("\xad", "").replace("\u00a0", " ")
    # «слово-\nпродолжение» → «словопродолжение»
    text = re.sub(r"(?<=[А-Яа-яA-Za-z])-\n(?=[а-яa-z])", "", text)
    text = re.sub(r"[ \t]+", " ", text)

    lines = [line.strip() for line in text.splitlines()]
    # Номер страницы стоит первой или последней строкой. Числа в середине
    # не трогаем: в формулах и заданиях они значимые.
    if lines and re.fullmatch(r"\d{1,4}", lines[0]):
        lines.pop(0)
    if lines and re.fullmatch(r"\d{1,4}", lines[-1]):
        lines.pop()

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _pause_all(seconds: float) -> None:
    """Общая пауза для всех воркеров после 429."""
    global _cooldown_until
    _cooldown_until = max(_cooldown_until,
                          asyncio.get_running_loop().time() + seconds)


async def _wait_cooldown() -> None:
    """Ждёт, пока не истечёт общая пауза (её ставит любой поймавший 429)."""
    loop = asyncio.get_running_loop()
    while (delay := _cooldown_until - loop.time()) > 0:
        await asyncio.sleep(min(delay, 2.0))


async def _pace_start() -> None:
    """Разносит старты запросов по времени, чтобы не ловить 429 залпом."""
    global _next_start
    loop = asyncio.get_running_loop()
    async with _start_lock:
        now = loop.time()
        start_at = max(now, _next_start)
        _next_start = start_at + OCR_START_INTERVAL
    if start_at > now:
        await asyncio.sleep(start_at - now)


def _is_rate_limit(error: Exception) -> bool:
    text = str(error)
    return "429" in text or "Too Many Requests" in text or "rate limit" in text.lower()


async def _upload_pages(images: dict[int, bytes]) -> dict[int, str]:
    """Загружает страницы в files API по одной: залп загрузок даёт 429."""
    file_ids: dict[int, str] = {}
    for page, image in images.items():
        for attempt in range(1, OCR_ATTEMPTS + 1):
            try:
                file_ids[page] = await _ocr_client.upload_image(
                    f"page_{page}.png", image)
                break
            except Exception as error:
                if not _is_rate_limit(error) or attempt == OCR_ATTEMPTS:
                    logger.warning("стр. %d: не загрузилась в GigaChat: %s: %s",
                                   page, type(error).__name__, error)
                    break
                logger.debug("стр. %d: загрузка, попытка %d/%d: %s",
                             page, attempt, OCR_ATTEMPTS, error)
                await asyncio.sleep(OCR_UPLOAD_PAUSE * attempt)
    return file_ids


async def _ocr_page(page: int, image: bytes | None = None,
                    file_id: str | None = None) -> str:
    """Распознаёт страницу. Пустая строка — не вышло."""
    if OCR_IS_GIGACHAT and file_id is None:
        return ""
    if not OCR_IS_GIGACHAT and image is None:
        return ""

    last_error: Exception | None = None
    for attempt in range(1, OCR_ATTEMPTS + 1):
        await _wait_cooldown()
        await _pace_start()
        try:
            messages = [
                {"role": "system", "content": promts.TRANSCRIBE_PAGE_PROMT},
                {"role": "user", "content": json.dumps(
                    {"page": page}, ensure_ascii=False)},
            ]
            if OCR_IS_GIGACHAT:
                return await _ocr_client.generate_with_image_ids(
                    messages, [file_id])
            return await _ocr_client.generate_with_images(
                messages, {f"page_{page}.png": image})
        except Exception as error:
            last_error = error
            if _is_rate_limit(error):
                pause = OCR_COOLDOWN * attempt
                logger.warning("стр. %d: сбавляю темп на %.0f с (%s)",
                               page, pause, str(error)[:120])
                await _pause_all(pause)
            else:
                logger.debug("стр. %d: OCR, попытка %d/%d не удалась: %s",
                             page, attempt, OCR_ATTEMPTS, error)
            if attempt < OCR_ATTEMPTS:
                await asyncio.sleep(5 * attempt)

    logger.warning("стр. %d: OCR не удался: %s: %s",
                   page, type(last_error).__name__, last_error)
    return ""


async def get_pages_text(textbook_path: str, pages: list[int]) -> str:
    """Текст страниц учебника в порядке нумерации.

    Каждая страница сначала проверяется на текстовый слой; сканы и битые
    страницы распознаются картинкой — параллельно, по OCR_CONCURRENCY штук.
    Одна неудачная страница не рушит пересказ: остальные всё равно попадут
    в промпт.
    """
    if not textbook_path or not pages:
        return ""

    pdf_path = BOOKS_DIR / f"{textbook_path.strip('/')}.pdf"
    if not pdf_path.exists():
        logger.warning("%s: PDF учебника не найден", textbook_path)
        return ""

    started = time.perf_counter()
    total = await asyncio.to_thread(_page_count, pdf_path)
    pages = sorted({page for page in pages if 1 <= page <= total})
    if not pages:
        return ""

    raw_texts = await asyncio.to_thread(_read_text_layers, pdf_path, pages)
    force_ocr = textbook_path.strip("/").split("/")[0] in FORCE_OCR_SUBJECTS

    texts: dict[int, str] = {}
    to_ocr: list[int] = []
    for page in pages:
        raw = raw_texts.get(page, "")
        if not force_ocr and _is_text_layer_usable(raw):
            texts[page] = _clean_text(raw)
        else:
            to_ocr.append(page)

    if to_ocr:
        logger.info("%s: распознаю страниц %d из %d (%s, параллельно %d)",
                    textbook_path, len(to_ocr), len(pages),
                    OCR_PROVIDER, OCR_CONCURRENCY)
        # PDF не потокобезопасен: рендерим все нужные страницы одним вызовом
        # в отдельном потоке, чтобы не блокировать event loop по странице.
        images = await asyncio.to_thread(
            get_textbook_pages, str(pdf_path), to_ocr)
        file_ids: dict[int, str] = {}
        if OCR_IS_GIGACHAT:
            # Загрузка — по одной (иначе 429 на files API), распознавание — параллельно.
            file_ids = await _upload_pages(images)
            del images
        semaphore = asyncio.Semaphore(OCR_CONCURRENCY)
        done = 0

        async def page_text(page: int) -> tuple[int, str]:
            nonlocal done
            raw = raw_texts.get(page, "")
            async with semaphore:
                if OCR_IS_GIGACHAT:
                    recognized = await _ocr_page(page, file_id=file_ids.get(page))
                else:
                    # Картинку забираем из словаря: после отправки PNG не нужен.
                    recognized = await _ocr_page(page, image=images.pop(page, None))
            done += 1
            if recognized.strip():
                logger.info("OCR %d/%d: стр. %d, %d символов",
                            done, len(to_ocr), page, len(recognized))
                return page, _clean_text(recognized)
            if raw.strip():
                # OCR не вышло — лучше грязный слой, чем потерять страницу.
                logger.warning("стр. %d: беру текстовый слой, хотя он выглядит битым",
                               page)
                return page, _clean_text(raw)
            return page, ""

        texts.update(await asyncio.gather(*(page_text(page) for page in to_ocr)))

    parts = [texts[page] for page in pages if texts.get(page)]
    joined = "\n\n".join(parts)

    if len(joined) > RETELL_TEXT_LIMIT:
        logger.warning("текст параграфа обрезан: %d > %d символов",
                       len(joined), RETELL_TEXT_LIMIT)
        joined = joined[:RETELL_TEXT_LIMIT]

    logger.info("текст параграфа: страниц %d, символов %d, %.1f с",
                len(parts), len(joined), time.perf_counter() - started)
    return joined


if __name__ == "__main__":
    import sys

    from logging_setup import setup_logging

    setup_logging()
    path = sys.argv[1]
    page_numbers = [int(value) for value in sys.argv[2:]]
    print(asyncio.run(get_pages_text(path, page_numbers)))
