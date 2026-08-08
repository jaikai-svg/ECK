# ADR-0008: Tiered Critical Web Research

- Status: Accepted for v0.2 development
- Date: 2026-08-08

## Context

ECK needs recent public information without treating search rank, repetition, or model summaries
as truth. A proposed stack used Redis, Celery, PostgreSQL, a vector database, raw HTML snapshots,
and permanent embeddings from the first implementation.

## Decision

Use an in-memory raw response buffer, compressed cleaned text in SQLite with 30-day retention,
and permanent provenance plus claim-level evidence records. Preserve duplicate source snapshots,
but group exact and near-duplicate content so it cannot inflate independent evidence counts.

Use the existing task queue instead of Celery and defer a dedicated vector database until measured
retrieval scale requires it. Separate read-only public research from all state-changing browser
operations at the registered capability and worker boundary.

An inconclusive conclusion is a valid research-process outcome, not verified topical knowledge.
The rolling quality gate becomes degraded when more than half of the latest ten completed runs are
inconclusive.

## Consequences

- The initial deployment remains local and operationally small.
- Source provenance survives after cleaned full text expires.
- Reprints cannot masquerade as independent corroboration.
- Embeddings can be rebuilt later from retained licensed content, but semantic RAG is not included
  in this milestone.
- A future state-changing browser worker cannot reuse the read-only worker's operation surface.
