"""Internal fail-closed deadline boundary for synchronous adapter backends."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread


class BoundedCallUnavailable(Exception):
    """A synchronous backend failed, timed out, or was already in flight."""


class BoundedCallTimedOut(BoundedCallUnavailable):
    """A synchronous backend remained in flight beyond its deadline."""


def invoke_bounded[T](
    operation: Callable[[], T],
    *,
    timeout_seconds: float,
    thread_name: str,
    in_flight_lock: Lock | None = None,
) -> T:
    """Return one backend result by the deadline or fail without waiting."""

    if in_flight_lock is not None and not in_flight_lock.acquire(blocking=False):
        raise BoundedCallUnavailable
    finished = Event()
    outputs: list[T] = []
    failed: list[bool] = []

    def invoke() -> None:
        try:
            outputs.append(operation())
        except BaseException:
            failed.append(True)
        finally:
            if in_flight_lock is not None:
                in_flight_lock.release()
            finished.set()

    worker = Thread(target=invoke, name=thread_name, daemon=True)
    try:
        worker.start()
    except BaseException:
        if in_flight_lock is not None:
            in_flight_lock.release()
        raise BoundedCallUnavailable from None
    if not finished.wait(timeout_seconds):
        raise BoundedCallTimedOut
    if failed or len(outputs) != 1:
        raise BoundedCallUnavailable
    return outputs[0]
