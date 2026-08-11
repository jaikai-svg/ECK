from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from eck.domain.enums import MissionCycleStatus, MissionStatus, MissionStepStatus
from eck.storage.sqlite import SQLiteStore

DATA_PACK_TYPES = {
    "knowledge_pack",
    "strategy_pack",
    "evaluation_pack",
    "distillation_pack",
}


class EvolutionDataPackService:
    """Build and reproduce public, model-independent Evolution Pack payloads."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def build_knowledge(self, run_ids: tuple[str, ...]) -> dict[str, Any]:
        selected = self._unique_ids(run_ids, label="research run", maximum=50)
        runs: list[dict[str, Any]] = []
        source_count = 0
        claim_count = 0
        for run_id in selected:
            run = self.store.get_research_run(run_id)
            if run["status"] != "completed":
                raise ValueError("Knowledge Packs require completed research runs.")
            if not run["claims"] or not run["sources"]:
                raise ValueError("Knowledge Packs require claims and traceable sources.")
            source_index = {
                str(source["snapshot_id"]): index
                for index, source in enumerate(run["sources"])
            }
            claim_index = {
                str(claim["claim_id"]): index for index, claim in enumerate(run["claims"])
            }
            sources = [
                {
                    "url": source["canonical_url"],
                    "domain": source["source_domain"],
                    "title": source["title"],
                    "provider": source["provider"],
                    "published_at": source["published_at"],
                    "fetched_at": source["fetched_at"],
                    "content_sha256": source["content_sha256"],
                }
                for source in run["sources"]
            ]
            claims = [
                {
                    "claim": claim["claim"],
                    "kind": claim["kind"],
                    "status": claim["status"],
                    "confidence": claim["confidence"],
                    "rationale": claim["rationale"],
                }
                for claim in run["claims"]
            ]
            links = [
                {
                    "claim_index": claim_index[str(link["claim_id"])],
                    "source_index": source_index[str(link["snapshot_id"])],
                    "stance": link["stance"],
                    "note": link["note"],
                    "independence_key": link["independence_key"],
                }
                for link in run["evidence_links"]
                if str(link["claim_id"]) in claim_index
                and str(link["snapshot_id"]) in source_index
            ]
            runs.append(
                {
                    "topic": run["topic"],
                    "conclusion_status": run["conclusion_status"],
                    "conclusion": run["conclusion"],
                    "confidence": run["confidence"],
                    "claims": claims,
                    "sources": sources,
                    "evidence_links": links,
                    "finished_at": run["finished_at"],
                }
            )
            source_count += len(sources)
            claim_count += len(claims)
        document: dict[str, Any] = {
            "schema": "eck-knowledge-pack.v1",
            "runs": runs,
            "quality": {
                "research_runs": len(runs),
                "claims": claim_count,
                "traceable_sources": source_count,
            },
        }
        return self._result(
            "knowledge_pack",
            name=self._pack_name("knowledge", [run["topic"] for run in runs]),
            description="Verified research claims with provenance and counter-evidence links.",
            payloads={"knowledge.json": self._json_bytes(document)},
            source_refs=selected,
            metrics=document["quality"],
        )

    def build_strategy(self, mission_id: str) -> dict[str, Any]:
        mission = self.store.get_mission(mission_id)
        if mission.status is not MissionStatus.APPROVED:
            raise ValueError("Strategy Packs require a human-approved mission.")
        pattern = mission.progress.get("learning_pattern")
        if not isinstance(pattern, dict):
            raise ValueError("The approved mission has no reusable learning pattern.")
        steps = self.store.list_mission_steps(mission_id)
        if not steps or any(step.status is not MissionStepStatus.SUCCEEDED for step in steps):
            raise ValueError("Strategy Packs require a fully successful mission step graph.")
        cycles = self.store.list_mission_react_cycles(mission_id, limit=1000)
        successful = [cycle for cycle in cycles if cycle.status is MissionCycleStatus.SUCCEEDED]
        document: dict[str, Any] = {
            "schema": "eck-strategy-pack.v1",
            "title": mission.title,
            "project_type": str(pattern.get("project_type", "general")),
            "tags": self._string_list(pattern.get("tags", []), maximum=30),
            "learning_pattern": self._public_value(pattern),
            "step_graph": [
                {
                    "step_key": step.step_key,
                    "sequence": step.sequence,
                    "action_kind": step.action_kind,
                    "objective": step.objective,
                    "depends_on": list(step.depends_on),
                    "attempts": step.attempts,
                }
                for step in steps
            ],
            "successful_corrections": [
                {
                    "reason": cycle.reason_summary,
                    "action_kind": str(cycle.action.get("kind", "")),
                    "correction": cycle.correction,
                    "observation": self._public_value(cycle.observation),
                }
                for cycle in successful
            ],
            "quality": {
                "approved": True,
                "successful_steps": len(steps),
                "successful_react_cycles": len(successful),
                "quality_score": pattern.get("quality_score", 0),
            },
        }
        return self._result(
            "strategy_pack",
            name=self._pack_name("strategy", [mission.title]),
            description="Human-approved task decomposition and successful correction strategy.",
            payloads={"strategy.json": self._json_bytes(document)},
            source_refs=(mission_id,),
            metrics=document["quality"],
        )

    def build_evaluation(self, run_ids: tuple[str, ...]) -> dict[str, Any]:
        selected = self._unique_ids(run_ids, label="benchmark run", maximum=100)
        by_id = {run.run_id: run for run in self.store.list_benchmark_runs(limit=10000)}
        missing = [run_id for run_id in selected if run_id not in by_id]
        if missing:
            raise KeyError(f"Unknown benchmark run: {missing[0]}")
        runs: list[dict[str, Any]] = []
        for run_id in selected:
            record = by_id[run_id]
            protocol = self._public_value(record.protocol)
            if self._contains_hidden_answers(protocol):
                raise ValueError("Evaluation Packs cannot contain hidden-test answers.")
            runs.append(
                {
                    "suite": record.suite.value,
                    "benchmark_version": record.benchmark_version,
                    "model": record.model,
                    "model_artifact_hash": record.model_artifact_hash,
                    "evaluator": record.evaluator,
                    "score": record.score,
                    "sample_count": record.sample_count,
                    "protocol": protocol,
                    "notes": record.notes,
                    "created_at": record.created_at.isoformat(),
                }
            )
        buckets: dict[tuple[object, ...], list[int]] = {}
        for index, run in enumerate(runs):
            key = (
                run["suite"],
                run["benchmark_version"],
                run["evaluator"],
                run["sample_count"],
                self._digest(run["protocol"]),
            )
            buckets.setdefault(key, []).append(index)
        comparisons: list[dict[str, Any]] = []
        for indexes in buckets.values():
            ordered = sorted(indexes, key=lambda index: str(runs[index]["created_at"]))
            if len(ordered) < 2:
                continue
            baseline_index = ordered[0]
            candidate_index = ordered[-1]
            baseline = runs[baseline_index]
            candidate = runs[candidate_index]
            model_changed = bool(
                baseline.get("model_artifact_hash")
                and candidate.get("model_artifact_hash")
                and baseline["model_artifact_hash"] != candidate["model_artifact_hash"]
            )
            comparisons.append(
                {
                    "baseline_index": baseline_index,
                    "candidate_index": candidate_index,
                    "score_delta": candidate["score"] - baseline["score"],
                    "model_artifact_changed": model_changed,
                    "comparable_protocol": True,
                }
            )
        improvement_verified = any(
            comparison["score_delta"] > 0 and comparison["model_artifact_changed"]
            for comparison in comparisons
        )
        document: dict[str, Any] = {
            "schema": "eck-evaluation-pack.v1",
            "runs": runs,
            "comparisons": comparisons,
            "quality": {
                "benchmark_runs": len(runs),
                "total_samples": sum(run["sample_count"] for run in runs),
                "mean_score": sum(run["score"] for run in runs) / len(runs),
                "hidden_answers_included": False,
                "comparable_pairs": len(comparisons),
                "improvement_verified": improvement_verified,
            },
        }
        return self._result(
            "evaluation_pack",
            name=self._pack_name("evaluation", [run["suite"] for run in runs]),
            description="Reproducible benchmark metadata, protocols, and observed scores.",
            payloads={"evaluation.json": self._json_bytes(document)},
            source_refs=selected,
            metrics=document["quality"],
        )

    def build_distillation(self, mission_ids: tuple[str, ...]) -> dict[str, Any]:
        selected = self._unique_ids(mission_ids, label="mission", maximum=50)
        examples: list[dict[str, Any]] = []
        seen: set[str] = set()
        for mission_id in selected:
            mission = self.store.get_mission(mission_id)
            if mission.status is not MissionStatus.APPROVED:
                raise ValueError("Distillation Packs require human-approved missions.")
            steps = {step.step_id: step for step in self.store.list_mission_steps(mission_id)}
            cycles = self.store.list_mission_react_cycles(mission_id, limit=1000)
            for cycle in reversed(cycles):
                step = steps.get(cycle.step_id)
                if step is None or cycle.status is not MissionCycleStatus.SUCCEEDED:
                    continue
                example = {
                    "instruction": step.objective,
                    "context": {
                        "project_type": mission.progress.get("project_type", "general"),
                        "action_kind": step.action_kind,
                        "dependencies": list(step.depends_on),
                    },
                    "reason_summary": cycle.reason_summary,
                    "action": self._public_value(cycle.action),
                    "observation": self._public_value(cycle.observation),
                    "correction": cycle.correction,
                    "outcome": "succeeded",
                }
                digest = self._digest(example)
                if digest in seen:
                    continue
                seen.add(digest)
                examples.append({"example_sha256": digest, **example})
        if not examples:
            raise ValueError("No successful ReAct examples are available for distillation.")
        payload = "".join(
            json.dumps(example, ensure_ascii=False, sort_keys=True) + "\n"
            for example in examples
        ).encode("utf-8")
        metadata = {
            "schema": "eck-distillation-pack.v1",
            "format": "jsonl",
            "examples": len(examples),
            "source_missions": len(selected),
            "human_approved_only": True,
        }
        return self._result(
            "distillation_pack",
            name=self._pack_name("distillation", [str(len(examples)), "react"]),
            description="Deduplicated, approved ReAct trajectories for student-model training.",
            payloads={
                "distillation.jsonl": payload,
                "metadata.json": self._json_bytes(metadata),
            },
            source_refs=selected,
            metrics=metadata,
        )

    def reproduce(self, pack_type: str, payload_root: Path) -> dict[str, Any]:
        validators = {
            "knowledge_pack": self._validate_knowledge,
            "strategy_pack": self._validate_strategy,
            "evaluation_pack": self._validate_evaluation,
            "distillation_pack": self._validate_distillation,
        }
        validator = validators.get(pack_type)
        if validator is None:
            raise ValueError(f"Unsupported data pack type: {pack_type}")
        try:
            metrics = validator(payload_root)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return {"success": False, "detail": str(exc), "metrics": {}}
        return {
            "success": True,
            "detail": f"{pack_type} passed deterministic receiver validation.",
            "metrics": metrics,
        }

    def _validate_knowledge(self, root: Path) -> dict[str, Any]:
        value = self._read_object(root / "knowledge.json", "eck-knowledge-pack.v1")
        runs = self._nonempty_list(value, "runs")
        claims = 0
        sources = 0
        for run in runs:
            run_claims = self._nonempty_list(run, "claims")
            run_sources = self._nonempty_list(run, "sources")
            for source in run_sources:
                url = str(source.get("url", ""))
                if not url.startswith(("http://", "https://")):
                    raise ValueError("Knowledge source URL is not traceable HTTP(S).")
                if not str(source.get("content_sha256", "")):
                    raise ValueError("Knowledge source has no content hash.")
            claims += len(run_claims)
            sources += len(run_sources)
        return {"runs": len(runs), "claims": claims, "sources": sources}

    def _validate_strategy(self, root: Path) -> dict[str, Any]:
        value = self._read_object(root / "strategy.json", "eck-strategy-pack.v1")
        steps = self._nonempty_list(value, "step_graph")
        keys = {str(step.get("step_key", "")) for step in steps}
        if "" in keys or len(keys) != len(steps):
            raise ValueError("Strategy step keys are missing or duplicated.")
        for step in steps:
            unknown = set(step.get("depends_on", [])) - keys
            if unknown:
                raise ValueError("Strategy step graph contains an unknown dependency.")
        self._topological_order(steps)
        return {"steps": len(steps), "corrections": len(value.get("successful_corrections", []))}

    def _validate_evaluation(self, root: Path) -> dict[str, Any]:
        value = self._read_object(root / "evaluation.json", "eck-evaluation-pack.v1")
        runs = self._nonempty_list(value, "runs")
        samples = 0
        for run in runs:
            score = float(run.get("score", -1))
            count = int(run.get("sample_count", 0))
            if not 0 <= score <= 1 or count < 1:
                raise ValueError("Evaluation score or sample count is invalid.")
            if self._contains_hidden_answers(run.get("protocol", {})):
                raise ValueError("Evaluation payload exposes hidden-test answers.")
            samples += count
        comparisons = value.get("comparisons", [])
        if not isinstance(comparisons, list):
            raise ValueError("Evaluation comparisons must be a list.")
        for comparison in comparisons:
            if not isinstance(comparison, dict):
                raise ValueError("Evaluation comparison is invalid.")
            baseline = int(comparison.get("baseline_index", -1))
            candidate = int(comparison.get("candidate_index", -1))
            if baseline < 0 or candidate < 0 or baseline >= len(runs) or candidate >= len(runs):
                raise ValueError("Evaluation comparison references an unknown run.")
        return {"runs": len(runs), "samples": samples, "comparisons": len(comparisons)}

    def _validate_distillation(self, root: Path) -> dict[str, Any]:
        metadata = self._read_object(root / "metadata.json", "eck-distillation-pack.v1")
        lines = (root / "distillation.jsonl").read_text(encoding="utf-8").splitlines()
        examples = [json.loads(line) for line in lines if line.strip()]
        if not examples or len(examples) != int(metadata.get("examples", -1)):
            raise ValueError("Distillation metadata does not match its JSONL examples.")
        digests: set[str] = set()
        for example in examples:
            digest = str(example.pop("example_sha256", ""))
            if digest != self._digest(example) or digest in digests:
                raise ValueError("Distillation example digest is invalid or duplicated.")
            if example.get("outcome") != "succeeded" or not example.get("instruction"):
                raise ValueError("Distillation example is not a successful trajectory.")
            digests.add(digest)
        return {"examples": len(examples), "unique_examples": len(digests)}

    @staticmethod
    def _result(
        pack_type: str,
        *,
        name: str,
        description: str,
        payloads: dict[str, bytes],
        source_refs: tuple[str, ...],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "pack_type": pack_type,
            "capability": {
                "name": name,
                "version": "1.0.0",
                "description": description,
                "category": pack_type.removesuffix("_pack"),
                "tags": sorted({token for token in name.split("-") if token}),
            },
            "payloads": payloads,
            "source_refs": source_refs,
            "metrics": metrics,
        }

    @staticmethod
    def _unique_ids(values: tuple[str, ...], *, label: str, maximum: int) -> tuple[str, ...]:
        selected = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not selected:
            raise ValueError(f"At least one {label} is required.")
        if len(selected) > maximum:
            raise ValueError(f"A pack can contain at most {maximum} {label}s.")
        return selected

    @staticmethod
    def _string_list(value: object, *, maximum: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item)[:200] for item in value[:maximum] if str(item).strip()]

    @classmethod
    def _public_value(cls, value: object, *, depth: int = 0) -> Any:
        if depth > 5:
            return "[depth-limited]"
        if isinstance(value, dict):
            blocked = ("password", "secret", "token", "credential", "owner", "private")
            return {
                str(key)[:120]: cls._public_value(item, depth=depth + 1)
                for key, item in value.items()
                if not any(part in str(key).casefold() for part in blocked)
            }
        if isinstance(value, (list, tuple)):
            return [cls._public_value(item, depth=depth + 1) for item in value[:100]]
        if isinstance(value, str):
            return value[:4000]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return str(value)[:1000]

    @staticmethod
    def _contains_hidden_answers(value: object) -> bool:
        text = json.dumps(value, ensure_ascii=False).casefold()
        markers = ("hidden_answers", "hidden_answer_key", "secret_answer_key")
        return any(marker in text for marker in markers)

    @staticmethod
    def _read_object(path: Path, schema: str) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != schema:
            raise ValueError(f"Invalid or unsupported payload schema: {path.name}")
        return value

    @staticmethod
    def _nonempty_list(value: dict[str, Any], key: str) -> list[dict[str, Any]]:
        items = value.get(key)
        if (
            not isinstance(items, list)
            or not items
            or not all(isinstance(item, dict) for item in items)
        ):
            raise ValueError(f"Payload field {key!r} must be a non-empty object list.")
        return items

    @staticmethod
    def _topological_order(steps: list[dict[str, Any]]) -> list[str]:
        remaining = {str(step["step_key"]): set(step.get("depends_on", [])) for step in steps}
        ordered: list[str] = []
        while remaining:
            ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
            if not ready:
                raise ValueError("Strategy step graph contains a dependency cycle.")
            for key in ready:
                ordered.append(key)
                remaining.pop(key)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return ordered

    @staticmethod
    def _pack_name(prefix: str, values: list[str]) -> str:
        source = "-".join(values).casefold()
        normalized = "".join(char if char.isalnum() else "-" for char in source)
        normalized = "-".join(part for part in normalized.split("-") if part)[:48]
        suffix = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
        return f"{prefix}-{normalized or 'verified'}-{suffix}"

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

    @staticmethod
    def _digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
