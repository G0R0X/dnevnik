import logging

LOGGING_CONFIG = {
    "version": 1,
    "formatters": {
        "simple": {
            "format": "%(levelname)s | %(name)s | %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "simple"
        },
        "file": {
            "class": "logging.FileHandler",
            "level": "WARNING",
            "formatter": "simple",
            "filename": "app.log",
            "encoding": "utf-8"
        }
    },
    "root": {
        "level": "DEBUG",
        "handlers": ["console", "file"]
    }
}
