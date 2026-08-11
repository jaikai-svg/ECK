# P6 Durable Mission Executor

## 1. Purpose

P6 replaces one-shot software answers with a persistent controller loop:

```text
Goal
→ typed microtask DAG
→ current reference and approved-pattern retrieval
→ principal-architect contract
→ six persisted implementation microtasks
→ auditable reason summary
→ registered action
→ external observation
→ correction or verified advance
→ three independent review/improvement rounds
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

The default software graph contains 24 persisted steps. The principal architect produces exact
files, interfaces, objectives, and checks. Its first six tasks become separate
`software.microtask` steps; each receives its own reason/action/observation/correction cycle and
must change the source hash. An interruption therefore resumes at one bounded microtask rather
than regenerating the entire project.

## 3. Verified workers

### Static website

The worker requires complete local `index.html`, `styles.css`, `app.js`, and `README.md` files.
The v3 validator checks document title, viewport, five substantive sections, heading hierarchy,
semantic navigation and main content, HTML and CSS local-asset references, mission-language
alignment, CSS tokens/layout/real breakpoint/fluid type/visual depth/component/focus/hover/motion
states, three meaningful JavaScript interactions, observable non-blocking page feedback,
accessibility, placeholder removal, objective relevance, a configurable quality score, and a
source hash. Unsupported preprocessor functions, blocking alerts, simulated success, missing
background images, and generic wrong-language output are rejected.

Initial model output is compared with a deterministic designed baseline. A model result that
scores lower is discarded before it can become the working tree. Every architect and expert
improvement receives the same non-degradation gate. Mechanical issues such as missing local
images and `aria-live` are repaired deterministically; generated vector assets replace broken
references, and a model repair is rolled back if it increases issues or lowers quality.

### Python project

The worker accepts a bounded Python 3.11 project with deterministic pytest tests. It reuses the
P5 static quality gate and executes tests in the read-only, networkless Docker skill worker. Mocked
success, placeholder functions, undefined names, unsupported dependencies, and weak tests are
rejected.

## 4. Delivery and review

After architecture microtasks, an implementation engineer performs an integration pass. A
separate demanding reviewer then evaluates specification, content, visual design, interaction,
accessibility, and maintainability. ECK must persist at least three review/improvement rounds
before deterministic validation and cannot submit early.

Verified source is ZIP-compressed before Git initialization and receives SHA-256 evidence. GitHub
publication uses the configured ECK account credential from GitHub CLI without persisting the
token. Repositories use `<topic>-task-<sequence>` names such as `travel-task-0001`; later revisions
commit to the same repository. The mission then enters `awaiting_review`; only the human creator
can mark it approved.

If the creator rejects a result, the feedback becomes binding reviewer input. P6 increments a
human-revision identifier, resets all review, validation, learning, package, publication, and
submission steps, and executes three new expert rounds. The dashboard saves review drafts in
browser local storage and does not replace the active form during its five-second refresh.

## 5. Verified reuse

Every validated mission distils architecture choices, reviewer findings, quality score, and source
hash into a candidate pattern. It is not reusable while the mission is merely submitted. Only a
human-approved mission can be retrieved for a later similar project, so failed or unaccepted work
cannot become positive learning.

Public GitHub search contributes current repository metadata and provenance when networking is
enabled. It is non-blocking and does not copy source code or assets; licenses still require
separate review. The implementation follows the official Search API limits and bounded result
count.

## 6. Storage governance

Mission files are isolated under `workspace/missions/<mission_id>` and never mixed with the ECK
core. Defaults enforce 256 MB per mission and 5 GB total. Reaching a quota blocks new writes with
an explicit observation rather than filling the SSD. A future NAS path can be configured through
`ECK_MISSION_ARCHIVE_DIR`; until it is configured and verified, P6 does not silently delete final
artifacts.

Urgent human missions run ahead of autonomous background research while still yielding to direct
urgent media/tool tasks. Resource pressure pauses model-heavy work, but a runnable deterministic
workspace, packaging, publication, distillation, or submission step may continue because it does
not load the coder model. These mechanical steps use fixed auditable reason summaries instead of
spending an extra model call. Creative architecture, implementation, review, improvement, and
repair-capable validation steps retain model inference, resource throttling, and all three review
rounds.

## 7. Acceptance gates

P6 is acceptable only when all of these remain true:

1. a travel-site chat request creates a mission instead of returning an inability disclaimer;
2. the mission has 24 dependency-ordered persisted steps and ReAct cycles;
3. an interrupted running step returns to pending after restart;
4. invalid output cannot be packaged or published;
5. a verified website has a working local preview, downloadable ZIP, and source SHA-256;
6. GitHub publication is private by default and uses the dedicated ECK account;
7. six architect microtasks and three independent review rounds complete before human review;
8. rejected work re-enters the expert loop with the creator's feedback;
9. only human-approved patterns can be reused;
10. source stays outside the ECK core and within configured storage quotas.

## 8. Superpowers adaptation

P6 adopts the parts of [obra/superpowers](https://github.com/obra/superpowers) that fit ECK:
brainstorm/specification before implementation, detailed bite-sized plans, implementation and
review role separation, test-first behavior for Python projects, and evidence before completion.
It does not install Superpowers as an autonomous authority or copy its session/subagent runtime.
Those workflows assume a compatible coding-agent environment; ECK instead persists equivalent
role boundaries in SQLite and keeps deterministic validators plus human final approval.

## 9. Claim boundary

P6 is not proof of AGI, consciousness, universal software competence, or frontier-model
superiority. It establishes a durable action/observation/correction substrate and two verified
software workers. New worker types must add fixed success contracts, isolation, reproducible
tests, failure evidence, and restart semantics before ECK may claim that capability.

## 10. Research basis

- [ReAct](https://arxiv.org/abs/2210.03629) motivates interleaved action and observation.
- [SWE-bench Verified](https://www.swebench.com/verified.html) motivates externally checkable
  software-task outcomes.
- [METR task-completion time horizons](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/)
  motivate measuring autonomous task duration instead of relying on fluent answers.
- [Superpowers planning](https://raw.githubusercontent.com/obra/superpowers/main/skills/writing-plans/SKILL.md),
  [subagent development](https://raw.githubusercontent.com/obra/superpowers/main/skills/subagent-driven-development/SKILL.md),
  [review](https://raw.githubusercontent.com/obra/superpowers/main/skills/requesting-code-review/SKILL.md),
  and [TDD](https://raw.githubusercontent.com/obra/superpowers/main/skills/test-driven-development/SKILL.md)
  motivate explicit microtasks, role separation, and proof-oriented implementation.
- [GitHub Search API](https://docs.github.com/en/rest/search/search#search-repositories)
  defines the current public-reference lookup surface and rate limits.
