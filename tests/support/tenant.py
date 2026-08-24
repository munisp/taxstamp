"""A full tenant, ready for lifecycle tests, created through the real schema."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.config import Settings
from taxstamp.enums import Role
from taxstamp.models import Company, Licence, Product
from tests.support.factories import (
    Identity,
    create_company,
    create_identity,
    create_licence,
    create_product,
    create_tariff,
)


@dataclass(frozen=True, slots=True)
class Tenant:
    company: Company
    licence: Licence
    product: Product
    requester: Identity
    analyst: Identity
    supervisor: Identity
    operator: Identity
    treasury: Identity
    admin: Identity
    device: Identity
    unit_price_minor: int
    vat_bps: int


@pytest.fixture
def tenant(session_factory: sessionmaker[Session], settings: Settings) -> Tenant:
    secret = settings.api_token_secret
    now = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)
    with session_factory() as session:
        company = create_company(session, now=now)
        tariff = create_tariff(session, now=now)
        licence = create_licence(session, company_id=company.id, now=now)
        product = create_product(session, company_id=company.id, now=now)
        tenant = Tenant(
            company=company,
            licence=licence,
            product=product,
            requester=create_identity(
                session, role=Role.REQUESTER, api_token_secret=secret, company_id=company.id, now=now
            ),
            analyst=create_identity(session, role=Role.ANALYST, api_token_secret=secret, now=now),
            supervisor=create_identity(session, role=Role.SUPERVISOR, api_token_secret=secret, now=now),
            operator=create_identity(session, role=Role.OPERATOR, api_token_secret=secret, now=now),
            treasury=create_identity(session, role=Role.TREASURY, api_token_secret=secret, now=now),
            admin=create_identity(session, role=Role.ADMIN, api_token_secret=secret, now=now),
            device=create_identity(session, role=Role.DEVICE, api_token_secret=secret, now=now),
            unit_price_minor=tariff.unit_price_minor,
            vat_bps=tariff.vat_bps,
        )
        session.commit()
        session.refresh(company)
        session.refresh(licence)
        session.refresh(product)
        session.expunge_all()
        return tenant
