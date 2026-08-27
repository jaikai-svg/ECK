# Review Round 1 — Correctness and Atomicity

## Observation

- Exact-claim coalescing initially selected the newest knowledge ID, so a repeated learning event
  could change a Library card primary identity and detach domain relations.
- A queued or running sleep record could remain stale after a process restart.
- A database failure after artifact quarantine restored files but initially lacked a durable failed
  deletion record.

## Correction

- Preserve the prior canonical knowledge ID; otherwise choose the oldest verified occurrence.
- Reconcile interrupted sleep runs on startup and resume queued runs.
- Restore quarantined paths and persist a failed deletion run after database-commit failure.

## Verification

- Stable card identity, domain binding, sleep restart, deletion rollback, Ruff and focused pytest
  passed.
