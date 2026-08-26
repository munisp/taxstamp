"""Worker process lifecycle is deterministic under a controlled runtime."""

from __future__ import annotations

import datetime as dt
import signal
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from taxstamp.worker import main

pytestmark = pytest.mark.unit


class _StopAfterWait:
    def __init__(self) -> None:
        self.stopped = False
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, timeout: float) -> None:
        self.waits.append(timeout)
        self.stopped = True


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        clock=SimpleNamespace(now=lambda: dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)),
        close=lambda: None,
    )


def test_run_relays_and_executes_periodic_jobs_before_clean_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    close = Mock()
    runtime.close = close
    stop = _StopAfterWait()
    relay = Mock(return_value=SimpleNamespace(claimed=0))
    expire = Mock()
    reconcile = Mock()

    monkeypatch.setattr(main, "build_runtime", lambda: runtime)
    monkeypatch.setattr(main, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(main, "relay_once", relay)
    monkeypatch.setattr(main, "expire_stamps_once", expire)
    monkeypatch.setattr(main, "reconcile_once", reconcile)
    monkeypatch.setattr(main.threading, "current_thread", lambda: object())

    main.run(stop_event=stop)  # type: ignore[arg-type]

    assert relay.call_count == 1
    assert expire.call_count == 1
    assert reconcile.call_count == 1
    assert stop.waits == [main.POLL_INTERVAL_SECONDS]
    close.assert_called_once_with()


def test_run_registers_shutdown_signals_and_closes_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    close = Mock()
    runtime.close = close
    stop = threading.Event()
    handlers: dict[signal.Signals, object] = {}

    monkeypatch.setattr(main, "build_runtime", lambda: runtime)
    monkeypatch.setattr(main, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(main.threading, "current_thread", threading.main_thread)
    monkeypatch.setattr(main.signal, "signal", lambda value, handler: handlers.__setitem__(value, handler))

    def relay_once(_runtime: object, *, worker_id: str) -> SimpleNamespace:
        del worker_id
        handler = handlers[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)
        return SimpleNamespace(claimed=1)

    monkeypatch.setattr(main, "relay_once", relay_once)
    monkeypatch.setattr(main, "expire_stamps_once", Mock())
    monkeypatch.setattr(main, "reconcile_once", Mock())

    main.run(stop_event=stop)

    assert signal.SIGTERM in handlers
    assert signal.SIGINT in handlers
    assert stop.is_set()
    close.assert_called_once_with()
