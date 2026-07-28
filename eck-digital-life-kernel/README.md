# ECK — Embodied Cognitive Kernel

**Digital Life Kernel v0.1**

ECK v0.1 is a local, persistent lifecycle runtime that can remain active, restore
its identity after restart, accumulate Experience, evidence-grounded Knowledge,
deterministic Reflection, and reusable Skills. It is deliberately **not**
described as AGI and it does not update model weights automatically.

> No Success Contract, no formal action.  
> No external evidence, no successful learning.  
> No reproduction and regression, no skill activation.

## v0.1 includes

- Persistent lifecycle: start, run, pause, sleep, resume, stop, recover.
- Tamper-evident SQLite event log with replay.
- Versioned Success Contracts.
- Graded risk gate and human approval queue.
- External evidence and deterministic reproduction checks.
- Experience admission, a Knowledge ledger, deterministic Reflection records,
  and candidate/active Skill states.
- Replaceable Mock and Ollama brain providers.
- CLI, REST API, OpenAPI, and a local Web Dashboard.
- Safe arithmetic-expression capability without shell or file access.
- Deterministic GridWorld capability for experience-reuse measurement.
- Windows 11 + WSL2 + Docker Desktop deployment.

## Fastest start on Windows 11

Prerequisites:

1. Docker Desktop with WSL2 integration.
2. Ollama installed on Windows if an actual local model is desired.
3. An Ollama model already pulled. ECK does not silently download one.

PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
.\scripts\setup-windows.ps1
.\scripts\start-windows.ps1
```

Open:

- Dashboard: <http://127.0.0.1:8420>
- OpenAPI: <http://127.0.0.1:8420/docs>
- Health: <http://127.0.0.1:8420/health>

Run all acceptance scenarios:

```powershell
docker compose exec eck python -m eck.acceptance
```

Stop while preserving identity, memory, and events:

```powershell
.\scripts\stop-windows.ps1
```

## Native Python development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
eck serve
```

On PowerShell, activate with `.venv\Scripts\Activate.ps1`.

## Configuration

The canonical configuration is `config/eck.yaml`. Environment variables prefixed
with `ECK_` override YAML values. Copy `.env.example` to `.env` for machine-local
settings.

The Ollama model is intentionally not hard-coded:

```dotenv
ECK_OLLAMA_MODEL=<a model already present in ollama list>
```

The deterministic acceptance tests use `MockBrainProvider`; they do not require
Ollama or a GPU.

## Safety boundary

v0.1 defaults:

- binds only to localhost;
- disables network capabilities;
- prohibits system-file mutation;
- exposes no arbitrary shell capability;
- permits only registered actions;
- sends high-risk actions to human approval;
- treats model self-report as non-external evidence;
- never updates model weights.

Docker adds a read-only root filesystem, dropped Linux capabilities, a
`no-new-privileges` policy, and localhost-only port publishing. These controls
reduce risk but are not a formal security sandbox for hostile code. Therefore
v0.1 intentionally does not execute arbitrary generated Python.

## Repository map

```text
src/eck/
  api/             REST API and lifecycle integration
  brain/           replaceable Mock/Ollama providers
  capabilities/    allowlisted executable capabilities
  dashboard/       local operator interface
  domain/          versioned contracts and state models
  events/          durable publication and replay
  kernel/          Digital Life lifecycle
  memory/          experience, knowledge, reflection, and skill admission
  policy/          graded "otherwise do nothing" gate
  services/        application workflows
  storage/         SQLite event/task/experience store
  verification/    external-evidence contract verifier
docs/              13-volume v0.1 specification
tests/             unit and integration verification
```

## Documentation status

The `docs/` directory distinguishes:

- `Implemented in v0.1`
- `Experimental in v0.1`
- `Specified for a later release`
- `Research hypothesis`

No future capability is presented as implemented.

## License

Apache License 2.0. See `LICENSE`.
