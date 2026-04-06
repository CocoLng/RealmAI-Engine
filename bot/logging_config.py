"""Logging configuration for RealmAI Engine.

Configures dual output: console (concise) + rotating file (detailed).
Call setup_logging() once at startup, before any other imports log.
"""

import logging
import logging.handlers
from pathlib import Path


def setup_logging(
    *,
    log_dir: str = "logs",
    level: int = logging.INFO,
) -> None:
    """Configure logging with console + daily rotating file output.

    Args:
        log_dir: Directory for log files (created if missing).
        level: Root log level for application loggers.
    """
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler — concise, time only
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)

    # File handler — detailed, daily rotation, 14 days retention
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_path / "realm.log",
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for noisy in ("discord", "httpx", "openai", "chromadb", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
