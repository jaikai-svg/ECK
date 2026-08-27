# Review Round 2 — Backward Compatibility and Migration

## Observation

- Existing REST paths, SQLite records, P6 execution, Library authoring and task-skill relations
  must remain operational after additive tables and response fields.
- Clearing `target_month` required distinguishing an omitted field from an explicit JSON null.

## Correction

- Preserve no-argument skill counting and all existing REST paths.
- Use Pydantic `model_fields_set` so explicit null clears only the nullable target month.
- Add all three audit tables to copy-only migration, integrity and rollback verification.

## Verification

- Workspace, Phase 2, P6, migration and integration API suite: 37 passed.
