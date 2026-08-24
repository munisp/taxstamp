"""Double-entry posting helpers.

Only balanced journals can be posted: the helper refuses in Python and the database
rejects an unbalanced journal at COMMIT via a deferred constraint trigger, so no code
path (including a future one) can create money.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.errors import IllegalState
from taxstamp.models import Journal, LedgerEntry
from taxstamp.money import Money


class Account:
    """Chart of accounts. Collections are an asset; duty payable is a liability."""

    BANK_COLLECTIONS = "asset:bank_collections"
    DUTY_PAYABLE = "liability:duty_payable"
    VAT_PAYABLE = "liability:vat_payable"
    UNAPPLIED_RECEIPTS = "liability:unapplied_receipts"
    DUTY_REVENUE = "revenue:excise_duty"


@dataclass(frozen=True, slots=True)
class Posting:
    account: str
    direction: str
    amount: Money

    def __post_init__(self) -> None:
        if self.direction not in ("debit", "credit"):
            raise IllegalState(f"invalid posting direction: {self.direction!r}")
        if self.amount.minor <= 0:
            raise IllegalState("posting amount must be positive")


def post_journal(
    session: Session,
    *,
    reference: str,
    kind: str,
    postings: list[Posting],
    now: dt.datetime,
    order_id: uuid.UUID | None = None,
    payment_receipt_id: uuid.UUID | None = None,
    reverses_journal_id: uuid.UUID | None = None,
) -> Journal:
    if len(postings) < 2:
        raise IllegalState("a journal requires at least two postings")
    currencies = {posting.amount.currency for posting in postings}
    if len(currencies) != 1:
        raise IllegalState("a journal cannot mix currencies")
    debits = sum(p.amount.minor for p in postings if p.direction == "debit")
    credits = sum(p.amount.minor for p in postings if p.direction == "credit")
    if debits != credits:
        raise IllegalState(f"journal is unbalanced: debits={debits} credits={credits}")

    currency = currencies.pop()
    journal = Journal(
        reference=reference,
        kind=kind,
        currency=currency,
        order_id=order_id,
        payment_receipt_id=payment_receipt_id,
        reverses_journal_id=reverses_journal_id,
        created_at=now,
    )
    session.add(journal)
    session.flush()
    for posting in postings:
        session.add(
            LedgerEntry(
                journal_id=journal.id,
                account=posting.account,
                direction=posting.direction,
                amount_minor=posting.amount.minor,
                currency=posting.amount.currency,
                created_at=now,
            )
        )
    session.flush()
    return journal


def account_balance(session: Session, account: str, currency: str) -> Money:
    """Debit-positive balance of an account."""
    debits = session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            LedgerEntry.account == account,
            LedgerEntry.currency == currency,
            LedgerEntry.direction == "debit",
        )
    ).scalar_one()
    credits = session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            LedgerEntry.account == account,
            LedgerEntry.currency == currency,
            LedgerEntry.direction == "credit",
        )
    ).scalar_one()
    return Money(int(debits) - int(credits), currency)


def unbalanced_journals(session: Session) -> list[tuple[uuid.UUID, int, int]]:
    """Journals whose debits and credits differ. Must always be empty."""
    debit_sum = func.coalesce(
        func.sum(func.coalesce(LedgerEntry.amount_minor, 0)).filter(LedgerEntry.direction == "debit"), 0
    )
    credit_sum = func.coalesce(
        func.sum(func.coalesce(LedgerEntry.amount_minor, 0)).filter(LedgerEntry.direction == "credit"), 0
    )
    rows = session.execute(
        select(LedgerEntry.journal_id, debit_sum, credit_sum)
        .group_by(LedgerEntry.journal_id)
        .having(debit_sum != credit_sum)
    ).all()
    return [(row[0], int(row[1]), int(row[2])) for row in rows]
