# ADR-0003 — v0.1採SQLite與SHA-256事件鏈

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

單機、單使用者需要低維運持久化、交易及可檢查歷史。完整分散式Event Store超出v0.1範圍。

## Decision

使用SQLite WAL保存Operational state與Events。Events以previous hash串鏈，提供JSONL匯出及Experimental Rust verifier。

## Alternatives

- PostgreSQL：可靠但增加安裝與維運。
- 純JSONL：Replay容易，查詢與交易較弱。
- Vector DB：不適合作為權威事件與狀態儲存。

## Consequences

- 單機部署簡單。
- 不支援多主寫入。
- Hash chain偵測篡改但不是簽章。

