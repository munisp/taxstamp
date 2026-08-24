"""Single-transaction idempotent execution.

The claim, the domain effect and the stored response commit together. A crash therefore
leaves neither a half-applied effect nor a poisoned key, and a concurrent duplicate is
rejected with 409 rather than executed twice.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from taxstamp import idempotency
from taxstamp.db import transaction
from taxstamp.jsontypes import JsonObject
from taxstamp.runtime import Runtime
from taxstamp.services.context import Actor


def run_idempotent(
    runtime: Runtime,
    *,
    scope: str,
    key: str,
    actor: Actor,
    payload: JsonObject,
    status: int,
    work: Callable[[Session], JsonObject],
) -> tuple[int, JsonObject]:
    from taxstamp.api.deps import request_fingerprint

    fingerprint = request_fingerprint(payload)
    with transaction(runtime.session_factory) as session:
        now = runtime.clock.now()
        replay = idempotency.claim(
            session,
            scope=scope,
            key=key,
            principal_id=actor.principal_id,
            request_hash=fingerprint,
            now=now,
            ttl_seconds=runtime.settings.idempotency_ttl_seconds,
        )
        if replay is not None:
            return replay.status, replay.body
        body = work(session)
        idempotency.complete(session, scope=scope, key=key, status=status, body=body, now=now)
        return status, body
