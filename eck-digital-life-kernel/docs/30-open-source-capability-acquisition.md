# Open-source capability acquisition

## Purpose

ECK should not rebuild every known solution from zero. Public repositories can shorten the
path from a capability gap to a tested local skill, but popularity is not proof of correctness,
security, license compatibility, or fitness for this machine.

The implemented acquisition loop is:

1. Accept an operator capability contract and up to three public GitHub repositories.
2. Resolve repository metadata, license, default branch, commit SHA, tree profile, and README.
3. Classify the source as an executable pattern library or a strategy/role library.
4. Stop automatic adaptation when the license is unknown or outside the permissive allowlist.
5. Treat all upstream text as untrusted research material and never execute upstream code.
6. Generate a small ECK-native implementation with only declared permissions and dependencies.
7. Reject inconsistent tests, undeclared imports, dynamic execution, and unsupported entrypoints.
8. Restore the Docker worker when necessary, run isolated tests, repeat Canary validation, and
   activate only after every gate passes.
9. Store `provenance.json` with the request, source identity, pinned commit, and policy decision.

## Strategy libraries

The MIT-licensed Agency Agents repository is predominantly a role, workflow, deliverable, and
review-rubric library. ECK therefore uses attributed, paraphrased profiles for software
architecture, frontend engineering, code review, multi-agent architecture, and UI finish-gate
review. It is not counted as five executable skills.

## Immediate teaching

The Dashboard's **Learning Results** page includes **Teach ECK an executable skill now**.
Provide:

- a narrow topic;
- a stable skill identifier when desired;
- the exact input, output, edge cases, and failure behavior;
- preferably a JSON array of immutable acceptance examples; and
- optional public GitHub repositories for provenance-aware reference.

Acceptance examples have this shape:

```json
[
  {
    "operation": "execute",
    "payload": {"text": "ECK"},
    "expected": {"length": 3},
    "context": {}
  }
]
```

ECK converts examples into tests that the coding model cannot rewrite during repair. The coding
model may rewrite the implementation, but only the operator can change the oracle. A failed
candidate remains failed evidence and does not increase the active-skill count.

## Background learning

Long-term themes guide research and do not guarantee an executable skill. The research-to-skill
bridge now checks every 30 minutes, requires at least six qualified research runs, avoids another
model call when no new evidence exists, validates only the newest unresolved candidate for each
skill, and automatically restores the worker image. This reduces idle repetition without
pretending that reading is equivalent to capability acquisition.

## Verified 100-tool campaign

The long-running `eck-agent-toolkit` campaign is separate from ordinary GitHub project
publication. It performs one bounded candidate cycle at a time and is serialized with the
supervisor, research-to-skill bridge, project lab, durable missions, and autonomous curriculum.
On the reference configuration it starts after one hour and then runs at most once every six
hours, which prevents multiple coding workloads from competing for an RTX 3060.

Repository popularity is used only for discovery. A candidate is counted only after all of these
gates pass:

1. The pinned repository is public, active, not a fork, and uses MIT, Apache-2.0, 0BSD,
   BSD-2-Clause, or BSD-3-Clause.
2. The ECK-native implementation passes deterministic AST, permission, dependency, and secret
   safety checks.
3. The isolated Docker worker passes the generated tests and all Canary replays.
4. At least two fixed, README-grounded input/output examples pass as immutable objective tests.
5. A fresh worker process reproduces the result after activation.

Only then does ECK export a hash-verified Skill Evolution Pack, add it to the versioned local
catalog, and attempt to update the dedicated `eck-agent-toolkit` repository. Failed discovery,
planning, testing, packaging, or publication is recorded but never increases the count.

The campaign deliberately adapts one bounded reusable capability from each repository. It does
not claim that a small local model has understood an entire upstream project merely because the
README was read. Status is available from `GET /v1/evolution/tool-campaign`, and one owner-driven
cycle can be requested with `POST /v1/evolution/tool-campaign/run`.

Autonomous GitHub commands are fail-closed. The runtime may read the dedicated account identity,
obtain its OAuth credential through GitHub CLI's credential storage, create a repository, or
update a verified repository. Account deletion, password changes, billing/payment operations,
Google-password access, and `gh auth login`, `logout`, or `refresh` are outside the command
allowlist.

## Current boundary

This mechanism improves ECK's reusable software workflows and tool layer. It does not train the
base model weights, prove generalization beyond the tests, or make an unreviewed repository safe.
Small local models can still fail simple repairs; immutable examples expose that limitation rather
than hiding it.
