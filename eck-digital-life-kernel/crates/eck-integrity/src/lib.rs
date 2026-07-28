//! Independent integrity verification for exported ECK events.
//!
//! This crate is intentionally outside the Python runtime trust boundary. In
//! v0.1 it is an optional verifier and is not required to run the kernel.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const GENESIS_HASH: &str =
    "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EventEnvelope {
    pub sequence: u64,
    pub event_id: String,
    pub event_type: String,
    pub aggregate_id: String,
    pub correlation_id: Option<String>,
    /// Canonical JSON string exactly as stored by the Python event store.
    pub payload_json: String,
    pub previous_hash: String,
    pub event_hash: String,
    pub created_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChainError {
    pub sequence: u64,
    pub reason: String,
}

pub fn hash_event(event: &EventEnvelope) -> String {
    let material = [
        event.previous_hash.as_str(),
        event.event_id.as_str(),
        event.event_type.as_str(),
        event.aggregate_id.as_str(),
        event.correlation_id.as_deref().unwrap_or(""),
        event.payload_json.as_str(),
        event.created_at.as_str(),
    ]
    .join("|");
    format!("{:x}", Sha256::digest(material.as_bytes()))
}

pub fn verify_chain(events: &[EventEnvelope]) -> Result<(), ChainError> {
    let mut previous = GENESIS_HASH.to_owned();
    for event in events {
        if event.previous_hash != previous {
            return Err(ChainError {
                sequence: event.sequence,
                reason: "previous hash does not match the prior event".to_owned(),
            });
        }
        if hash_event(event) != event.event_hash {
            return Err(ChainError {
                sequence: event.sequence,
                reason: "event hash does not match its canonical material".to_owned(),
            });
        }
        previous.clone_from(&event.event_hash);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event() -> EventEnvelope {
        let mut item = EventEnvelope {
            sequence: 1,
            event_id: "event_test".to_owned(),
            event_type: "KernelStarted".to_owned(),
            aggregate_id: "eck-local".to_owned(),
            correlation_id: None,
            payload_json: r#"{"boot_count":1}"#.to_owned(),
            previous_hash: GENESIS_HASH.to_owned(),
            event_hash: String::new(),
            created_at: "2026-07-29T00:00:00+00:00".to_owned(),
        };
        item.event_hash = hash_event(&item);
        item
    }

    #[test]
    fn accepts_a_valid_chain() {
        assert_eq!(verify_chain(&[event()]), Ok(()));
    }

    #[test]
    fn detects_payload_tampering() {
        let mut item = event();
        item.payload_json = r#"{"boot_count":999}"#.to_owned();
        let error = verify_chain(&[item]).expect_err("tampering must fail");
        assert_eq!(error.sequence, 1);
    }
}

