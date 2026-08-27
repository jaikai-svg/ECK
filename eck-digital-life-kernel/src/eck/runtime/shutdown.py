from __future__ import annotations

import threading

_shutdown_requested = threading.Event()
_restart_requested = threading.Event()


def request_shutdown() -> None:
    _shutdown_requested.set()


def request_restart() -> None:
    _restart_requested.set()
    _shutdown_requested.set()


def clear_shutdown_request() -> None:
    _shutdown_requested.clear()


def shutdown_requested() -> bool:
    return _shutdown_requested.is_set()


def restart_requested() -> bool:
    return _restart_requested.is_set()


def clear_restart_request() -> None:
    _restart_requested.clear()
