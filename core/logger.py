"""Logging configuration for AI Tool."""
import logging
import sys
from pathlib import Path


def setup_logger(debug: bool = False) -> logging.Logger:
    """Setup and configure application logger."""
    level = logging.DEBUG if debug else logging.INFO
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Ensure logs directory in workspace exists
    logs_dir = Path("workspace") / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "bot.log"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8")
    ]

    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        handlers=handlers,
        force=True
    )

    # Suppress verbose HTTP logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.ExtBot").setLevel(logging.INFO)

    logger = logging.getLogger("ai_tool")
    logger.setLevel(level)
    return logger


logger = logging.getLogger("ai_tool")
