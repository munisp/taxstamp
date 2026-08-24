"""Administrative CLI.

Used for bootstrap, credential issuance, reference data, reconciliation and audit
verification. Tokens are printed exactly once and never stored in clear text.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import uuid
from decimal import Decimal

from sqlalchemy import select

from taxstamp.audit import verify_audit_chain
from taxstamp.clock import utcnow
from taxstamp.db import transaction
from taxstamp.enums import KybStatus, LicenceStatus, LicenceType, RiskTier, Role
from taxstamp.models import Company, Credential, Licence, Principal, Tariff
from taxstamp.money import Money
from taxstamp.runtime import build_runtime
from taxstamp.security import generate_token, hash_token
from taxstamp.services.reconciliation import run_reconciliation
from taxstamp.services.registry import assert_tariff_period_free


def _create_principal(args: argparse.Namespace) -> int:
    runtime = build_runtime()
    role = Role(args.role)
    company_id = uuid.UUID(args.company_id) if args.company_id else None
    token = generate_token()
    with transaction(runtime.session_factory) as session:
        existing = session.execute(
            select(Principal).where(Principal.subject == args.subject)
        ).scalar_one_or_none()
        principal = existing or Principal(
            subject=args.subject,
            role=role.value,
            company_id=company_id,
            display_name=args.display_name,
            active=True,
            created_at=utcnow(),
        )
        if existing is None:
            session.add(principal)
            session.flush()
        session.add(
            Credential(
                principal_id=principal.id,
                token_hash=hash_token(token, secret=runtime.settings.api_token_secret),
                label=args.label,
                created_at=utcnow(),
            )
        )
    print(f"principal_subject={args.subject}")  # noqa: T201 - CLI output
    print(f"token={token}")  # noqa: T201 - shown once, stored only as a keyed hash
    runtime.close()
    return 0


def _create_company(args: argparse.Namespace) -> int:
    runtime = build_runtime()
    with transaction(runtime.session_factory) as session:
        company = Company(
            tin=args.tin,
            name=args.name,
            kyb_status=KybStatus(args.kyb_status).value,
            kyb_verified_at=utcnow() if args.kyb_status == KybStatus.VERIFIED.value else None,
            risk_tier=RiskTier(args.risk_tier).value,
            created_at=utcnow(),
        )
        session.add(company)
        session.flush()
        print(f"company_id={company.id}")  # noqa: T201 - CLI output
    runtime.close()
    return 0


def _issue_licence(args: argparse.Namespace) -> int:
    runtime = build_runtime()
    categories = sorted({value.strip() for value in args.product_categories.split(",") if value.strip()})
    with transaction(runtime.session_factory) as session:
        licence = Licence(
            licence_number=args.licence_number,
            company_id=uuid.UUID(args.company_id),
            licence_type=LicenceType(args.licence_type).value,
            product_categories=categories,
            status=LicenceStatus.ACTIVE.value,
            valid_from=dt.datetime.fromisoformat(args.valid_from),
            valid_to=dt.datetime.fromisoformat(args.valid_to) if args.valid_to else None,
            statutory_reference=args.statutory_reference,
            created_at=utcnow(),
        )
        session.add(licence)
        session.flush()
        print(f"licence_id={licence.id}")  # noqa: T201 - CLI output
    runtime.close()
    return 0


def _add_tariff(args: argparse.Namespace) -> int:
    runtime = build_runtime()
    unit_price = Money.from_major(Decimal(args.unit_price_major), args.currency)
    effective_from = dt.datetime.fromisoformat(args.effective_from)
    with transaction(runtime.session_factory) as session:
        assert_tariff_period_free(
            session,
            product_category=args.product_category,
            effective_from=effective_from,
            effective_to=None,
        )
        tariff = Tariff(
            product_category=args.product_category,
            unit_price_minor=unit_price.minor,
            currency=args.currency,
            vat_bps=args.vat_bps,
            effective_from=effective_from,
            statutory_reference=args.statutory_reference,
            created_at=utcnow(),
        )
        session.add(tariff)
        session.flush()
        print(f"tariff_id={tariff.id}")  # noqa: T201 - CLI output
    runtime.close()
    return 0


def _reconcile(_args: argparse.Namespace) -> int:
    runtime = build_runtime()
    with transaction(runtime.session_factory) as session:
        report = run_reconciliation(
            session,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            export_secret=runtime.settings.export_signing_secret,
            transparency_secret=runtime.settings.transparency_signing_secret,
        )
    print(report.as_document())  # noqa: T201 - CLI output
    runtime.close()
    return 0 if report.clean else 2


def _verify_audit(_args: argparse.Namespace) -> int:
    runtime = build_runtime()
    with runtime.session_factory() as session:
        result = verify_audit_chain(session, secret=runtime.settings.audit_chain_secret)
    print(  # noqa: T201 - CLI output
        {
            "intact": result.intact,
            "events_checked": result.events_checked,
            "first_bad_seq": result.first_bad_seq,
            "reason": result.reason,
        }
    )
    runtime.close()
    return 0 if result.intact else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taxstamp", description="Tax stamp administration")
    sub = parser.add_subparsers(dest="command", required=True)

    principal = sub.add_parser("create-principal", help="create a principal and issue a credential")
    principal.add_argument("--subject", required=True)
    principal.add_argument("--role", required=True, choices=[role.value for role in Role])
    principal.add_argument("--display-name", required=True)
    principal.add_argument("--company-id")
    principal.add_argument("--label", default="cli")
    principal.set_defaults(func=_create_principal)

    company = sub.add_parser("create-company", help="register a company")
    company.add_argument("--tin", required=True)
    company.add_argument("--name", required=True)
    company.add_argument(
        "--kyb-status", default=KybStatus.UNVERIFIED.value, choices=[s.value for s in KybStatus]
    )
    company.add_argument("--risk-tier", default=RiskTier.MEDIUM.value, choices=[t.value for t in RiskTier])
    company.set_defaults(func=_create_company)

    licence = sub.add_parser("issue-licence", help="issue an excise licence to a company")
    licence.add_argument("--company-id", required=True)
    licence.add_argument("--licence-number", required=True)
    licence.add_argument("--licence-type", required=True, choices=[kind.value for kind in LicenceType])
    licence.add_argument("--product-categories", required=True, help="comma-separated product categories")
    licence.add_argument("--valid-from", required=True, help="ISO-8601 timestamp with offset")
    licence.add_argument("--valid-to", default=None, help="ISO-8601 timestamp with offset")
    licence.add_argument("--statutory-reference", required=True)
    licence.set_defaults(func=_issue_licence)

    tariff = sub.add_parser("add-tariff", help="add an effective-dated tariff")
    tariff.add_argument("--product-category", required=True)
    tariff.add_argument("--unit-price-major", required=True)
    tariff.add_argument("--currency", default="NGN")
    tariff.add_argument("--vat-bps", type=int, required=True)
    tariff.add_argument("--effective-from", required=True, help="ISO-8601 timestamp with offset")
    tariff.add_argument(
        "--statutory-reference",
        required=True,
        help="citation for the rate, recorded with the tariff for auditability",
    )
    tariff.set_defaults(func=_add_tariff)

    sub.add_parser("reconcile", help="run reconciliation").set_defaults(func=_reconcile)
    sub.add_parser("verify-audit-chain", help="verify the audit hash chain").set_defaults(func=_verify_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    sys.exit(main())
