# ADR-0007 — Python主Runtime，Rust先作獨立Verifier

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Python適合AI、API及快速迭代；Rust適合獨立完整性驗證與未來效能敏感模組。若一開始大量跨語言，會降低單人開發速度。

## Decision

v0.1主Runtime使用Python 3.11+。Rust crate只驗證event export，標記Experimental且不成為執行必要條件。

## Consequences

- 第一版可在沒有Rust toolchain的使用者電腦運行。
- CI另外編譯Rust。
- 未經profiling不把模組移往Rust。

