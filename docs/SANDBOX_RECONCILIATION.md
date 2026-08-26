# TigerBeetle and Mojaloop Sandbox Reconciliation

The repository now provides a **snapshot-driven, fail-closed** sandbox reconciliation workflow. It deliberately does not create financial transfers, submit payment instructions, or auto-correct mismatches. Instead, it accepts reviewed exports from each sandbox, compares them with locally settled payment intents, writes the resulting reconciliation evidence to the database, and returns a non-zero status when findings exist.

## Required snapshot shape

Each provider export is a JSON object with a `settlements` array. Every entry must include a Taxstamp payment `reference`, a provider `external_id`, positive `amount_minor`, three-letter `currency`, and a state of `settled`, `pending`, or `failed`.

```json
{
  "settlements": [
    {
      "reference": "PAY-EXAMPLE-001",
      "external_id": "sandbox-transfer-001",
      "amount_minor": 12500,
      "currency": "NGN",
      "state": "settled"
    }
  ]
}
```

## Controlled workflow

1. Export settlement records from the TigerBeetle and Mojaloop sandboxes using their approved authenticated tools.
2. Preserve those exports as evidence in the organisation’s controlled storage; do not add them to source control.
3. Run `python scripts/reconcile_sandbox.py --tigerbeetle-snapshot <file> --mojaloop-snapshot <file>` with a controlled Taxstamp configuration.
4. Treat any finding as a release gate. Investigate the external provider and local audit trail; do not repair balances automatically.

The next external prerequisite is sandbox access: TigerBeetle cluster address and account/ledger mapping, plus Mojaloop participant onboarding, client certificates, callback verification material, and a settlement-report export procedure.
