import logging
import sys
import os

def setup_logger(name: str = "ImageSorter", log_file: str = "imagesorter.log", level: int = logging.INFO) -> logging.Logger:
    """
    Configures and returns a robust logger with both console and file handlers.

    Args:
        name (str): The name of the logger.
        log_file (str): The path to the log file.
        level (int): The logging level (e.g., logging.INFO, logging.DEBUG).

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding handlers multiple times if the logger is already set up
    if not logger.handlers:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
        )

        # Console Handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # File Handler
        try:
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except OSError as e:
            logger.warning(f"Failed to create file handler for {log_file}: {e}")

    return logger

# Create a default logger instance for easy import
logger = setup_logger()
