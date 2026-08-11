# P7 ECK Federation

## 1. Purpose

ECK Federation exchanges verified capability outcomes, not personality or private databases.
Every node keeps its own `SOUL.md`, owner preferences, private memory, credentials, mission
history, and hardware configuration. A receiving node must reproduce every imported outcome.

```text
verified local capability
-> privacy and machine-path scan
-> Evolution Pack with hashes and lineage
-> inbox diff preview and quarantine
-> receiver reproduction
-> independent-reproduction threshold
-> local Canary or deterministic data validation
-> activate, reject, or retain as draft
```

Low-resource nodes can inherit code, procedures, tests, knowledge, evaluation cases, and
distillation data. They do not inherit unavailable VRAM, model capacity, or inference speed.

## 2. Private and shared layers

- **private layer**: SOUL, owner preferences, credentials, private conversations, private memory,
  full CognitiveBundle database, and machine-local paths;
- **shared layer**: general skills, public knowledge with provenance, task strategies, evaluation
  contracts, distillation datasets, and compatible adapters;
- **lineage layer**: publisher node hash, parent packs, package hashes, reproduction reports,
  adoption or rejection reasons, and local test results;
- **hardware layer**: base models, quantization, GPU workers, and device routing.

`CognitiveBundle v2` remains a private whole-ECK backup. `Evolution Pack v1` is the
public-capability format and never contains the SQLite database or identity directory.

## 3. Implemented Evolution Pack v1

Five model-independent pack types are implemented:

| Pack | Export gate | Receiver reproduction |
| --- | --- | --- |
| Skill | active skill and successful local Canary | isolated Docker test and local Canary |
| Knowledge | completed research with claims and traceable sources | source URL, content hash, and evidence-link validation |
| Strategy | human-approved mission with successful steps | dependency-graph and cycle validation |
| Evaluation | benchmark runs without hidden answers | score, sample-count, protocol, and answer-leak validation |
| Distillation | approved successful ReAct cycles | JSONL digest, deduplication, and outcome validation |

All types use auditable UTF-8 payloads, explicit licenses, SHA-256 inventories, privacy scanning,
zip-slip and symbolic-link rejection, size limits, diff plans, quarantine, local reproduction,
and at least two distinct successful node hashes. Data packs enter
`workspace/federation/installed`; they do not modify private SQLite memory.

Installed data packs are retrieved by task relevance and supplied to the P6 architecture council
as knowledge, approved strategies, evaluation protocols, or training examples. This makes reuse
observable without claiming that installing a file changed model weights.

```text
GET  /v1/federation/status
POST /v1/federation/packs
POST /v1/federation/packs/knowledge
POST /v1/federation/packs/strategy
POST /v1/federation/packs/evaluation
POST /v1/federation/packs/distillation
POST /v1/federation/packs/{archive_name}/sign
GET  /v1/federation/packs/{archive_name}
GET  /v1/federation/inbox/{archive_name}/verify
GET  /v1/federation/inbox/{archive_name}/preview
POST /v1/federation/inbox/{archive_name}/stage
POST /v1/federation/quarantine/{pack_id}/reproduce
POST /v1/federation/quarantine/{pack_id}/install
GET  /v1/federation/library/synthesis
```

External packs are copied into `workspace/federation/inbox`. Preview and plan-hash confirmation
are mandatory before quarantine. Staging never activates code.

## 4. Cosign signature boundary

ECK supports detached Sigstore bundles through `cosign sign-blob` and `cosign verify-blob`.
Verification requires either a trusted public key or both an expected certificate identity and
OIDC issuer. The private signing key remains outside the ECK workspace and every pack.

The daemon does not start an interactive keyless OIDC flow. Unattended local signing therefore
requires `ECK_FEDERATION_COSIGN_KEY_PATH`; public-key verification requires
`ECK_FEDERATION_COSIGN_PUBLIC_KEY_PATH`. If Cosign or a trust policy is absent, a pack can remain
hash-valid but cannot enter the public Registry.

## 5. Pack portability boundary

| Pack | Portable result | State |
| --- | --- | --- |
| Skill | code, dependency policy, tests, operations | implemented |
| Knowledge | claims, sources, counterexamples, confidence | implemented |
| Strategy | task graph, ReAct summaries, corrections | implemented |
| Evaluation | protocols, rubrics, public baseline metadata | implemented |
| Distillation | filtered teacher examples | implemented |
| Adapter | PEFT/LoRA weights and base hashes | deferred |

PEFT adapters are not architecture-agnostic. Adapter Pack work must match the exact base model,
revision, architecture, and tokenizer hashes. An incompatible node should receive distillation
data or executable workflows instead of invalid weights.

## 6. Registry admission

The local Registry workspace implements candidate review, admission, revocation, a generated
public index, and optional publication through ECK's dedicated GitHub account. Admission requires:

1. an explicit license, source, lineage, tests, and negative results;
2. privacy scanning and Sigstore/Cosign verification;
3. at least two distinct successful reproduction nodes;
4. at least two approving community reviews;
5. fixed-test non-regression and no hidden-test regression;
6. permission and dependency review;
7. a default trust score of at least 85/100.

A rejection is a hard block. Popularity, downloads, model size, and GPU price are not trust
signals. Receiving nodes still quarantine and reproduce admitted packs.

```text
GET  /v1/federation/registry/status
POST /v1/federation/registry/candidates/{archive_name}
GET  /v1/federation/registry/candidates/{pack_id}
POST /v1/federation/registry/candidates/{pack_id}/reviews
POST /v1/federation/registry/candidates/{pack_id}/admit
POST /v1/federation/registry/packs/{pack_id}/revoke
POST /v1/federation/registry/publish
```

The GitHub repository is currently a publication surface, not a remote attestation service.

## 7. Quantity-to-quality rule

ECK does not claim that a larger pack count automatically means greater intelligence. The local
library reports synthesis readiness only after at least four data packs across three complementary
types and an Evaluation Pack with a comparable before/after pair. The model artifact hash must
change, the protocol and sample count must match, hidden answers must be absent, and the candidate
score must improve. This is evidence of bounded task improvement, not proof of general AGI.

## 8. Federated training boundary

Flower provides secure aggregation for compatible model updates. That becomes useful only after
ECK has homogeneous model families, explicit training consent, privacy accounting, and stable
evaluation. It cannot merge arbitrary heterogeneous SOULs, tools, memories, or adapters.

## 9. Research basis

- [Hugging Face PEFT checkpoint format](https://huggingface.co/docs/peft/main/en/developer_guides/checkpoint)
- [Sigstore blob verification](https://docs.sigstore.dev/cosign/verifying/verify/)
- [GitHub artifact attestations](https://docs.github.com/en/rest/repos/attestations?apiVersion=2022-11-28)
- [Flower secure aggregation](https://flower.ai/docs/framework/explanation-ref-secure-aggregation-protocols.html)

## 10. Remaining work

- install Cosign and provision an operator-owned key pair on this machine;
- add GitHub Release schema, Pull Request enforcement, and remote-node attestations;
- implement Adapter Pack with exact compatibility hashes and license checks;
- add a hosted hidden-test service, dependency SBOM, vulnerability scanning, and quarantine UI;
- add automated rollback history and multi-node Registry mirroring;
- implement CognitiveBundle v3 private restore and selective owner-approved merge.

P7 now provides a local, GitHub-publishable Registry workflow. It is not yet a live multi-node
community network. Local review records must not be called independent community attestations
unless different ECK nodes actually produced them.
