import logging
from logging.handlers import RotatingFileHandler
import sys
import os
from typing import Optional

def setup_logger(name: str = "ImageSorter", log_file: Optional[str] = None, level: int = logging.INFO) -> logging.Logger:
    """
    Configures and returns a robust enterprise logger with both console and file handlers.
    Uses PathManager to resolve the log path correctly, and RotatingFileHandler
    for stable log sizing.

    Args:
        name (str): The name of the logger.
        log_file (Optional[str]): The path to the log file. If None, resolves via PathManager.
        level (int): The logging level (e.g., logging.INFO, logging.DEBUG).

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger: logging.Logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding handlers multiple times if the logger is already set up
    if not logger.handlers:
        # RFC-3339 style timestamping with file/line telemetry
        formatter: logging.Formatter = logging.Formatter(
            '%(asctime)s.%(msecs)03dZ - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S'
        )

        # Console Handler
        ch: logging.StreamHandler = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # File Handler setup
        try:
            # We defer import to avoid circular dependency since logger is usually imported early.
            # However, since paths.py has zero dependencies, importing here is perfectly safe.
            from src.paths import PathManager

            resolved_log_file: str = log_file if log_file else PathManager.get_log_path()

            # Ensure the directory exists
            os.makedirs(os.path.dirname(resolved_log_file), exist_ok=True)

            # RotatingFileHandler: Max 10MB, 5 Backups
            fh: RotatingFileHandler = RotatingFileHandler(
                resolved_log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding='utf-8'
            )
            fh.setLevel(level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except OSError as e:
            logger.warning(f"Failed to create file handler: {e}")

    return logger

# Create a default logger instance for easy import
logger: logging.Logger = setup_logger()
