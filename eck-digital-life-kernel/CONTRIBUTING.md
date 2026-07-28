# Contributing to ECK

ECK accepts changes only when the implementation, contract, tests, and
documentation remain consistent.

## Required workflow

1. Open an issue or ADR for changes to lifecycle, contracts, persistence,
   policy, evidence, admission, or public APIs.
2. Mark the proposal as implemented, experimental, future, or research.
3. Add or update deterministic tests before requesting review.
4. Run `ruff check .`, `mypy src/eck`, and `coverage run -m pytest`.
5. Update the relevant document under `docs/`.
6. Do not introduce an action that bypasses `PolicyGate` or an outcome that
   bypasses `ContractVerifier`.

## Architectural invariants

- Model self-report is never external evidence.
- Unknown capabilities fail closed.
- System-file mutation is prohibited in v0.1.
- Network capabilities are disabled by default.
- High-risk action requires explicit approval.
- Model weights are immutable in v0.1.
- Durable events are append-only and hash chained.

## Commit convention

Use one of:

- `feat:` behavior visible to operators or integrators
- `fix:` correctness or safety repair
- `docs:` specification-only change
- `test:` test-only change
- `refactor:` behavior-preserving code change
- `security:` risk-boundary change

