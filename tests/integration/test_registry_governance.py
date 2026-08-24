"""Tariff governance and the licensing/accountability reconciliation checks."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.config import Settings
from taxstamp.enums import LicenceStatus, Role, StampStatus
from taxstamp.errors import Conflict
from taxstamp.models import Licence, Order, Stamp, StampBatch, StampDisposition
from taxstamp.services.reconciliation import run_reconciliation
from taxstamp.services.registry import assert_tariff_period_free, overlapping_tariffs
from tests.support.factories import create_company, create_identity, create_tariff
from tests.support.tenant import Tenant

pytestmark = pytest.mark.integration
JANUARY = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
JULY = dt.datetime(2027, 7, 1, tzinfo=dt.UTC)


def _counts(session: Session, now: dt.datetime) -> dict[str, int]:
    return run_reconciliation(session, now=now, audit_secret="s" * 32).counts_by_kind()


def test_a_tariff_overlapping_an_open_ended_rate_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        create_tariff(session, product_category="beverages", effective_from=JANUARY)
        session.commit()
        with pytest.raises(Conflict):
            assert_tariff_period_free(
                session, product_category="beverages", effective_from=JULY, effective_to=None
            )
        # A different category is unaffected by the beverages rate.
        assert_tariff_period_free(
            session, product_category="pharmaceuticals", effective_from=JULY, effective_to=None
        )


def test_overlapping_tariffs_are_reported_once_per_pair(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        first = create_tariff(session, product_category="beverages", effective_from=JANUARY)
        second = create_tariff(session, product_category="beverages", effective_from=JULY)
        session.commit()
        pairs = overlapping_tariffs(session)
        assert [(row[1], row[2]) for row in pairs] == [(first.id, second.id)]
        assert _counts(session, JULY)["overlapping_tariff"] == 1


def test_a_suspended_licence_behind_an_in_flight_order_is_reported(
    session_factory: sessionmaker[Session], tenant: Tenant
) -> None:
    with session_factory() as session:
        order = Order(
            order_ref="ORD-2027-RECON0001",
            company_id=tenant.company.id,
            submitted_by=tenant.requester.principal_id,
            product_category="alcohol",
            product_id=tenant.product.id,
            licence_id=tenant.licence.id,
            quantity=10,
            tariff_id=create_tariff(session, product_category="tobacco").id,
            unit_price_minor=1_250,
            subtotal_minor=12_500,
            vat_bps=750,
            vat_minor=938,
            total_minor=13_438,
            currency="NGN",
            status="awaiting_payment",
            delivery_state="Lagos",
            delivery_address="12 Marina Road, Lagos Island, Lagos",
            risk_tier="low",
            created_at=JANUARY,
            updated_at=JANUARY,
        )
        session.add(order)
        licence = session.get(Licence, tenant.licence.id)
        assert licence is not None
        licence.status = LicenceStatus.SUSPENDED.value
        session.commit()
        assert _counts(session, JULY)["order_without_effective_licence"] == 1

        licence.status = LicenceStatus.ACTIVE.value
        session.commit()
        assert _counts(session, JULY)["order_without_effective_licence"] == 0


def test_a_disposition_whose_stamps_are_live_is_reported(
    session_factory: sessionmaker[Session], settings: Settings, tenant: Tenant
) -> None:
    with session_factory() as session:
        company = create_company(session)
        requester = create_identity(
            session,
            role=Role.REQUESTER,
            api_token_secret=settings.api_token_secret,
            company_id=company.id,
        )
        order = Order(
            order_ref="ORD-2027-RECON0002",
            company_id=company.id,
            submitted_by=requester.principal_id,
            product_category="alcohol",
            licence_id=None,
            quantity=1,
            tariff_id=create_tariff(session, product_category="pharmaceuticals").id,
            unit_price_minor=1_250,
            subtotal_minor=1_250,
            vat_bps=750,
            vat_minor=94,
            total_minor=1_344,
            currency="NGN",
            status="issued",
            delivery_state="Lagos",
            delivery_address="12 Marina Road, Lagos Island, Lagos",
            risk_tier="low",
            created_at=JANUARY,
            updated_at=JANUARY,
        )
        session.add(order)
        session.flush()
        batch = StampBatch(
            order_id=order.id,
            requested_count=1,
            issued_count=1,
            status="issued",
            completed_at=JANUARY,
            created_at=JANUARY,
        )
        session.add(batch)
        session.flush()
        serial = "NG-ALC-2027-000000001-0"
        session.add(
            Stamp(
                batch_id=batch.id,
                order_id=order.id,
                company_id=company.id,
                product_category="alcohol",
                serial=serial,
                secure_code_hash="0" * 64,
                status=StampStatus.ISSUED.value,
                issued_at=JANUARY,
                expires_at=JULY,
            )
        )
        session.add(
            StampDisposition(
                batch_id=batch.id,
                kind="destroyed",
                stamp_count=1,
                serials=[serial],
                reason="declared destroyed but never voided",
                evidence_reference="DEST-CERT-9999",
                declared_by=tenant.operator.principal_id,
                created_at=JANUARY,
            )
        )
        session.commit()

        counts = _counts(session, JANUARY)
        assert counts["disposition_not_voided"] == 1
        # An order that already completed issuance needs no licence decision, so a
        # pre-licensing order is not reported as an in-flight licensing problem.
        assert counts["order_without_effective_licence"] == 0
