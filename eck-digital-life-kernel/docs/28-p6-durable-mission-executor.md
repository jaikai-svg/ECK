# P6 Durable Mission Executor

## 1. Purpose

P6 replaces one-shot software answers with a persistent controller loop:

```text
Goal
→ typed microtask DAG
→ auditable reason summary
→ registered action
→ external observation
→ correction or verified advance
→ packaged evidence
→ human review
```

This design follows the useful part of ReAct—interleaving reasoning summaries with environment
actions and observations—without exposing private chain-of-thought or treating model narration
as evidence. The background scheduler can replay an interrupted idempotent step after restart.

## 2. Persistent contract

SQLite stores two new ledgers:

- `mission_steps`: stable key, action type, dependency keys, status, attempts, inputs, output,
  timestamps, and the final error;
- `mission_react_cycles`: reason summary, structured action, tool observation, correction,
  attempt number, status, and timestamps.

Only dependency-complete steps can be claimed. A failed step blocks pending descendants. A
successful model response does not advance the graph unless the corresponding tool contract
also succeeds.

## 3. Verified workers

### Static website

The worker requires complete local `index.html`, `styles.css`, `app.js`, and `README.md` files.
The validator checks document title, viewport, semantic navigation and main content, local asset
references, CSS balance, JavaScript presence, placeholder removal, objective relevance, and a
source hash. Failure returns a real observation to the coder and retries the same fixed contract.

### Python project

The worker accepts a bounded Python 3.11 project with deterministic pytest tests. It reuses the
P5 static quality gate and executes tests in the read-only, networkless Docker skill worker. Mocked
success, placeholder functions, undefined names, unsupported dependencies, and weak tests are
rejected.

## 4. Delivery and review

Verified source is ZIP-compressed before Git initialization and receives SHA-256 evidence. GitHub
publication uses the configured ECK account credential from GitHub CLI without persisting the
token. The mission then enters `awaiting_review`; only the human creator can mark it approved.

## 5. Storage governance

Mission files are isolated under `workspace/missions/<mission_id>` and never mixed with the ECK
core. Defaults enforce 256 MB per mission and 5 GB total. Reaching a quota blocks new writes with
an explicit observation rather than filling the SSD. A future NAS path can be configured through
`ECK_MISSION_ARCHIVE_DIR`; until it is configured and verified, P6 does not silently delete final
artifacts.

## 6. Acceptance gates

P6 is acceptable only when all of these remain true:

1. a travel-site chat request creates a mission instead of returning an inability disclaimer;
2. the mission has dependency-ordered persisted steps and ReAct cycles;
3. an interrupted running step returns to pending after restart;
4. invalid output cannot be packaged or published;
5. a verified website has a working local preview, downloadable ZIP, and source SHA-256;
6. GitHub publication is private by default and uses the dedicated ECK account;
7. completion remains pending until human review;
8. source stays outside the ECK core and within configured storage quotas.

## 7. Claim boundary

P6 is not proof of AGI, consciousness, universal software competence, or frontier-model
superiority. It establishes a durable action/observation/correction substrate and two verified
software workers. New worker types must add fixed success contracts, isolation, reproducible
tests, failure evidence, and restart semantics before ECK may claim that capability.

## 8. Research basis

- [ReAct](https://arxiv.org/abs/2210.03629) motivates interleaved action and observation.
- [SWE-bench Verified](https://www.swebench.com/verified.html) motivates externally checkable
  software-task outcomes.
- [METR task-completion time horizons](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/)
  motivate measuring autonomous task duration instead of relying on fluent answers.
