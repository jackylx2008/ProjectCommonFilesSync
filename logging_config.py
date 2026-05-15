import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(
    log_level: int | str = logging.INFO,
    log_file: str | Path | None = None,
    log_dir: str | Path = "log",
    filemode: str = "a",
) -> logging.Logger:
    """Initialize project-wide logging for console and rotating file output."""
    if log_file is None:
        main_module = os.path.splitext(os.path.basename(sys.argv[0]))[0] or "app"
        log_file = Path(log_dir) / f"{main_module}.log"
    else:
        log_file = Path(log_file)

    log_file.parent.mkdir(parents=True, exist_ok=True)

    log_format = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    resolved_level = _resolve_log_level(log_level)

    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)

    if root_logger.handlers:
        root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(resolved_level)
    console_handler.setFormatter(logging.Formatter(log_format))

    file_handler = RotatingFileHandler(
        log_file,
        mode=filemode,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(resolved_level)
    file_handler.setFormatter(logging.Formatter(log_format))

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.debug("Logger initialized: level=%s file=%s", logging.getLevelName(resolved_level), log_file)

    return root_logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a named logger; handlers are configured only by setup_logger()."""
    return logging.getLogger(name)


def _resolve_log_level(log_level: int | str) -> int:
    if isinstance(log_level, int):
        return log_level
    level = logging.getLevelName(log_level.upper())
    if isinstance(level, int):
        return level
    raise ValueError(f"Unsupported log level: {log_level}")
