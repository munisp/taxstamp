"""Worker entrypoint.

Runs the outbox relay continuously plus periodic expiry and reconciliation passes, and
shuts down cleanly on SIGTERM so a rolling deployment cannot interrupt a delivery
mid-transaction.
"""

from __future__ import annotations

import os
import signal
import socket
import threading
import types

import structlog

from taxstamp.observability import configure_logging
from taxstamp.runtime import build_runtime
from taxstamp.worker.relay import expire_stamps_once, next_due, reconcile_once, relay_once

logger = structlog.get_logger(__name__)
POLL_INTERVAL_SECONDS = 1.0
EXPIRY_INTERVAL_SECONDS = 300
RECONCILE_INTERVAL_SECONDS = 900


def run(stop_event: threading.Event | None = None) -> None:
    configure_logging(service="taxstamp-worker", revision=os.environ.get("TAXSTAMP_REVISION", "unknown"))
    runtime = build_runtime()
    stop = stop_event or threading.Event()

    def _handle_signal(signum: int, frame: types.FrameType | None) -> None:
        del frame
        logger.info("worker_stopping", signal=signum)
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    logger.info("worker_started", worker_id=worker_id)
    last_expiry = None
    last_reconcile = None
    try:
        while not stop.is_set():
            stats = relay_once(runtime, worker_id=worker_id)
            now = runtime.clock.now()
            if next_due(last_expiry, now, EXPIRY_INTERVAL_SECONDS):
                expire_stamps_once(runtime)
                last_expiry = now
            if next_due(last_reconcile, now, RECONCILE_INTERVAL_SECONDS):
                reconcile_once(runtime)
                last_reconcile = now
            if stats.claimed == 0:
                stop.wait(POLL_INTERVAL_SECONDS)
    finally:
        runtime.close()
        logger.info("worker_stopped", worker_id=worker_id)


def main() -> None:
    run()


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    main()
