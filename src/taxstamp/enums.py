"""Authoritative state machines and enumerations.

Every transition allowed by the platform is listed here. Services consult
``assert_transition`` so that an illegal transition cannot be reached from any
entry point, and the database mirrors the values with CHECK constraints.
"""

from __future__ import annotations

from enum import StrEnum


class TransitionError(ValueError):
    """Raised when a state transition is not permitted."""


class OrderStatus(StrEnum):
    SUBMITTED = "submitted"
    COMPLIANCE_PENDING = "compliance_pending"
    COMPLIANCE_REJECTED = "compliance_rejected"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    ISSUING = "issuing"
    ISSUED = "issued"
    CANCELLED = "cancelled"


ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.SUBMITTED: frozenset(
        {OrderStatus.COMPLIANCE_PENDING, OrderStatus.AWAITING_APPROVAL, OrderStatus.CANCELLED}
    ),
    OrderStatus.COMPLIANCE_PENDING: frozenset(
        {OrderStatus.AWAITING_APPROVAL, OrderStatus.COMPLIANCE_REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.COMPLIANCE_REJECTED: frozenset({OrderStatus.CANCELLED}),
    OrderStatus.AWAITING_APPROVAL: frozenset(
        {OrderStatus.APPROVED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.APPROVED: frozenset({OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED}),
    OrderStatus.REJECTED: frozenset(set()),
    OrderStatus.AWAITING_PAYMENT: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.ISSUING}),
    OrderStatus.ISSUING: frozenset({OrderStatus.ISSUED}),
    OrderStatus.ISSUED: frozenset(set()),
    OrderStatus.CANCELLED: frozenset(set()),
}


class StampStatus(StrEnum):
    ISSUED = "issued"
    ACTIVE = "active"
    VOID = "void"
    EXPIRED = "expired"


STAMP_TRANSITIONS: dict[StampStatus, frozenset[StampStatus]] = {
    StampStatus.ISSUED: frozenset({StampStatus.ACTIVE, StampStatus.VOID, StampStatus.EXPIRED}),
    StampStatus.ACTIVE: frozenset({StampStatus.VOID, StampStatus.EXPIRED}),
    StampStatus.VOID: frozenset(set()),
    StampStatus.EXPIRED: frozenset({StampStatus.VOID}),
}


class BatchStatus(StrEnum):
    PENDING = "pending"
    ISSUING = "issuing"
    ISSUED = "issued"
    INSPECTION_FAILED = "inspection_failed"


class PaymentIntentStatus(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    SETTLED = "settled"
    MISMATCHED = "mismatched"
    CANCELLED = "cancelled"


class ReceiptStatus(StrEnum):
    MATCHED = "matched"
    AMOUNT_MISMATCH = "amount_mismatch"
    UNKNOWN_REFERENCE = "unknown_reference"
    ORDER_NOT_PAYABLE = "order_not_payable"
    DUPLICATE = "duplicate"


class ApprovalLevel(StrEnum):
    ANALYST = "analyst"
    SUPERVISOR = "supervisor"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


REQUIRED_APPROVALS: dict[RiskTier, tuple[ApprovalLevel, ...]] = {
    RiskTier.LOW: (ApprovalLevel.ANALYST,),
    RiskTier.MEDIUM: (ApprovalLevel.ANALYST, ApprovalLevel.SUPERVISOR),
    RiskTier.HIGH: (ApprovalLevel.ANALYST, ApprovalLevel.SUPERVISOR),
}


class VerificationOutcome(StrEnum):
    VALID = "valid"
    UNKNOWN_SERIAL = "unknown_serial"
    SECURE_CODE_MISMATCH = "secure_code_mismatch"
    NOT_ACTIVE = "not_active"
    VOID = "void"
    EXPIRED = "expired"
    VELOCITY_SUSPECT = "velocity_suspect"


class Role(StrEnum):
    REQUESTER = "requester"
    ANALYST = "analyst"
    SUPERVISOR = "supervisor"
    TREASURY = "treasury"
    OPERATOR = "operator"
    AUDITOR = "auditor"
    ADMIN = "admin"
    DEVICE = "device"


class KybStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    SUSPENDED = "suspended"


def assert_transition(
    kind: str,
    table: dict[OrderStatus, frozenset[OrderStatus]] | dict[StampStatus, frozenset[StampStatus]],
    current: OrderStatus | StampStatus,
    target: OrderStatus | StampStatus,
) -> None:
    allowed = table.get(current, frozenset())  # type: ignore[arg-type]
    if target not in allowed:
        raise TransitionError(f"{kind}: illegal transition {current.value} -> {target.value}")


def assert_order_transition(current: OrderStatus, target: OrderStatus) -> None:
    assert_transition("order", ORDER_TRANSITIONS, current, target)


def assert_stamp_transition(current: StampStatus, target: StampStatus) -> None:
    assert_transition("stamp", STAMP_TRANSITIONS, current, target)
