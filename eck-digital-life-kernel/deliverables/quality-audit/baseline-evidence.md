# ECK Workspace 全面品質稽核：修正前證據

- Initial automatic mission draft: `mission_abe9c461117345c99b6f78b128a4a1d0` (cancelled after it duplicated the external audit and stalled on reference research)
- Final persistent review mission: `mission_d86fff81c8434c58b042dcbd628e7dba`
- Git rollback tag: `rollback-eck-workspace-quality-audit-20260812`
- Rollback commit: `c7f57ac7be82daf8f197fa276754a000c7ce0b77`
- Captured at: `2026-08-12T00:20Z`

## Reproduced contradictions

1. `/v1/workspace/home` reported 18 memory skills and 6 active runtime skills. The home UI summed these values and displayed 24 available skills.
2. Authoritative SQLite rows contained only 8 active memory skills and 6 active runtime skills. `/v1/workspace/skills` therefore correctly displayed 14 available skills.
3. Root cause: `WorkspaceReadService.home()` used `count_skills()`, which counts active and inactive memory skills, while the skills page used lifecycle `active` state.

## Knowledge and unresolved-question inflation

- Knowledge rows: 1,145.
- Admitted knowledge rows: 1,131.
- Distinct normalized admitted claims: 1,108.
- Exact duplicate claim groups: 8.
- Reflections: 1,145.
- `Retain the verified procedure; activate it only after the skill threshold.` appeared as `next_step` 1,082 times.
- `Revise the proposal or contract candidate, then run a new bounded task.` appeared 63 times.
- Library cards before correction: 1,131.
- Library unresolved questions before correction: 1,131.

Root causes:

1. The Library projection created one card for every admitted knowledge row without coalescing exact normalized duplicate claims.
2. Every non-empty reflection `next_step` was classified as an unresolved question, including deterministic workflow instructions that were not questions or open research gaps.

## Additional baseline observations

- Results catalog contained 68 indexed artifacts.
- Artifact rows had no hard-delete API or recoverable deletion plan.
- Mission PATCH existed, but no revision history or rollback API was available in the Workspace UI.
- Sleep API only returned `{ "accepted": true }`; no run ID, durable phase, result, error, before/after counts, or measured changes were exposed.
- NAS was unconfigured. Offline state was correctly distinct from missing files.
- Host resource pressure was `critical` because available memory was below the configured background floor; this is evidence, not a fabricated task failure.
