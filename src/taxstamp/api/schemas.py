"""Request and response models.

Inputs are strict: unknown fields are rejected, strings are bounded, and monetary or
identifier fields are never accepted from the client where the server can derive them.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from taxstamp.enums import ApprovalDecision, ApprovalLevel
from taxstamp.serials import CATEGORY_CODES


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class CreateOrderRequest(StrictModel):
    company_id: uuid.UUID
    product_category: str = Field(min_length=3, max_length=64)
    quantity: int = Field(gt=0, le=5_000_000)
    delivery_state: str = Field(min_length=2, max_length=64)
    delivery_address: str = Field(min_length=10, max_length=500)

    @field_validator("product_category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        if value not in CATEGORY_CODES:
            raise ValueError(f"unsupported product category: {value}")
        return value


class ApprovalRequest(StrictModel):
    level: ApprovalLevel
    decision: ApprovalDecision
    reason: str = Field(min_length=3, max_length=500)


class CancelOrderRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class RemittanceRequest(StrictModel):
    external_reference: str = Field(min_length=6, max_length=128)
    payment_reference: str = Field(min_length=6, max_length=64)
    amount_minor: int = Field(gt=0, le=10**15)
    currency: str = Field(min_length=3, max_length=3)
    value_date: dt.datetime

    @field_validator("value_date")
    @classmethod
    def _aware(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None:
            raise ValueError("value_date must include a timezone offset")
        return value


class ActivateStampsRequest(StrictModel):
    serials: list[str] = Field(min_length=1, max_length=1_000)


class VoidStampsRequest(StrictModel):
    serials: list[str] = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=3, max_length=500)


class InspectionRequest(StrictModel):
    defects_found: int = Field(ge=0, le=10_000)
    defective_serials: list[str] = Field(default_factory=list, max_length=10_000)


class VerifyRequest(StrictModel):
    serial: str = Field(min_length=8, max_length=64)
    secure_code: str = Field(min_length=6, max_length=32)
    device_id: str = Field(min_length=3, max_length=64)
    nonce: str = Field(min_length=8, max_length=64)
    latitude_e7: int | None = Field(default=None, ge=-900_000_000, le=900_000_000)
    longitude_e7: int | None = Field(default=None, ge=-1_800_000_000, le=1_800_000_000)
