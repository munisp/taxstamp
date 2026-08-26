# Ledger Boundary

This Rust crate validates a deterministic double-entry transfer intent before a TigerBeetle or equivalent ledger adapter is invoked. It contains no network client, secret, account mapping, or settlement authority. A production adapter must configure the TigerBeetle cluster, map immutable account and ledger codes, submit transfers idempotently, reconcile accepted transfers with PostgreSQL business state, and complete a tested rollback and disaster-recovery process.
