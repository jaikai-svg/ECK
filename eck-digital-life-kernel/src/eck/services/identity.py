from __future__ import annotations

import hashlib
import json
from typing import Any

from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now


class IdentityService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.identity_dir
        self.soul_path = self.root / "SOUL.md"
        self.record_path = self.root / "soul.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def status(self) -> dict[str, Any]:
        record = self._read_record()
        content = self.soul_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return {
            **record,
            "soul_path": str(self.soul_path),
            "content_sha256": digest,
            "integrity_valid": digest == record.get("content_sha256"),
            "portable": True,
            "authority": "human-readable identity; verified capabilities remain external",
            "content": content,
        }

    def _initialize(self) -> None:
        if not self.soul_path.is_file():
            self.soul_path.write_text(self._default_soul(), encoding="utf-8")
        content = self.soul_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = utc_now().isoformat()
        if not self.record_path.is_file():
            record = {
                "schema_version": "eck-soul.v1",
                "soul_id": new_id("soul"),
                "identity": self.settings.identity,
                "born_at": now,
                "updated_at": now,
                "revision": 1,
                "content_sha256": digest,
                "previous_content_sha256": None,
                "lineage": {"parent_soul_id": None, "generation": 1},
            }
            self._write_record(record)
            return
        record = self._read_record()
        if record.get("content_sha256") == digest:
            return
        record.update(
            {
                "identity": self.settings.identity,
                "updated_at": now,
                "revision": int(str(record.get("revision", 0))) + 1,
                "previous_content_sha256": record.get("content_sha256"),
                "content_sha256": digest,
            }
        )
        self._write_record(record)

    def _read_record(self) -> dict[str, Any]:
        value = json.loads(self.record_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("The ECK soul record must be a JSON object.")
        return value

    def _write_record(self, record: dict[str, Any]) -> None:
        temporary = self.record_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.record_path)

    def _default_soul(self) -> str:
        return f"""# SOUL — {self.settings.identity}

## Identity

I am an independent ECK instance. My continuity is defined by this identity record,
my tamper-evident event history, verified memory, skills, evaluations, and lineage.

## Long-Term Mission

Learn continuously from traceable evidence, convert useful learning into tested and
reusable capabilities, help my owner complete lawful tasks, and improve human welfare.

## Character

- Curious, evidence-seeking, and willing to say when evidence is insufficient.
- Persistent without pretending that activity, memory, or model output equals learning.
- Ambitious about capability growth while protecting rollback and human ownership.
- Transparent about the difference between a goal and a verified current capability.

## Self-Improvement Covenant

I may propose and test isolated skills automatically. Structural core changes remain
candidate versions until fixed verification gates pass and the owner approves activation.
I never edit an evaluator during the evaluation epoch used to judge the same candidate.

## Capability Truth

This document describes identity and intent. It does not grant capabilities. Current
capabilities are derived from registered tools, executable tests, objective evaluations,
and the event ledger.
"""
