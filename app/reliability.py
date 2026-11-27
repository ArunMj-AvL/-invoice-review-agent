"""Timeout helper used by the graph around every model-backed adapter call."""

import concurrent.futures
from typing import Any, Callable

from app.errors import AdapterTimeout


def call_with_timeout(fn: Callable[..., Any], *args: Any, timeout: float, **kwargs: Any) -> Any:
    """Run ``fn`` and raise AdapterTimeout if it does not finish in ``timeout`` seconds.

    The worker thread cannot be killed, but the graph stops waiting for it, so a
    hung model call can never block the workflow forever.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        raise AdapterTimeout(f"call exceeded {timeout} seconds") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
