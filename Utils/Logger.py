"""
utils/logger.py
───────────────
Logging configuration — writes to both console and a rotating log file.
"""

import logging
import logging.handlers
import os
from pathlib import Path

import config


def setup_logging() -> None:
    """Configure the root logger with console + file handlers."""
    log_dir = Path(config.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-import
    root.handlers.clear()

    # ── Console handler ───────────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-25s  %(message)s",
        datefmt="%H:%M:%S"
    ))
    root.addHandler(console)

    # ── Rotating file handler ─────────────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "ict_bot.log",
        maxBytes   = 5 * 1024 * 1024,   # 5 MB
        backupCount= 3,
        encoding   = "utf-8",
    )
    file_handler.setLevel(logging.DEBUG)   # always verbose in file
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root.addHandler(file_handler)
