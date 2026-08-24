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


class LicenceType(StrEnum):
    MANUFACTURER = "manufacturer"
    IMPORTER = "importer"
    DISTRIBUTOR = "distributor"


#: Licence types entitled to order stamps. A distributor moves stamped goods but does
#: not apply stamps, so it cannot procure them.
ORDERING_LICENCE_TYPES: frozenset[LicenceType] = frozenset({LicenceType.MANUFACTURER, LicenceType.IMPORTER})


class LicenceStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class DispositionKind(StrEnum):
    """Why stamps left the usable population without being applied to goods."""

    SPOILED = "spoiled"
    DAMAGED = "damaged"
    DESTROYED = "destroyed"
    RETURNED = "returned"


class ResolutionKind(StrEnum):
    """How a quarantined receipt left the unapplied-receipts account."""

    APPLIED = "applied"
    REFUNDED = "refunded"


class FacilityKind(StrEnum):
    """Where in the supply chain a movement is recorded.

    Facility identification is what makes a movement record meaningful: an event without
    a known origin or destination cannot be checked against anything.
    """

    FACTORY = "factory"
    WAREHOUSE = "warehouse"
    DISTRIBUTION_CENTRE = "distribution_centre"
    RETAIL = "retail"
    PORT = "port"
    FREE_ZONE = "free_zone"
    DUTY_FREE_OUTLET = "duty_free_outlet"
    DESTRUCTION_SITE = "destruction_site"


class TradeUnitLevel(StrEnum):
    CASE = "case"
    PALLET = "pallet"
    CONTAINER = "container"


#: Aggregation order, smallest first. A unit may only contain units one level below it.
TRADE_UNIT_HIERARCHY: tuple[TradeUnitLevel, ...] = (
    TradeUnitLevel.CASE,
    TradeUnitLevel.PALLET,
    TradeUnitLevel.CONTAINER,
)


def child_level(level: TradeUnitLevel) -> TradeUnitLevel | None:
    """The level a unit of ``level`` may contain, or None for the lowest level."""
    index = TRADE_UNIT_HIERARCHY.index(level)
    return None if index == 0 else TRADE_UNIT_HIERARCHY[index - 1]


class TradeUnitStatus(StrEnum):
    CLOSED = "closed"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    EXPORTED = "exported"
    DESTROYED = "destroyed"
    DISAGGREGATED = "disaggregated"


TRADE_UNIT_TRANSITIONS: dict[TradeUnitStatus, frozenset[TradeUnitStatus]] = {
    TradeUnitStatus.CLOSED: frozenset(
        {
            TradeUnitStatus.IN_TRANSIT,
            TradeUnitStatus.EXPORTED,
            TradeUnitStatus.DESTROYED,
            TradeUnitStatus.DISAGGREGATED,
        }
    ),
    TradeUnitStatus.IN_TRANSIT: frozenset(
        {
            TradeUnitStatus.IN_TRANSIT,
            TradeUnitStatus.DELIVERED,
            TradeUnitStatus.EXPORTED,
            TradeUnitStatus.DESTROYED,
        }
    ),
    TradeUnitStatus.DELIVERED: frozenset(
        {
            TradeUnitStatus.IN_TRANSIT,
            TradeUnitStatus.DESTROYED,
            TradeUnitStatus.DISAGGREGATED,
        }
    ),
    TradeUnitStatus.EXPORTED: frozenset(set()),
    TradeUnitStatus.DESTROYED: frozenset(set()),
    TradeUnitStatus.DISAGGREGATED: frozenset(set()),
}


class TraceEventType(StrEnum):
    """The movement events the EU traceability regime requires to be recorded."""

    DISPATCH = "dispatch"
    ARRIVAL = "arrival"
    TRANSLOAD = "transload"
    EXPORT = "export"
    DESTRUCTION = "destruction"


#: Unit status implied by each movement event.
TRACE_EVENT_RESULT: dict[TraceEventType, TradeUnitStatus] = {
    TraceEventType.DISPATCH: TradeUnitStatus.IN_TRANSIT,
    TraceEventType.ARRIVAL: TradeUnitStatus.DELIVERED,
    TraceEventType.TRANSLOAD: TradeUnitStatus.IN_TRANSIT,
    TraceEventType.EXPORT: TradeUnitStatus.EXPORTED,
    TraceEventType.DESTRUCTION: TradeUnitStatus.DESTROYED,
}


class CustomsRegime(StrEnum):
    """Why a consignment is, or is not, liable to carry a domestic excise stamp."""

    IMPORT_DUTY_PAID = "import_duty_paid"
    FREE_ZONE = "free_zone"
    TRANSIT = "transit"
    DUTY_FREE = "duty_free"


#: Regimes whose goods must carry domestic stamps before release into the market.
STAMP_LIABLE_REGIMES: frozenset[CustomsRegime] = frozenset({CustomsRegime.IMPORT_DUTY_PAID})


class ConsignmentStatus(StrEnum):
    DECLARED = "declared"
    STAMPS_LINKED = "stamps_linked"
    RELEASED = "released"
    REJECTED = "rejected"


CONSIGNMENT_TRANSITIONS: dict[ConsignmentStatus, frozenset[ConsignmentStatus]] = {
    ConsignmentStatus.DECLARED: frozenset(
        {
            ConsignmentStatus.STAMPS_LINKED,
            ConsignmentStatus.RELEASED,
            ConsignmentStatus.REJECTED,
        }
    ),
    ConsignmentStatus.STAMPS_LINKED: frozenset({ConsignmentStatus.RELEASED, ConsignmentStatus.REJECTED}),
    ConsignmentStatus.RELEASED: frozenset(set()),
    ConsignmentStatus.REJECTED: frozenset(set()),
}


class AnomalyKind(StrEnum):
    """Deterministic, reason-coded findings from movement and scan geometry."""

    IMPOSSIBLE_TRAVEL = "impossible_travel"
    QUANTITY_NOT_CONSERVED = "quantity_not_conserved"
    DUPLICATE_SCAN_DIVERGENCE = "duplicate_scan_divergence"
    MARKET_DIVERSION = "market_diversion"


class AnomalySeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExportKind(StrEnum):
    PORTABILITY = "portability"
    REGULATOR = "regulator"


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


def assert_trade_unit_transition(current: TradeUnitStatus, target: TradeUnitStatus) -> None:
    if target not in TRADE_UNIT_TRANSITIONS.get(current, frozenset()):
        raise TransitionError(f"trade_unit: illegal transition {current.value} -> {target.value}")


def assert_consignment_transition(current: ConsignmentStatus, target: ConsignmentStatus) -> None:
    if target not in CONSIGNMENT_TRANSITIONS.get(current, frozenset()):
        raise TransitionError(f"consignment: illegal transition {current.value} -> {target.value}")
