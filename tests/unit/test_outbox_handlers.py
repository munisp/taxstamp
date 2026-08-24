"""Every effect the platform enqueues must have somewhere to go.

An enqueued event with no handler is not a silent no-op: the relay retries it until it
dead-letters, which surfaces as a permanent outbox backlog finding. This walks the
source for ``enqueue(...)`` calls so a new effect cannot be added without a handler.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from taxstamp.worker.handlers import HANDLERS

pytestmark = pytest.mark.unit

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "taxstamp"


def _enqueued_event_types() -> set[str]:
    found: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if isinstance(callee, ast.Attribute):
                name = callee.attr
            elif isinstance(callee, ast.Name):
                name = callee.id
            else:
                continue
            if name != "enqueue":
                continue
            for keyword in node.keywords:
                if keyword.arg == "event_type" and isinstance(keyword.value, ast.Constant):
                    value = keyword.value.value
                    if isinstance(value, str):
                        found.add(value)
    return found


def test_source_enqueues_at_least_the_known_effects() -> None:
    assert {"order.issue_stamps", "trace.event_recorded", "consignment.released"} <= (_enqueued_event_types())


def test_every_enqueued_event_type_has_a_handler() -> None:
    unhandled = sorted(_enqueued_event_types() - set(HANDLERS))
    assert unhandled == [], f"outbox events with no handler would dead-letter: {unhandled}"
