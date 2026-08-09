# ECK — Embodied Cognitive Kernel

**Digital Life Kernel v0.1**

> v0.1.0 is the current verified runtime. v0.2.0 is now in specification and
> development; the runtime version will not change until its release gate passes.

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
- Local dialogue grounded with admitted experience, active skills, and verified research.
- Human-guided, bounded academic curricula using allowlisted Crossref metadata and abstracts.
- Persistent idle supervisor that reviews verified learning and assigns bounded research tests.
- Persistent multi-day challenge contracts with externally reviewed social metrics.
- Versioned MMLU, GSM8K, FrontierScience, and real-task evaluation records.
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

For a native Windows process with PID and log tracking instead of Compose, use:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-eck.ps1 -Wait
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
ECK_SUPERVISOR_MODEL=<optional second Ollama model; defaults to ECK_OLLAMA_MODEL>
```

The supervisor is a separate role and provider instance. By default it uses the same
local model sequentially, which avoids loading two models at once on limited VRAM. Its
first review delay and recurring interval are controlled by
`ECK_SUPERVISOR_INITIAL_DELAY_SECONDS` and `ECK_SUPERVISOR_REVIEW_SECONDS`.

Supervisor inference is bounded independently from normal dialogue. The reference
profile checks at most every 10 minutes, allows at most 48 reviews in a rolling 24-hour
window, limits output to 512 tokens and context to 4096 tokens, and offloads only 12
model layers to the GPU. Set `ECK_SUPERVISOR_NUM_GPU_LAYERS=0` for CPU-only supervision.
GPU utilization can still briefly reach 100% during matrix operations; the limits reduce
duration and heat rather than guaranteeing a particular utilization percentage.
Previously reviewed topics are checked against the complete persisted review history;
exact, contained, and highly similar topics are replaced or skipped before assignment.

The deterministic acceptance tests use `MockBrainProvider`; they do not require
Ollama or a GPU.

## v0.2 current-information development

The first implemented v0.2 priority is `web.critical_research`. It discovers recent
public sources through the free GDELT DOC 2.0 index, fetches pages through the read-only
SSRF/robots/size-gated worker, preserves source hashes and metadata, extracts checkable
claims, looks for supporting and contradicting evidence, and reports uncertainty instead
of forcing a conclusion.

Raw HTTP bodies stay in memory only. Cleaned text is compressed in SQLite for 30 days by
default; provenance, hashes, claims, and exact evidence excerpts remain traceable. Exact
and near-duplicate pages are retained as source records but count as one independent
content group. If more than half of the latest ten completed runs are inconclusive, the
research quality endpoint reports `degraded` so the supervisor improves the method rather
than endlessly adding similar topics.

```text
POST /v1/research/critical
GET  /v1/research/runs
GET  /v1/research/runs/{run_id}
GET  /v1/research/quality
```

This milestone improves ECK's research procedure and evidence memory; it does not train
the Qwen weights or prove AGI. See `docs/16-current-information-critical-learning.md`.

The deterministic autonomous curriculum now fills idle learning slots without requiring
an expensive supervisor inference first. Its status is available at
`GET /v1/learning/autonomous/status`. Learning throughput is bounded and still requires
external evidence; continuous GPU saturation is not treated as progress.

By default, 70% of autonomous research slots now target agent architecture, workflow
automation, reusable skills, MCP, memory, evaluation, sandboxing, local inference, and
self-modification safety. Trusted official specifications and maintainer repositories are
listed by `GET /v1/learning/community-sources`; catalog inclusion never authorizes direct
code execution or bypasses the isolated skill and regression gates.

## P5 verified self-development

P5 prioritizes ECK's ability to inspect, test, and improve its own software without
presenting unverified model output as progress. Autonomous learning slots are allocated
50% to ECK self-development, 30% to current AI engineering, 15% to foundations, and 5%
to operator themes and broad exploration.

The repository self model now records definitions, imports, calls, API routes, and direct
or one-hop test impact. Code drafting uses the separately configured local
`qwen2.5-coder:7b` provider on the reference RTX 3060 Laptop profile. Generated runtime
skills must pass isolated validation plus repeated Canary validation before activation.
Structural core changes remain isolated and human-approved.

The autonomous project laboratory can turn conclusive research into a small local project,
run its tests in a networkless constrained Docker worker, preserve its source and evidence
in portability bundles, and publish only verified projects through an authenticated GitHub
CLI session. Publication is private by default, includes an AI/ECK disclosure, and never
stores GitHub credentials in ECK. Multi-account installations resolve the token for the
configured dedicated account without switching the operator's active GitHub CLI account.
Supervisor topics also normalize cosmetic round counters before de-duplication and retain
their cooldown across process restarts.

```text
GET  /v1/self-model/impact?path=src/eck/services/project_lab.py
GET  /v1/evolution/projects
POST /v1/evolution/projects/run
POST /v1/evolution/projects/{project_id}/publish
```

P5 is an auditable recursive software-development mechanism. It is not proof of AGI,
world-class engineering ability, or autonomous base-model weight improvement. See
`docs/27-p5-verified-recursive-self-development.md` for its gates and claim boundary.

## P6 durable mission executor

P6 changes explicit software requests from one-shot chat answers into persistent execution.
The Python controller compiles a mission into typed, dependency-ordered microtasks and stores
each auditable reason summary, tool action, external observation, correction, attempt, and
result in SQLite. A restart returns interrupted idempotent steps to the queue instead of losing
the mission.

The first verified workers deliver static websites and constrained Python projects. Website
missions produce a local preview, deterministic validation report, ZIP plus SHA-256, and a
private GitHub repository when the dedicated account is ready. Python projects must pass static
quality checks and pytest inside the existing networkless Docker worker. Unsupported project
types stop at a recorded capability boundary; model text never counts as delivery.

Mission source lives under `workspace/missions/<mission_id>/`, separate from the ECK core and
autonomous research projects. Per-mission and total workspace quotas prevent silent disk
exhaustion. `ECK_MISSION_ARCHIVE_DIR` reserves a future NAS archive target; no completed source
is silently deleted while that target is unset.

```text
POST /v1/chat                                      # "製作一個旅遊網站並展示成果"
GET  /v1/missions/executor/status
GET  /v1/missions/{mission_id}/execution
GET  /v1/missions/{mission_id}/preview/
GET  /v1/missions/{mission_id}/download
```

P6 is a durable, verifier-grounded task runtime—not evidence that ECK is AGI or that it can yet
complete every arbitrary digital task. See `docs/28-p6-durable-mission-executor.md`.

## Safety boundary

v0.1 defaults:

- binds only to localhost;
- permits registered native capabilities plus tested Docker worker skills;
- validates public research URLs against SSRF, credentials, robots, redirects, response size,
  content type, source count, and research-window limits;
- prohibits system-file mutation;
- exposes no arbitrary shell capability;
- permits only registered actions;
- permits policy-compliant public posts, likes, follows, replies, and private messages
  without per-action approval, while requiring an explicit AI/ECK disclosure;
- blocks paid APIs, real-money actions, deception, fake engagement, illegal content,
  personal data in private messages, and platform-control evasion;
- sends legal uncertainty, credentials/CAPTCHA/2FA, and tested structural
  self-modification to human approval;
- treats model self-report as non-external evidence;
- refuses social actions when platform rules prohibit automation, and requires an
  official adapter plus an explicit AI/ECK disclosure before any public action;
- allows free PyPI/npm dependencies only inside the disposable Docker skill worker;
- does not include a model-weight training pipeline yet.

The reference configuration enables both fixed-host Crossref academic research and the
v0.2-development critical current-information loop. Crossref queries bibliographic metadata
and available abstracts only; it does not claim to read paywalled or unavailable full text.
Critical research can read public HTML through a separate read-only operation surface, but
cannot click, log in, post, publish, follow, like, or send messages. Both paths remain bounded
and must pass a Success Contract before the research procedure becomes positive learning.
The supervisor only assigns a new test when no task is queued, running, or awaiting
approval. Dialogue responses are not admitted as learning by themselves.

## Legacy challenge 001

The first persistent challenge is platform-neutral: ECK must publish one primary post
per local day until one post receives at least 100 human-verified comments and 10 likes
within the same 24-hour window. Platform, topic, language, audience, and experiments are
planning decisions for ECK rather than fixed task inputs.

Completion also requires a human-reviewed public URL, the disclosure
`此帳號由 AI/ECK 協作營運`, policy compliance, and the daily-post cadence. Fake
engagement, paid promotion, deception, and metric manipulation cannot satisfy the
contract. The current release persists and plans the challenge, records evidence, and
enforces its contract; it does **not** yet log in to or operate a social platform.

Create or resume it from the Dashboard, or call:

```text
POST /v1/challenges/social-engagement
GET  /v1/challenges
```

## Missions and resource allocation

Missions are editable background objectives. Creating a mission schedules a bounded
planning task, records capability gaps, and keeps the plan itself separate from positive
learning. Eligible software missions additionally compile into the P6 durable execution graph.
ECK allocates 90% of normal scheduling opportunities to autonomous learning
and 10% to mission preparation or execution. Urgent human tasks override this ratio.

Submitting evidence moves a mission to `awaiting_review`; it is not complete until the
operator approves it. Approved missions remain in a collapsed history for later
inspection. Monthly approved missions may trigger a major runtime version only when
verified updates are pending. Every 100 active verified skills triggers one minor
runtime version increment. Skill implementation patch versions do not restart or
renumber the ECK runtime.

Manage missions from the Dashboard or call:

```text
POST  /v1/missions
PATCH /v1/missions/{mission_id}
POST  /v1/missions/{mission_id}/completion
POST  /v1/missions/{mission_id}/review
```

## Docker skill workers

Build the worker image once after Docker Desktop starts:

```powershell
docker build -f docker/skill-worker/Dockerfile -t eck-skill-worker:0.1.0 .
```

Running the image directly performs a self-check and exits successfully. `validate` and
`execute` are worker-protocol modes and require the ECK core to mount
`/request/manifest.json`; they should not be launched manually from Docker Desktop.

ECK validates generated or bundled skills in one-shot containers with a read-only root
filesystem, dropped Linux capabilities, `no-new-privileges`, CPU/memory/PID limits,
and no Docker socket. A skill is hot-activated only after its isolated tests pass. The
initial worker pack covers browser inspection, documents, images, code tests, advanced
data analysis, and policy-gated social adapters. The social adapter cannot perform or
claim a platform action until a compliant official adapter is configured.

## Local image generation

ECK uses a local-only Stable Diffusion WebUI Forge API on `127.0.0.1:7861`; it does not
call a paid image API. The installed image stack includes three SD1.5 SafeTensor
checkpoints, ADetailer face repair, Forge's integrated ControlNet with OpenPose, and
`rembg` with BiRefNet-General for transparent-background output.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-forge.ps1 -Wait
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/verify-image-stack.ps1
```

The dialogue router sends image requests to `image.generate` and requests such as
`移除上一張圖片背景` to `image.remove_background`. Model aliases are
`realistic_vision`, `chilloutmix`, and `cyberrealistic`; their exact filenames, source
URLs, hashes, intended strengths, and reuse flags are recorded in
`config/image-models.json`.

Legal adult nude or erotic image generation is enabled locally. ECK permanently rejects
sexual content involving minors, non-consensual sexual content, and sexual content
involving animals. These application checks do not replace applicable law or a human
review before publishing generated material.

Forge and ADetailer are AGPL-3.0 software; `rembg` is MIT software. Checkpoint terms are
separate. The installed ChilloutMix release does not grant commercial use in its Civitai
metadata, and Realistic Vision requires attribution; review the recorded model source
before public or commercial distribution.

## Local video generation

The `video.generate` capability uses the official FramePack implementation and model
weights locally. The selected low-VRAM path supports RTX 30-series GPUs, but a 6 GB RTX
3060 Laptop remains substantially slower than high-end desktop hardware. ECK serializes
media workers and stops Forge before FramePack starts to reduce VRAM contention.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup-framepack.ps1
```

The setup keeps the large Hugging Face cache outside the Git worktree and creates a local
junction for FramePack. No paid API is used. ECK does not implement a jailbreak or a
safeguard-bypass mode; legal adult-content configuration never permits minors,
non-consensual content, sexual violence, bestiality, or illegal use.

## Cognitive portability

`POST /v1/portability/bundles` exports a checksum-verified cognitive bundle containing a
consistent database backup, runtime configuration, dependency locks, generated hot-skill
source, and model catalog. Large model weights and secrets are excluded by default and
must be restored from their licensed sources before a clean-machine regression test.

See `docs/17-autonomous-learning-portability-media.md` for the learning validation,
transfer, and current AGI-gap criteria.

## Capability evaluation

ECK stores versioned benchmark runs rather than treating conversation quality or skill
counts as proof of intelligence. The initial catalog includes
[MMLU](https://arxiv.org/abs/2009.03300),
[GSM8K](https://arxiv.org/abs/2110.14168),
[FrontierScience](https://openai.com/index/frontierscience/), and a fixed suite of 20–50
real tasks. A model cannot be the sole judge of its own growth claim, and a finite
benchmark result is not presented as proof that a system has surpassed all humans.

P3 adds `POST /v1/evaluations/objective`, a 20-case local public diagnostic with
deterministic exact-match grading, repeated-answer reproducibility, suite hashing, Ollama
model digests, resource snapshots, and same-condition comparison. The dashboard separately
reports research admissions, new memory procedures, generated skill candidates, and tested
active generated skills, so repeated research cannot be presented as executable skill growth.
See `docs/25-p3-objective-evaluation.md` for the claim boundary and next-stage requirements.

Docker isolation reduces risk but is not a formal security boundary for hostile code.
Generated Python executes only through the restricted worker protocol; structural core
changes and model-weight changes still require testing and human approval.

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
  services/        workflows, self model, evolution, and autonomous project laboratory
  storage/         SQLite event/task/experience store
  verification/    external-evidence contract verifier
docs/              13-volume v0.1 specification and completion report
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
