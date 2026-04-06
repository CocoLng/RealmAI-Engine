"""Logging configuration for RealmAI Engine.

Configures dual output: console (concise) + per-session file (detailed).
Call setup_logging() once at startup, before any other imports log.
Each bot launch creates a new log file: realm_YYYYMMDD_HHMMSS.log
"""

import logging
from datetime import datetime
from pathlib import Path


def setup_logging(
    *,
    log_dir: str = "logs",
    level: int = logging.INFO,
) -> None:
    """Configure logging with console + per-session file output.

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

    # File handler — one file per session for full history
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(
        filename=log_path / f"realm_{timestamp}.log",
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
