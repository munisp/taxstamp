"""Reconcile reviewed TigerBeetle and Mojaloop sandbox exports against Taxstamp."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from taxstamp.config import get_settings
from taxstamp.db import transaction
from taxstamp.runtime import build_runtime
from taxstamp.services.external_settlement_reconciliation import SettlementProvider, parse_snapshot
from taxstamp.services.reconciliation import run_reconciliation


def _snapshot(path: Path, provider: SettlementProvider):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return parse_snapshot(provider, document)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tigerbeetle-snapshot", type=Path, required=True)
    parser.add_argument("--mojaloop-snapshot", type=Path, required=True)
    args = parser.parse_args()

    settings = get_settings()
    runtime = build_runtime(settings)
    snapshots = _snapshot(args.tigerbeetle_snapshot, SettlementProvider.TIGERBEETLE) + _snapshot(
        args.mojaloop_snapshot, SettlementProvider.MOJALOOP
    )
    try:
        with transaction(runtime.session_factory) as session:
            report = run_reconciliation(
                session,
                now=dt.datetime.now(tz=dt.UTC),
                audit_secret=settings.audit_chain_secret,
                external_settlement_snapshots=snapshots,
                external_settlement_providers=(
                    SettlementProvider.TIGERBEETLE,
                    SettlementProvider.MOJALOOP,
                ),
            )
        print(json.dumps(report.as_document(), sort_keys=True))
        return 0 if report.clean else 2
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
