import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from typing import Optional
from src.paths import get_logs_dir


def setup_logger(
    name: str = "ImageSorter",
    log_file: Optional[str] = None,
    level: int = logging.INFO
) -> logging.Logger:
    """
    Configures and returns a robust logger with console and RotatingFileHandler,
    using RFC-3339 formatted timestamps and file/line telemetry.

    Args:
        name (str): The name of the logger.
        log_file (Optional[str]): Custom log file path or filename. If None, resolves via paths.py.
        level (int): The logging level.

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        # ISO-8601 / RFC-3339 timestamp format
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S%z'
        )

        # Console Handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # File Handler using RotatingFileHandler (10MB max, 5 backups)
        if log_file is None:
            log_path = get_logs_dir() / "imagesorter.log"
        else:
            log_path = get_logs_dir() / os.path.basename(log_file)

        try:
            rfh = RotatingFileHandler(
                str(log_path),
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding='utf-8'
            )
            rfh.setLevel(level)
            rfh.setFormatter(formatter)
            logger.addHandler(rfh)
        except OSError as e:
            logger.warning(f"Failed to create RotatingFileHandler at {log_path}: {e}")

    return logger


logger = setup_logger()
