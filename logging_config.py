"""
Configure console and rotating file logger for user actions.
"""

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
USERS_LOGGER_NAME = "protein_tracker.users"


def _make_timed_handler(filename: str) -> TimedRotatingFileHandler:
    path = Path(__file__).parent / filename
    handler = TimedRotatingFileHandler(
        path,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(logging.INFO)
    return handler


def setup_logging() -> None:
    """
    Root → console. Named logger → users.log (7-day rotation).
    Named logger propagates so its lines also appear on the console.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    console.setLevel(logging.INFO)
    root.addHandler(console)

    users_logger = logging.getLogger(USERS_LOGGER_NAME)
    users_logger.setLevel(logging.INFO)
    users_logger.handlers.clear()
    users_logger.addHandler(_make_timed_handler("users.log"))
    users_logger.propagate = True
