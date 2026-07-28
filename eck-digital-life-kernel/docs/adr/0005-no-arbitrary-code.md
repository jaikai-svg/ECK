# ADR-0005 — v0.1不執行任意Shell或生成程式

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Docker read-only與非root不能構成敵對程式的完整沙盒。把Docker socket交給Agent更會擴大權限。

## Decision

不註冊Shell capability；程式驗收只允許AST allowlist的單一算術表達式，沒有Call、Attribute、Import、檔案或網路。

## Consequences

- 安全邊界清楚。
- 無法驗證一般程式修復。
- 未來需獨立sandbox worker、唯讀輸入、一次性容器、資源上限與無網路設計。

