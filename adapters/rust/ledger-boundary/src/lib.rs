//! Validated, deterministic boundary values for a transaction-plane ledger.
//! This crate intentionally contains no client or credential. A deployment adapter
//! must map accepted intents to TigerBeetle accounts/transfers and reconcile them.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransferIntent {
    pub transfer_id: String,
    pub debit_account_id: String,
    pub credit_account_id: String,
    pub amount_minor: u128,
    pub ledger_code: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransferError {
    EmptyIdentifier,
    IdenticalAccounts,
    ZeroAmount,
    ZeroLedgerCode,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObservedTransfer {
    pub transfer_id: String,
    pub debit_account_id: String,
    pub credit_account_id: String,
    pub amount_minor: u128,
    pub ledger_code: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CreateTransferResult {
    Created,
    Exists,
    Rejected,
}

pub trait TransferClient {
    type Error;

    fn lookup_transfer(
        &mut self,
        transfer_id: &str,
    ) -> Result<Option<ObservedTransfer>, Self::Error>;
    fn create_transfer(
        &mut self,
        intent: &TransferIntent,
    ) -> Result<CreateTransferResult, Self::Error>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubmitOutcome {
    Confirmed,
    Rejected,
}

#[derive(Debug, PartialEq, Eq)]
pub enum SubmitError<E> {
    InvalidIntent(TransferError),
    Client(E),
    MissingAfterCreate,
    ExistingTransferMismatch,
}

impl TransferIntent {
    /// Validate before any external ledger call. Callers must treat the transfer ID
    /// as an idempotency key and reconcile the accepted transfer asynchronously.
    pub fn validate(&self) -> Result<(), TransferError> {
        if self.transfer_id.trim().is_empty()
            || self.debit_account_id.trim().is_empty()
            || self.credit_account_id.trim().is_empty()
        {
            return Err(TransferError::EmptyIdentifier);
        }
        if self.debit_account_id == self.credit_account_id {
            return Err(TransferError::IdenticalAccounts);
        }
        if self.amount_minor == 0 {
            return Err(TransferError::ZeroAmount);
        }
        if self.ledger_code == 0 {
            return Err(TransferError::ZeroLedgerCode);
        }
        Ok(())
    }
}

fn matches_intent(intent: &TransferIntent, observed: &ObservedTransfer) -> bool {
    intent.transfer_id == observed.transfer_id
        && intent.debit_account_id == observed.debit_account_id
        && intent.credit_account_id == observed.credit_account_id
        && intent.amount_minor == observed.amount_minor
        && intent.ledger_code == observed.ledger_code
}

/// Lookup before retrying a create. A lost response is not evidence that no transfer
/// exists, so the same durable ID is always looked up before a create is attempted.
pub fn submit_lookup_before_retry<C: TransferClient>(
    client: &mut C,
    intent: &TransferIntent,
) -> Result<SubmitOutcome, SubmitError<C::Error>> {
    intent.validate().map_err(SubmitError::InvalidIntent)?;
    if let Some(observed) = client
        .lookup_transfer(&intent.transfer_id)
        .map_err(SubmitError::Client)?
    {
        return if matches_intent(intent, &observed) {
            Ok(SubmitOutcome::Confirmed)
        } else {
            Err(SubmitError::ExistingTransferMismatch)
        };
    }
    match client
        .create_transfer(intent)
        .map_err(SubmitError::Client)?
    {
        CreateTransferResult::Rejected => Ok(SubmitOutcome::Rejected),
        CreateTransferResult::Created | CreateTransferResult::Exists => {
            let observed = client
                .lookup_transfer(&intent.transfer_id)
                .map_err(SubmitError::Client)?
                .ok_or(SubmitError::MissingAfterCreate)?;
            if matches_intent(intent, &observed) {
                Ok(SubmitOutcome::Confirmed)
            } else {
                Err(SubmitError::ExistingTransferMismatch)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid() -> TransferIntent {
        TransferIntent {
            transfer_id: "settlement-0001".into(),
            debit_account_id: "payer-clearing".into(),
            credit_account_id: "tax-authority".into(),
            amount_minor: 12_500,
            ledger_code: 566,
        }
    }

    fn observed_from(intent: &TransferIntent) -> ObservedTransfer {
        ObservedTransfer {
            transfer_id: intent.transfer_id.clone(),
            debit_account_id: intent.debit_account_id.clone(),
            credit_account_id: intent.credit_account_id.clone(),
            amount_minor: intent.amount_minor,
            ledger_code: intent.ledger_code,
        }
    }

    #[test]
    fn validates_a_cross_account_transfer() {
        assert_eq!(valid().validate(), Ok(()));
    }

    #[test]
    fn rejects_non_transfer_or_non_financial_intents() {
        let mut intent = valid();
        intent.amount_minor = 0;
        assert_eq!(intent.validate(), Err(TransferError::ZeroAmount));
        intent.amount_minor = 1;
        intent.credit_account_id = intent.debit_account_id.clone();
        assert_eq!(intent.validate(), Err(TransferError::IdenticalAccounts));
    }

    struct MemoryClient {
        stored: Option<ObservedTransfer>,
        create_result: CreateTransferResult,
    }

    impl TransferClient for MemoryClient {
        type Error = ();

        fn lookup_transfer(
            &mut self,
            _transfer_id: &str,
        ) -> Result<Option<ObservedTransfer>, Self::Error> {
            Ok(self.stored.clone())
        }

        fn create_transfer(
            &mut self,
            intent: &TransferIntent,
        ) -> Result<CreateTransferResult, Self::Error> {
            if self.create_result == CreateTransferResult::Created {
                self.stored = Some(observed_from(intent));
            }
            Ok(self.create_result.clone())
        }
    }

    #[test]
    fn lookup_before_retry_confirms_existing_matching_transfer() {
        let intent = valid();
        let mut client = MemoryClient {
            stored: Some(observed_from(&intent)),
            create_result: CreateTransferResult::Rejected,
        };
        assert_eq!(
            submit_lookup_before_retry(&mut client, &intent),
            Ok(SubmitOutcome::Confirmed)
        );
    }

    #[test]
    fn lookup_before_retry_rejects_a_conflicting_existing_transfer() {
        let intent = valid();
        let mut observed = observed_from(&intent);
        observed.debit_account_id = "other".into();
        let mut client = MemoryClient {
            stored: Some(observed),
            create_result: CreateTransferResult::Created,
        };
        assert_eq!(
            submit_lookup_before_retry(&mut client, &intent),
            Err(SubmitError::ExistingTransferMismatch)
        );
    }

    #[test]
    fn lookup_after_create_confirms_the_same_durable_id() {
        let intent = valid();
        let mut client = MemoryClient {
            stored: None,
            create_result: CreateTransferResult::Created,
        };
        assert_eq!(
            submit_lookup_before_retry(&mut client, &intent),
            Ok(SubmitOutcome::Confirmed)
        );
    }
}
