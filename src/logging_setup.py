"""Единая настройка логирования для всех процессов проекта.

Вызывается один раз на старте процесса: `setup_logging("bot")`,
`setup_logging("worker")` или `setup_logging()` для CLI-утилит. В модулях
дальше только `logger = logging.getLogger(__name__)` и `logger.info(...)`.

Уровень задаётся переменной окружения `LOG_LEVEL` (по умолчанию `INFO`),
путь к файлу — `LOG_FILE` (по умолчанию `logs/<имя процесса>.log`).

Время в строках — местное для процесса (`TZ` из systemd-юнита). Кто именно
дёрнул обработчик, видно по `[tg:<id>]` перед именем модуля: бот привязывает
telegram id к корутине через `bind_user` на входе в middleware, поэтому метка
сама появляется во всех строках, написанных во время обработки нажатия.
"""

import logging
import os
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

# Telegram id того, кто сейчас обрабатывается. ContextVar, а не глобальная
# переменная: у бота обработка последовательная, но воркер и CLI живут
# отдельными процессами, а корутины могут накладываться.
_current_user: ContextVar[int | None] = ContextVar("log_user", default=None)

# Время, уровень, [tg:<id>], имя модуля — по нему видно, откуда строка.
# Метку пользователя подставляет UserFormatter: `%(user)s` — либо « [tg:123]»,
# либо пустая строка, чтобы в воркере и CLI не было мусорных скобок.
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s%(user)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Файл растёт на 20–30 МБ в неделю при INFO, поэтому ротация по 5 МБ:
# пять архивов, старое вытесняется само.
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def bind_user(user_id: int | None):
    """Привязывает telegram id к текущей корутине. Возвращает токен для reset."""
    return _current_user.set(user_id)


def reset_user(token) -> None:
    """Снимает привязку: следующие строки снова идут без пользователя."""
    _current_user.reset(token)


class UserFormatter(logging.Formatter):
    """Добавляет к строке telegram id того, кто её вызвал (если он известен)."""

    def format(self, record: logging.LogRecord) -> str:
        user = _current_user.get()
        record.user = f" [tg:{user}]" if user else ""
        return super().format(record)



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

    formatter = UserFormatter(LOG_FORMAT, DATE_FORMAT)

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
