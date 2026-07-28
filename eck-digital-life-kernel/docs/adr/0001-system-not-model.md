# ADR-0001 — ECK是系統，不是單一模型

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

模型架構與權重會快速更換，但生命週期、證據、行動與記憶需要穩定邊界。若把所有能力放在單一模型類別，無法可靠替換、測試或追蹤。

## Decision

將Brain視為可替換Provider。Kernel、Policy、Capability、Verifier與Memory皆不依賴特定模型。

## Consequences

- 可以用Mock驗證核心。
- 模型無法自行授予成功。
- 需要更多明確Contract。
- 系統整合複雜度高於單一聊天程式。

