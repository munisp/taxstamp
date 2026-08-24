"""Helpers for driving the HTTP API the way a real client would."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient

from taxstamp.jsontypes import JsonObject
from taxstamp.security import sign_request


def auth(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"authorization": f"Bearer {token}"}
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def signed_headers(body: JsonObject, *, secret: str, now: dt.datetime) -> dict[str, str]:
    return {
        "x-signature": sign_request(body, now, secret=secret),
        "x-timestamp": str(int(now.timestamp())),
        "content-type": "application/json",
    }


def new_key(label: str = "k") -> str:
    return f"{label}-{uuid.uuid4().hex}"


@dataclass(frozen=True, slots=True)
class Remittance:
    external_reference: str
    payment_reference: str
    amount_minor: int
    currency: str
    value_date: dt.datetime

    def body(self) -> JsonObject:
        return {
            "external_reference": self.external_reference,
            "payment_reference": self.payment_reference,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "value_date": self.value_date.isoformat(),
        }


def post_remittance(client: TestClient, remittance: Remittance, *, secret: str, now: dt.datetime) -> object:
    body = remittance.body()
    return client.post(
        "/v1/payments/remittances",
        content=json.dumps(body),
        headers=signed_headers(body, secret=secret, now=now),
    )
