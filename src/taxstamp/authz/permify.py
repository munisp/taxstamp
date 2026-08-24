"""Permify client.

Permify answers relationship questions the platform's role table cannot express, such as
"has this auditor been delegated read access to that manufacturer?". The client is
deliberately thin: one request, one bounded timeout, no retries (an authorisation check
must not amplify load on a struggling engine), and three possible outcomes - allowed,
denied, or *unknown*. Callers must treat unknown as "no decision", never as either
answer, so an outage cannot silently permit or silently break access.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import httpx

from taxstamp.jsontypes import JsonObject

_CHECK_PATH: Final[str] = "/v1/tenants/{tenant}/permissions/check"
#: Permify's enum spelling for an allowed check. Both are accepted because the wire name
#: was shortened between releases; anything else is a denial, never an allow.
_ALLOWED: Final[frozenset[str]] = frozenset({"RESULT_ALLOWED", "CHECK_RESULT_ALLOWED"})
_DENIED: Final[frozenset[str]] = frozenset({"RESULT_DENIED", "CHECK_RESULT_DENIED"})


class CheckOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CheckRequest:
    """A single relationship question."""

    entity_type: str
    entity_id: str
    permission: str
    subject_type: str
    subject_id: str


@dataclass(frozen=True, slots=True)
class PermifyConfig:
    base_url: str
    tenant_id: str
    api_key: str
    timeout_seconds: float
    schema_version: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.tenant_id)


@dataclass(slots=True)
class PermifyClient:
    config: PermifyConfig
    client: httpx.Client | None = None

    def with_client(self, client: httpx.Client) -> PermifyClient:
        return PermifyClient(config=self.config, client=client)

    def check(self, request: CheckRequest) -> CheckOutcome:
        """Ask the engine a single question. Never raises for engine failure."""
        if not self.config.configured:
            return CheckOutcome.UNKNOWN
        url = self.config.base_url.rstrip("/") + _CHECK_PATH.format(tenant=self.config.tenant_id)
        payload: JsonObject = {
            "metadata": {"schema_version": self.config.schema_version, "depth": 20},
            "entity": {"type": request.entity_type, "id": request.entity_id},
            "permission": request.permission,
            "subject": {"type": request.subject_type, "id": request.subject_id},
        }
        headers = {"content-type": "application/json"}
        if self.config.api_key:
            headers["authorization"] = f"Bearer {self.config.api_key}"
        try:
            response = self._send(url, payload, headers)
        except (httpx.HTTPError, OSError):
            return CheckOutcome.UNKNOWN
        return _verdict(response)

    def _send(self, url: str, payload: JsonObject, headers: dict[str, str]) -> httpx.Response:
        if self.client is not None:
            return self.client.post(url, json=payload, headers=headers)
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            return client.post(url, json=payload, headers=headers)


def _verdict(response: httpx.Response) -> CheckOutcome:
    """Read a verdict from a response, or report that there is none.

    Anything that is not a recognised, well-formed positive or negative answer - an
    error status, an unparseable body, an unknown enum member - is "unknown". A caller
    that cannot get an answer must decide what to do about it; guessing here would hide
    an engine or schema fault behind an access decision.
    """
    if response.status_code != 200:
        return CheckOutcome.UNKNOWN
    try:
        body = response.json()
    except ValueError:
        return CheckOutcome.UNKNOWN
    result = body.get("can") if isinstance(body, dict) else None
    if not isinstance(result, str):
        return CheckOutcome.UNKNOWN
    if result in _ALLOWED:
        return CheckOutcome.ALLOWED
    if result in _DENIED:
        return CheckOutcome.DENIED
    return CheckOutcome.UNKNOWN
