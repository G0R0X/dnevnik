"""Единая настройка логирования для всех процессов проекта.

Вызывается один раз на старте процесса: `setup_logging("bot")`,
`setup_logging("worker")` или `setup_logging()` для CLI-утилит. В модулях
дальше только `logger = logging.getLogger(__name__)` и `logger.info(...)`.

Уровень задаётся переменной окружения `LOG_LEVEL` (по умолчанию `INFO`),
путь к файлу — `LOG_FILE` (по умолчанию `logs/<имя процесса>.log`).
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

# Время, уровень, имя модуля — по нему видно, откуда пришла строка.
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Файл растёт на 20–30 МБ в неделю при INFO, поэтому ротация по 5 МБ:
# пять архивов, старое вытесняется само.
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def setup_logging(name: str = "dnevnik", level: str | None = None) -> None:
    """Включает логи в консоль и в файл с ротацией.

    `name` — имя процесса, по нему называется файл: у бота и воркера они
    разные (`logs/bot.log`, `logs/worker.log`). Общий файл на два процесса
    не годится: при ротации один переименовывает файл, второй продолжает
    в него писать — на Windows это ошибка доступа, и часть логов теряется.

    `level` — уровень для консоли и файла, по умолчанию `LOG_LEVEL` или INFO.
    """
    root = logging.getLogger()
    if root.handlers:
        # Функцию зовут на старте каждого процесса, а в тестах и повторно:
        # без этой проверки каждая строка дублировалась бы.
        return

    log_level = _parse_level(level or os.getenv("LOG_LEVEL"), logging.INFO)
    root.setLevel(log_level)

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    console = logging.StreamHandler(_utf8_stream(sys.stderr))
    console.setLevel(log_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    log_path = Path(os.getenv("LOG_FILE") or LOG_DIR / f"{name}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def _parse_level(value: str | None, default: int) -> int:
    """Имя уровня из .env («info», «DEBUG») в число; мусор — default."""
    if not value:
        return default
    return logging.getLevelNamesMapping().get(value.strip().upper(), default)


def _utf8_stream(stream):
    """UTF-8 для консоли: на Windows по умолчанию cp1251/cp866.

    Без этого кириллица в консоли падает с UnicodeEncodeError, а сообщения
    с символами вне кодировки (стрелки, длинные тире) — вылетают целиком.
    """
    if stream is None or not hasattr(stream, "reconfigure"):
        return stream
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    return stream
