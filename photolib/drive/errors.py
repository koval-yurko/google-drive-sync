"""Drive error classification and retry policy."""

from __future__ import annotations

import functools
import random
import time
from typing import Callable, TypeVar

import httpx

MAX_ATTEMPTS = 5
BASE_DELAY = 0.5
MAX_DELAY = 30.0

T = TypeVar("T")


class DriveError(Exception):
    """Base class for Drive API failures."""


class NotFoundError(DriveError):
    """The requested file or folder does not exist."""


class RateLimitedError(DriveError):
    """Drive asked us to slow down."""


class TransientError(DriveError):
    """A server-side failure that is worth retrying."""


def raise_for_response(response: httpx.Response) -> None:
    """Translate an unsuccessful HTTP response into a typed error."""
    if response.is_success:
        return
    try:
        message = response.json()["error"]["message"]
    except Exception:
        message = response.text[:200]

    status = response.status_code
    if status == 404:
        raise NotFoundError(message)
    if status == 429 or (status == 403 and "rate" in message.lower()):
        raise RateLimitedError(message)
    if status >= 500:
        raise TransientError(f"{status}: {message}")
    raise DriveError(f"{status}: {message}")


def retry(fn: Callable[..., T]) -> Callable[..., T]:
    """Retry rate-limit and transient failures with exponential backoff."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> T:
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return fn(*args, **kwargs)
            except (RateLimitedError, TransientError) as exc:
                last = exc
                if attempt == MAX_ATTEMPTS - 1:
                    break
                delay = min(BASE_DELAY * (2**attempt), MAX_DELAY)
                time.sleep(delay + random.uniform(0, delay * 0.25))
        assert last is not None
        raise last

    return wrapper
