"""Structured, category-based logging.

Logs go to per-category files under `logs/` (system / ai / claude / browser /
orchestrator) plus a readable console stream. Secrets must never be logged;
callers are responsible for not passing tokens/keys into log messages.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

_CONFIGURED: set[str] = set()
_LOGS_DIR: Path | None = None

# Category -> log file name.
_CATEGORIES = {
    "system": "system.log",
    "ai": "ai.log",
    "claude": "claude.log",
    "browser": "browser.log",
    "orchestrator": "orchestrator.log",
    "telegram": "system.log",
    "state": "system.log",
    "git": "system.log",
}


def init_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    """Configure the root logger once. Safe to call multiple times."""
    global _LOGS_DIR
    _LOGS_DIR = logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("aidevbot")
    if root.handlers:
        return
    root.setLevel(level)
    root.propagate = False

    console = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    console.setFormatter(logging.Formatter("%(name)s | %(message)s"))
    root.addHandler(console)


def get_logger(category: str = "system") -> logging.Logger:
    """Return a logger for a category, attaching its file handler on first use."""
    name = f"aidevbot.{category}"
    logger = logging.getLogger(name)
    if category in _CONFIGURED or _LOGS_DIR is None:
        return logger

    file_name = _CATEGORIES.get(category, "system.log")
    handler = RotatingFileHandler(
        _LOGS_DIR / file_name, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    _CONFIGURED.add(category)
    return logger
