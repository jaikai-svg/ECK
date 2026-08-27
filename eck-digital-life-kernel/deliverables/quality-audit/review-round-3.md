# Review Round 3 — UX, Refresh, Statistics and Performance

## Observation

- The result center offered an `archived` status filter, but archive operations update
  `storage_state`, not artifact status.
- Artifact rows without sidecar timestamps inherited index time, making old results appear to be
  created together; an end-date query excluded most of that date.
- Loading more project pages stopped later scheduled refreshes, leaving visible state stale.
- NAS restore succeeded without refreshing the open result detail.

## Correction

- Add a backward-compatible storage-state API filter and align the UI options.
- Use sidecar timestamp or filesystem mtime and date-aware SQLite filtering.
- Refresh up to the already loaded project count within the existing 48-row API limit.
- Reopen result detail after verified cache restore.

## Verification

- Added date, storage, API route and static performance contracts; targeted backend and frontend
  tests passed without adding idle polling.
