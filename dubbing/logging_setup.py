import logging
from datetime import datetime

from . import config


class SecretMaskFilter(logging.Filter):
    """Never let anything that looks like an API key reach a log file or the UI."""

    def filter(self, record):
        from .settings import mask_secrets
        msg = record.getMessage()
        masked = mask_secrets(msg)
        if masked != msg:
            record.msg, record.args = masked, ()
        return True


def setup_logging(extra_handler=None):
    """Log to a file in output/logs (and optionally to the UI). Returns the log file path."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.LOG_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
    logger = logging.getLogger("dubbing")
    logger.setLevel(logging.DEBUG)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    mask = SecretMaskFilter()
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    fh.addFilter(mask)
    logger.addHandler(fh)
    if extra_handler:
        extra_handler.setLevel(logging.INFO)
        extra_handler.addFilter(mask)
        logger.addHandler(extra_handler)
    return path
