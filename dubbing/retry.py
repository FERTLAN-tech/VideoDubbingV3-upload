"""Retry helper for flaky network / API calls."""
import logging
import random
import time

from . import config

log = logging.getLogger("dubbing")


def _is_retryable(exc):
    try:
        import openai
        fatal = (openai.AuthenticationError, openai.PermissionDeniedError,
                 openai.BadRequestError, openai.NotFoundError)
        if isinstance(exc, fatal):
            return False
        if isinstance(exc, openai.RateLimitError) and "insufficient_quota" in str(exc):
            return False
    except ImportError:
        pass
    return not isinstance(exc, (ValueError, TypeError, KeyboardInterrupt))


def with_retries(fn, what, retries=None, base_delay=None):
    retries = retries or config.API_RETRIES
    base_delay = config.RETRY_BASE_DELAY if base_delay is None else base_delay
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - we re-raise below
            if attempt >= retries or not _is_retryable(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            log.warning("%s failed (attempt %d/%d): %s. Retrying in %.0f s.",
                        what, attempt, retries, exc, delay)
            time.sleep(delay)
