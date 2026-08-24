"""Retention policy for each class of record the platform holds.

The policy is declared here and published through the API so an auditor can see what the
platform intends to keep and for how long, rather than inferring it from the schema.

Two properties matter more than the durations. First, the classes marked
``destructible=False`` are protected by database triggers that reject UPDATE and DELETE,
so nothing in the platform can quietly shorten their life to satisfy a retention target.
Second, expiry here means *archive*, not erase: reaching the minimum period makes a
record eligible for export to cold storage, and the platform performs no deletion at
all. Erasure requests that collide with a statutory retention duty are therefore
refused rather than partially honoured.
"""

from __future__ import annotations

from dataclasses import dataclass

from taxstamp.jsontypes import JsonObject


@dataclass(frozen=True, slots=True)
class RetentionClass:
    name: str
    tables: tuple[str, ...]
    minimum_years: int
    basis: str
    destructible: bool


#: Retention minimums. The financial and audit classes follow the longest statutory
#: period the platform is expected to face; they are not configurable at runtime.
RETENTION_CLASSES: tuple[RetentionClass, ...] = (
    RetentionClass(
        name="financial",
        tables=("journals", "ledger_entries", "payment_receipts", "receipt_resolutions"),
        minimum_years=10,
        basis="tax and accounting records supporting assessed duty and VAT",
        destructible=False,
    ),
    RetentionClass(
        name="audit",
        tables=("audit_events", "transparency_checkpoints"),
        minimum_years=10,
        basis="tamper-evident record of who did what, and the published commitments to it",
        destructible=False,
    ),
    RetentionClass(
        name="fiscal_marks",
        tables=("stamps", "stamp_events", "stamp_batches", "stamp_dispositions"),
        minimum_years=10,
        basis="serial-level accountability for every mark issued",
        destructible=False,
    ),
    RetentionClass(
        name="traceability",
        tables=("trace_events", "trade_units", "unit_memberships", "facilities"),
        minimum_years=5,
        basis="supply-chain movement history required for track-and-trace obligations",
        destructible=False,
    ),
    RetentionClass(
        name="customs",
        tables=("consignments", "consignment_stamps"),
        minimum_years=10,
        basis="import and duty-suspension records supporting release decisions",
        destructible=False,
    ),
    RetentionClass(
        name="enforcement",
        tables=("anomalies", "verifications"),
        minimum_years=5,
        basis="detection findings and field verifications relied on in enforcement",
        destructible=False,
    ),
    RetentionClass(
        name="licensing",
        tables=("licences", "products", "companies", "principals"),
        minimum_years=10,
        basis="entitlement and master data identifying who was permitted to trade",
        destructible=False,
    ),
    RetentionClass(
        name="operational",
        tables=("idempotency_records", "outbox_messages", "data_exports"),
        minimum_years=1,
        basis="operational plumbing; export records are kept as disclosure evidence",
        destructible=False,
    ),
)


def retention_policy_document(*, anchoring_configured: bool) -> JsonObject:
    """The published policy, including what the platform does not do."""
    return {
        "classes": [
            {
                "name": entry.name,
                "tables": list(entry.tables),
                "minimum_years": entry.minimum_years,
                "basis": entry.basis,
                "database_enforced_append_only": not entry.destructible,
            }
            for entry in RETENTION_CLASSES
        ],
        "expiry_behaviour": "archive_only",
        "erasure_supported": False,
        "erasure_note": (
            "records under a statutory retention duty are never deleted; an erasure "
            "request against them is refused rather than partially applied"
        ),
        "legal_hold": {
            "supported": True,
            "mechanism": (
                "append-only tables plus an operator-declared hold recorded in the audit "
                "log; no automated purge exists that a hold would need to suspend"
            ),
        },
        "portability": {
            "supported": True,
            "endpoint": "/v1/exports/portability",
            "integrity": "canonical-JSON hash plus purpose-separated HMAC signature",
        },
        "external_anchoring_configured": anchoring_configured,
    }
