from __future__ import annotations

import threading

_shutdown_requested = threading.Event()


def request_shutdown() -> None:
    _shutdown_requested.set()


def clear_shutdown_request() -> None:
    _shutdown_requested.clear()


def shutdown_requested() -> bool:
    return _shutdown_requested.is_set()
