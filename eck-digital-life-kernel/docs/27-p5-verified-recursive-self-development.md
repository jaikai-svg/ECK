# P5 Verified Recursive Self-Development

**Status:** implemented and under release verification
**Runtime claim:** an evidence-gated self-development loop, not AGI and not autonomous model-weight training

## Objective

P5 makes ECK study itself and modern AI engineering before broad exploration. It converts verified research into isolated code artifacts, tests them, records provenance, and permits only low-risk runtime skills to enter a bounded Canary. Structural core changes remain isolated candidates requiring human approval.

The default autonomous learning portfolio is:

- 50% ECK self-development: repository architecture, defects, tests, reliability, portability, and capability gaps.
- 30% current AI engineering: agents, memory, evaluation, tool use, local inference, safety, and self-improvement research.
- 15% foundation: software engineering, mathematics, statistics, systems, and scientific method.
- 5% exploration: operator themes and unrelated high-information topics.

This is a scheduling policy, not proof of learning. A cycle that produces no external evidence, reproducible result, or tested artifact does not increase the verified experience or skill counts.

## Repository Self Model v2

`RepositorySelfModelService` builds a local, deterministic code map containing:

- files, SHA-256 hashes, Python definitions, imports, calls, and API routes;
- direct and one-hop relationships between implementation and tests;
- Git commit and working-tree state;
- impact reports for a requested path, including inbound dependencies, outbound dependencies, related tests, routes, and risk signals.

This graph helps the coder select relevant context. It does not imply that the model fully understands the repository, and generated changes still require executable validation.

```text
GET  /v1/self-model
GET  /v1/self-model/impact?path=src/eck/services/project_lab.py
POST /v1/self-model/refresh
```

## Dedicated Local Coder

P5 routes code drafting through a separate Ollama provider configured by `ECK_CODER_MODEL`. The reference RTX 3060 Laptop profile uses `qwen2.5-coder:7b`; ordinary dialogue and supervision can retain their existing model. A shared inference arbiter prevents the providers from competing for limited VRAM.

The coder may propose source files, but cannot declare its own result successful. ECK validates paths, file types, size, secrets, required tests, deterministic inputs, standard-library-only imports, undefined names, placeholder text, non-mocked behavioral assertions, objective-to-identifier relevance, source hashes, and Docker test output before recording a verified project. Contract or test failures are returned to the coder for at most three repair attempts with the preceding files preserved as bounded context. If the 7B model cannot maintain a multi-file JSON contract, one final split-file repair writes a fixed source module first and then generates tests against that exact source; a final AST-guided test-only repair may reference only functions actually present in the source. These repairs receive no weaker quality privileges. Exhaustion remains a recorded failure rather than weakening the gate. Previously verified local projects are re-audited against the current deterministic quality contract and downgraded if a stronger gate finds simulated, mocked, undefined, or unrelated success.

## Autonomous Project Laboratory

The project laboratory converts unused, conclusive research records into small standard-library-first Python experiments. Each project receives an immutable manifest containing its objective, research IDs, generated files, validation report, source hash, timestamps, publication state, and disclosure.

Projects are stored below `workspace/projects/` and run in a one-shot Docker container with:

- no network;
- a read-only container root;
- dropped Linux capabilities and `no-new-privileges`;
- CPU, memory, process, and timeout limits;
- mandatory tests before the state can become `verified`.

The background scheduler defaults to no more than one autonomous project per day. It waits for a pool of at least four unused conclusive research records, then incubates one lead topic at a time so unrelated evidence cannot dilute the implementation objective. Manual project requests remain available through the API.

```text
GET  /v1/evolution/projects
GET  /v1/evolution/projects/{project_id}
POST /v1/evolution/projects
POST /v1/evolution/projects/run
POST /v1/evolution/projects/{project_id}/publish
```

## Skill Canary

A non-structural generated skill must first pass isolated validation. P5 then repeats the same validation in a bounded Canary window. Any failed replay blocks activation and preserves the failure reports. Only all-pass Canary results may hot-activate the runtime skill.

The current core is still a monolithic Python process. P5 therefore does not claim live replacement of arbitrary core modules. Core candidates stay in detached worktrees and require the existing structural approval gate.

## GitHub Publication Boundary

Verified projects can be published with GitHub CLI. Repositories are private by default and include a public AI/ECK collaboration disclosure. ECK records the repository URL and command result but never stores a password, token, recovery code, CAPTCHA answer, or 2FA secret.

Creating the dedicated GitHub account and performing the first CLI login are human credential steps. After that one-time authentication, ECK may create and push only projects whose manifest state is `verified`. Failed or unverified projects remain local. Future publishers should implement the same manifest, disclosure, credential, policy, and evidence interface rather than bypassing it.

```powershell
gh auth login --hostname github.com --web
```

Set `ECK_GITHUB_ACCOUNT` to the authenticated dedicated account. Leave `ECK_GITHUB_DEFAULT_VISIBILITY=private` until the operator intentionally chooses public publication.

When GitHub CLI stores more than one account, ECK resolves the OAuth token for the exact configured account and injects it only into the publication child process. It does not switch the operator's active CLI account or persist the token in project files, manifests, logs, or environment files.

## Supervisor Novelty Gate

Supervisor topic comparison removes cosmetic round suffixes such as `（第 943 輪）` before semantic de-duplication. A restart also respects the timestamp of the latest persisted review, so it cannot bypass the configured review interval. If neither the model nor the fallback curriculum produces a novel topic, ECK records no new review or research task and waits for the next cycle.

## Portability

The cognitive bundle now includes project manifests and project source, excluding nested `.git`, caches, model weights, and secrets. A transferred ECK can therefore retain the same verified project evidence and executable source while restoring licensed model weights separately.

## Acceptance Contract

P5 is acceptable only when all of the following hold:

1. The self model records calls, routes, and implementation-to-test impact.
2. The 50/30/15/5 curriculum is deterministic and totals 100%.
3. The coder model is locally available and health-checked.
4. A generated project cannot become verified without isolated passing tests.
5. Unsafe paths and apparent secrets are rejected before execution.
6. A skill cannot activate unless every configured Canary replay passes.
7. GitHub publication is impossible before project verification and CLI authentication.
8. Portability preserves verified project source and provenance without secrets.
9. The complete regression suite, strict MyPy, Ruff, coverage gate, and release verifier pass.

## Research Basis

P5 follows the direction of repository graph retrieval, empirical self-improvement archives, risk-aware quality gates, and continually refreshed software-engineering evaluation. These sources motivate the design; they do not establish that ECK has reproduced their research results:

- RepoGraph: <https://arxiv.org/abs/2410.14684>
- Darwin Godel Machine: <https://arxiv.org/abs/2505.22954>
- Risk-Aware Quality-Gated Self-Improvement: <https://arxiv.org/abs/2606.26294>
- SWE-rebench: <https://arxiv.org/abs/2505.20411>
- SWE-Bench-CL: <https://arxiv.org/abs/2507.00014>
