# ADR-0002 — 只有外部證據能授予成功學習

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Self-training若以模型自身信心當標籤，會放大錯誤。ECK需要可被環境、測試或人類核對的Outcome。

## Decision

所有正向Experience Admission必須是Verified Success、含外部Evidence且可重現。Model self-report永遠不算外部Evidence。

## Consequences

- 很多開放式任務在v0.1會是Unverifiable。
- 成功範圍較窄，但資料品質較高。
- 新領域導入前必須先建立Verifier。

