"""Domain fixtures created through the real models and migrations."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from taxstamp.enums import KybStatus, LicenceStatus, LicenceType, ProductStatus, RiskTier, Role
from taxstamp.models import Company, Credential, Licence, Principal, Product, Tariff
from taxstamp.money import Money
from taxstamp.security import generate_token, hash_token

EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


@dataclass(frozen=True, slots=True)
class Identity:
    principal_id: uuid.UUID
    subject: str
    token: str
    role: Role
    company_id: uuid.UUID | None


def create_company(
    session: Session,
    *,
    tin: str | None = None,
    kyb: KybStatus = KybStatus.VERIFIED,
    risk: RiskTier = RiskTier.LOW,
    now: dt.datetime = EPOCH,
) -> Company:
    company = Company(
        tin=tin or f"TIN-{uuid.uuid4().hex[:12]}",
        name="Acme Distilleries Ltd",
        kyb_status=kyb.value,
        kyb_verified_at=now if kyb is KybStatus.VERIFIED else None,
        risk_tier=risk.value,
        created_at=now,
    )
    session.add(company)
    session.flush()
    return company


def create_identity(
    session: Session,
    *,
    role: Role,
    api_token_secret: str,
    company_id: uuid.UUID | None = None,
    subject: str | None = None,
    now: dt.datetime = EPOCH,
) -> Identity:
    resolved_subject = subject or f"{role.value}-{uuid.uuid4().hex[:8]}"
    principal = Principal(
        subject=resolved_subject,
        role=role.value,
        company_id=company_id,
        display_name=resolved_subject,
        active=True,
        created_at=now,
    )
    session.add(principal)
    session.flush()
    token = generate_token()
    session.add(
        Credential(
            principal_id=principal.id,
            token_hash=hash_token(token, secret=api_token_secret),
            label="test",
            created_at=now,
        )
    )
    session.flush()
    return Identity(
        principal_id=principal.id,
        subject=resolved_subject,
        token=token,
        role=role,
        company_id=company_id,
    )


def create_licence(
    session: Session,
    *,
    company_id: uuid.UUID,
    licence_type: LicenceType = LicenceType.MANUFACTURER,
    product_categories: tuple[str, ...] = ("alcohol",),
    status: LicenceStatus = LicenceStatus.ACTIVE,
    valid_from: dt.datetime = EPOCH - dt.timedelta(days=365),
    valid_to: dt.datetime | None = None,
    now: dt.datetime = EPOCH,
) -> Licence:
    licence = Licence(
        licence_number=f"LIC-{uuid.uuid4().hex[:10].upper()}",
        company_id=company_id,
        licence_type=licence_type.value,
        product_categories=list(product_categories),
        status=status.value,
        valid_from=valid_from,
        valid_to=valid_to,
        statutory_reference="Test fixture licence (not a legal licence)",
        created_at=now,
    )
    session.add(licence)
    session.flush()
    return licence


def create_product(
    session: Session,
    *,
    company_id: uuid.UUID,
    sku: str | None = None,
    product_category: str = "alcohol",
    pack_size: int = 12,
    intended_market: str = "NG",
    status: ProductStatus = ProductStatus.ACTIVE,
    now: dt.datetime = EPOCH,
) -> Product:
    product = Product(
        company_id=company_id,
        sku=sku or f"SKU-{uuid.uuid4().hex[:8].upper()}",
        brand="Acme Reserve",
        product_category=product_category,
        pack_size=pack_size,
        unit_of_measure="bottle",
        intended_market=intended_market,
        status=status.value,
        withdrawn_at=now if status is ProductStatus.WITHDRAWN else None,
        created_at=now,
    )
    session.add(product)
    session.flush()
    return product


def create_tariff(
    session: Session,
    *,
    product_category: str = "alcohol",
    unit_price_major: str = "12.50",
    currency: str = "NGN",
    vat_bps: int = 750,
    effective_from: dt.datetime = EPOCH - dt.timedelta(days=365),
    now: dt.datetime = EPOCH,
) -> Tariff:
    price = Money.from_major(Decimal(unit_price_major), currency)
    tariff = Tariff(
        product_category=product_category,
        unit_price_minor=price.minor,
        currency=currency,
        vat_bps=vat_bps,
        effective_from=effective_from,
        statutory_reference="Test fixture rate (not a legal rate)",
        created_at=now,
    )
    session.add(tariff)
    session.flush()
    return tariff
