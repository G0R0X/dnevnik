import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BOOKS_DIR = PROJECT_ROOT / "files" / "books"
HOMEWORK_DIR = PROJECT_ROOT / "files" / "homework"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

TEXT_LIMIT = 20000
PDF_IMAGE_PAGE_LIMIT = 6

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def get_textbook_pages(pdf_path: str, page_numbers: list[int]) -> dict[int, bytes]:
    path = Path(pdf_path)
    pages = {}

    with pymupdf.open(path) as document:
        for page_number in set(page_numbers):
            page = document[page_number - 1]
            pixmap = page.get_pixmap(dpi=180)

            if _is_empty(pixmap):
                # страница отрендерилась пустой — почти всегда битый JPEG2000-слой
                fallback = _extract_page_image(document, page)
                if fallback is not None:
                    pixmap = fallback

            pages[page_number] = pixmap.tobytes("png")

    return pages


def _is_empty(pixmap: pymupdf.Pixmap) -> bool:
    """Проверяет, что страница однотонная (белая) — рендер не удался."""
    samples = pixmap.samples
    channels = pixmap.n
    first = samples[:channels]
    step = channels * 997
    return all(
        samples[i:i + channels] == first
        for i in range(0, len(samples) - channels, step)
    )


def _extract_page_image(document: pymupdf.Document, page: pymupdf.Page) -> pymupdf.Pixmap | None:
    """Достаёт самую крупную вложенную картинку страницы.

    В части учебников (информатика) основной слой — битый JPEG2000,
    который MuPDF не декодирует, но рядом лежит читаемая серая картинка
    с тем же содержимым страницы.
    """
    best = None
    for image in page.get_images(full=True):
        try:
            data = document.extract_image(image[0])
            pixmap = pymupdf.Pixmap(data["image"])
        except Exception:
            continue
        if pixmap.n not in (1, 3):
            continue
        if best is None or pixmap.width * pixmap.height > best.width * best.height:
            best = pixmap
    return best


def get_count_of_pages(pdf_path: str) -> int:
    with pymupdf.open(Path(pdf_path)) as document:
        return len(document)


def load_structure(textbook_path: str) -> dict | None:
    stem = textbook_path.strip("/")
    structure_path = BOOKS_DIR / f"{stem}.structure.json"
    if not structure_path.exists():
        return None
    with open(structure_path, encoding="utf-8") as f:
        return json.load(f)


def _printed_to_pdf(structure: dict, page: int) -> int:
    """Печатный номер → PDF-страница. structure.json уже прочитан."""
    offset = int(structure.get("page_offset", 0))
    skipped = structure.get("skipped_pages") or []
    missing = sum(1 for skipped_page in skipped if skipped_page <= page)
    return page + offset - missing


def printed_to_pdf(textbook_path: str, page: int) -> int:
    structure = load_structure(textbook_path)
    if structure is None:
        return page
    return _printed_to_pdf(structure, page)


def _starts_of_paragraph(structure: dict) -> dict[int, int]:
    """Начала параграфов в PDF-страницах. structure.json уже прочитан."""
    return {
        int(k): _printed_to_pdf(structure, v)
        for k, v in structure["paragraphs"].items()
        if k.isdigit()
    }


def get_starts_of_paragraph(textbook_path: str) -> dict[int, int]:
    structure = load_structure(textbook_path)
    if structure is None:
        return {}
    return _starts_of_paragraph(structure)


def pdf_to_printed(textbook_path: str, page: int) -> int:
    structure = load_structure(textbook_path)
    if structure is None:
        return page
    offset = int(structure.get("page_offset", 0))
    skipped = structure.get("skipped_pages") or []
    start = max(1, page - offset)
    for printed in range(start, page - offset + len(skipped) + 2):
        missing = sum(1 for skipped_page in skipped if skipped_page <= printed)
        if printed + offset - missing == page:
            return printed
    return page


def get_pages_for_paragraphs(textbook_path: str, paragraphs: list[int],
                             include_next_page: bool = True) -> list[int]:
    structure = load_structure(textbook_path)
    if structure is None:
        return []

    total_pages = structure["total_pages"]
    starts = _starts_of_paragraph(structure)
    sorted_starts = sorted(starts.items())

    pages: set[int] = set()
    for paragraph in paragraphs:
        start = starts.get(paragraph)
        if start is None:
            continue
        end = total_pages
        for _, page in sorted_starts:
            if page > start:
                end = page if include_next_page else page - 1
                break
        if end < start:
            continue
        pages.update(range(start, end + 1))

    return sorted(pages)


_TARGET_PREFIX_RE = re.compile(
    r"^(?:упражнени[ея]|упр\.?|exercise|ex\.?|задани[ея]|task)\s+(?=\d)",
    re.IGNORECASE,
)


def normalize_target(value: str) -> str:
    """Приводит элемент задания к сравниваемому виду.

    Строчные, без «№»/«#» и без слов-префиксов: «Упражнение 22» и «22»
    должны сравниваться одинаково, иначе индекс не находит страницы.
    """
    text = " ".join(str(value).strip().lower().split())
    while text[:1] in ("№", "#"):
        text = text[1:].lstrip()
    text = _TARGET_PREFIX_RE.sub("", text).strip()
    while text[:1] in ("№", "#"):
        text = text[1:].lstrip()
    return text


def load_exercises(textbook_path: str) -> dict | None:
    stem = textbook_path.strip("/")
    path = BOOKS_DIR / f"{stem}.exercises.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_UNIT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _normalize_unit(unit) -> str:
    """«Unit Two», «two», 2, «юнит 2» → «unit two»."""
    if isinstance(unit, int):
        return f"unit {_UNIT_WORDS.get(unit, unit)}"
    text = normalize_target(unit).replace("юнит", "unit")
    if text.isdigit():
        return f"unit {_UNIT_WORDS.get(int(text), text)}"
    if text in _UNIT_WORDS.values():
        return f"unit {text}"
    return re.sub(
        r"\bunit\s+(\d+)\b",
        lambda match: f"unit {_UNIT_WORDS.get(int(match.group(1)), match.group(1))}",
        text,
    )


def get_unit_ranges(textbook_path: str) -> list[tuple[str, int, int]]:
    structure = load_structure(textbook_path)
    if structure is None:
        return []

    total_pages = structure["total_pages"]
    starts: dict[str, int] = {}
    for key, page in structure["paragraphs"].items():
        match = re.match(r"^(unit\s+\w+)", key, re.IGNORECASE)
        if match:
            starts.setdefault(key[:match.end(1)],
                              _printed_to_pdf(structure, page))

    ordered = sorted(starts.items(), key=lambda item: item[1])
    ranges = []
    for index, (name, start) in enumerate(ordered):
        end = ordered[index + 1][1] - 1 if index + \
            1 < len(ordered) else total_pages
        ranges.append((name, start, end))
    return ranges


def get_unit_pages(textbook_path: str, unit) -> set[int]:
    wanted = _normalize_unit(unit)
    pages: set[int] = set()
    for name, start, end in get_unit_ranges(textbook_path):
        normalized = normalize_target(name)
        if normalized.startswith(wanted) or wanted.startswith(normalized):
            pages.update(range(start, end + 1))
    return pages


def get_page_units(textbook_path: str) -> dict[int, str]:
    """PDF-страница → название Unit'а (для книг, где нумерация идёт по юнитам)."""
    pages: dict[int, str] = {}
    for name, start, end in get_unit_ranges(textbook_path):
        for page in range(start, end + 1):
            pages[page] = name
    return pages


def get_page_unit_from_index(textbook_path: str, pages: list[int]) -> str | None:
    """Unit страницы по индексу заданий.

    Индекс хранит заголовок Unit'а со страницы (`scan.py`), поэтому по
    словарным страницам в конце книги можно понять, к какому Unit они относятся.
    """
    index = load_exercises(textbook_path)
    if index is None:
        return None
    for page in pages:
        unit = index.get("pages", {}).get(str(page), {}).get("unit")
        if unit:
            return unit
    return None


def _matches_target(number: str, block: str, target: str) -> bool:
    """Совпадает ли задание со страницы с искомым элементом.

    Все три строки уже приведены `normalize_target`. Элемент задания может
    быть сквозным номером («42.15») или заданием из блока («Вопросы №3»).
    Блок сравниваем по началу названия: модели называют его то «Вопросы»,
    то «Вопросы и задания».
    """
    if not number:
        return False
    if target == number:
        return True
    if block and target in (f"{block} {number}", f"{block} №{number}"):
        return True
    if not block:
        return False

    match = re.match(
        rf"^(?P<head>.*?)\s*(?:№|#)?\s*(?<!\d){re.escape(number)}$", target)
    if match is None:
        return False
    head = match.group("head").strip(" №#.,")
    if not head:
        return True
    return head.startswith(block) or block.startswith(head)


def find_pages_by_targets(textbook_path: str, targets: list[str],
                          unit=None) -> tuple[list[int], list[str]]:
    """Ищет страницы с заданиями по готовому индексу, без обращения к ИИ.

    Возвращает (PDF-страницы, ненайденные targets). Если индекса нет,
    все targets считаются ненайденными — вызывающий код идёт по старинке.
    """
    exercises = load_exercises(textbook_path)
    if exercises is None:
        return [], [str(target) for target in targets]

    allowed = None
    if unit is not None:
        allowed = get_unit_pages(textbook_path, unit)
        if not allowed:
            return [], [str(target) for target in targets]

    page_units = get_page_units(textbook_path) if unit is None else {}

    # Индекс нормализуем один раз на вызов: каждый элемент сравнивается
    # с каждым target, поэтому нормализация в цикле стоит targets × items.
    index: list[tuple[int, list[tuple[str, str]]]] = []
    for page, data in exercises.get("pages", {}).items():
        page_number = int(page)
        if allowed is not None and page_number not in allowed:
            continue
        index.append((
            page_number,
            [
                (normalize_target(item.get("number") or ""),
                 normalize_target(item.get("block") or ""))
                for item in data.get("items", [])
            ],
        ))

    pages: set[int] = set()
    missing: list[str] = []
    for target in targets:
        text = normalize_target(target)
        target_pages = set()
        target_units = set()
        for page_number, items in index:
            if any(_matches_target(number, block, text) for number, block in items):
                target_pages.add(page_number)
                if page_number in page_units:
                    target_units.add(page_units[page_number])
        if len(target_units) > 1:
            # Номер повторяется в разных Unit'ах — без указания Unit'а
            # непонятно, какой открывать. Пусть решает ИИ по страницам.
            missing.append(str(target))
        elif target_pages:
            pages.update(target_pages)
        else:
            missing.append(str(target))
    return sorted(pages), missing


def get_attachment_path(file_id: int) -> Path | None:
    folder = HOMEWORK_DIR / str(file_id)
    if not folder.is_dir():
        return None
    for path in folder.iterdir():
        if path.is_file():
            return path
    return None


def read_attachment(path: Path) -> tuple[str, dict[str, bytes]]:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "", {path.name: path.read_bytes()}
    if suffix == ".pdf":
        return _pdf_content(path)
    if suffix == ".docx":
        return _docx_text(path), {}
    if suffix == ".xlsx":
        return _xlsx_text(path), {}
    if suffix in (".txt", ".md", ".csv"):
        return _read_text_file(path), {}
    return "", {}


def _read_text_file(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")

    paragraphs = []
    for paragraph in ElementTree.fromstring(xml).iter(f"{_W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_W}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in item.iter(f"{_X}t"))
                for item in root
            ]

        lines = []
        for name in sorted(n for n in names if n.startswith("xl/worksheets/sheet")):
            root = ElementTree.fromstring(archive.read(name))
            for row in root.iter(f"{_X}row"):
                cells = []
                for cell in row.iter(f"{_X}c"):
                    value = cell.find(f"{_X}v")
                    if value is None or value.text is None:
                        continue
                    if cell.get("t") == "s":
                        cells.append(shared[int(value.text)])
                    else:
                        cells.append(value.text)
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines)


def _pdf_content(path: Path) -> tuple[str, dict[str, bytes]]:
    with pymupdf.open(path) as document:
        text = "\n".join(page.get_text() for page in document)
        page_count = document.page_count

    if len(text.strip()) >= 200:
        return text.strip(), {}

    # Скан без текстового слоя — показываем первые страницы картинками.
    pages = list(range(1, min(page_count, PDF_IMAGE_PAGE_LIMIT) + 1))
    images = get_textbook_pages(str(path), pages)
    return "", {f"{path.stem}_p{page}.png": data for page, data in images.items()}


def limit_attachment_text(text: str) -> str:
    """Обрезает слишком длинный текст файла, помечая обрыв."""
    text = text.strip()
    if len(text) <= TEXT_LIMIT:
        return text
    return text[:TEXT_LIMIT].rstrip() + "\n[текст файла обрезан]"


if __name__ == "__main__":
    # print(get_pages_for_paragraphs("algebra/1", [42]))
    b = get_textbook_pages("files/books/algebra/1.pdf", [327, 328])
    for k, v in b.items():
        with open(f'{k}.png', "wb") as f:
            f.write(v)
