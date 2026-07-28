# ADR-0006 — Ollama為預設本地Brain Provider

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

目標環境是Windows 11、WSL2、Docker Desktop與16GB VRAM。需要易安裝、具REST API、模型可替換且不依賴雲端的runtime。

## Decision

Ollama作為預設Provider，Mock用於測試。模型名稱不寫死，也不由ECK自動下載。

## Consequences

- 使用者明確決定模型與授權。
- ECK沒有模型也能啟動核心。
- Docker透過host.docker.internal連線。
- 未來可加入llama.cpp/vLLM adapter。

