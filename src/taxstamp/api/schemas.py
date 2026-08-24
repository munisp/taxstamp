"""Request and response models.

Inputs are strict: unknown fields are rejected, strings are bounded, and monetary or
identifier fields are never accepted from the client where the server can derive them.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from taxstamp.enums import (
    ApprovalDecision,
    ApprovalLevel,
    DispositionKind,
    LicenceStatus,
    LicenceType,
)
from taxstamp.serials import CATEGORY_CODES


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class IssueLicenceRequest(StrictModel):
    company_id: uuid.UUID
    licence_number: str = Field(min_length=4, max_length=64)
    licence_type: LicenceType
    product_categories: list[str] = Field(min_length=1, max_length=32)
    valid_from: dt.datetime
    valid_to: dt.datetime | None = None
    statutory_reference: str = Field(min_length=3, max_length=255)

    @field_validator("product_categories")
    @classmethod
    def _known_categories(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(CATEGORY_CODES))
        if unknown:
            raise ValueError(f"unsupported product categories: {', '.join(unknown)}")
        return value

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _aware_validity(cls, value: dt.datetime | None) -> dt.datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("validity timestamps must include a timezone offset")
        return value


class LicenceStatusRequest(StrictModel):
    status: LicenceStatus
    reason: str = Field(min_length=3, max_length=500)


class RegisterProductRequest(StrictModel):
    company_id: uuid.UUID
    sku: str = Field(min_length=2, max_length=64)
    brand: str = Field(min_length=2, max_length=128)
    product_category: str = Field(min_length=3, max_length=64)
    pack_size: int = Field(gt=0, le=100_000)
    unit_of_measure: str = Field(min_length=1, max_length=16)
    intended_market: str = Field(min_length=2, max_length=32)

    @field_validator("product_category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        if value not in CATEGORY_CODES:
            raise ValueError(f"unsupported product category: {value}")
        return value


class CreateOrderRequest(StrictModel):
    company_id: uuid.UUID
    product_category: str | None = Field(default=None, min_length=3, max_length=64)
    product_id: uuid.UUID | None = None
    quantity: int = Field(gt=0, le=5_000_000)
    delivery_state: str = Field(min_length=2, max_length=64)
    delivery_address: str = Field(min_length=10, max_length=500)

    @field_validator("product_category")
    @classmethod
    def _known_category(cls, value: str | None) -> str | None:
        if value is not None and value not in CATEGORY_CODES:
            raise ValueError(f"unsupported product category: {value}")
        return value

    @model_validator(mode="after")
    def _exactly_one_product_selector(self) -> CreateOrderRequest:
        if (self.product_id is None) == (self.product_category is None):
            raise ValueError("provide exactly one of product_id or product_category")
        return self


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


class DispositionRequest(StrictModel):
    kind: DispositionKind
    serials: list[str] = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=3, max_length=500)
    evidence_reference: str = Field(min_length=3, max_length=128)


class ApplyReceiptRequest(StrictModel):
    order_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)


class RefundReceiptRequest(StrictModel):
    beneficiary_reference: str = Field(min_length=3, max_length=128)
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
