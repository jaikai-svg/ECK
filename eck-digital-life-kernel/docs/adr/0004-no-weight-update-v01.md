# ADR-0004 — v0.1不更新模型權重

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

自動LoRA/RL需要資料治理、候選模型、回歸、回滾與算力控制。v0.1尚未建立完整安全條件。

## Decision

累積Event、Experience、Knowledge候選、Skill與Reflection資料，但基礎模型權重保持不變。

## Revisit criteria

- 隔離candidate adapter；
-完整holdout與回歸；
- artifact版本/hash；
-人工promotion；
-一鍵rollback；
-證明不會讓已知能力顯著退化。

