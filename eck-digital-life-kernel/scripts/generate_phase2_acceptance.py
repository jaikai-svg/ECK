from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from eck.config import Settings
from eck.core.ids import new_id
from eck.core.time import utc_now
from eck.domain.enums import (
    ComparisonOperator,
    EvidenceSource,
    RiskLevel,
    TaskStatus,
    VerificationStatus,
)
from eck.domain.models import (
    ActionProposal,
    CapabilityResult,
    Evidence,
    SuccessContract,
    TaskCreate,
    VerificationCheck,
    VerificationReport,
)
from eck.modules.library.authoring import LibraryAuthoringService
from eck.services.missions import MissionService
from eck.storage.sqlite import SQLiteStore


def generate(output_dir: Path) -> dict[str, object]:
    runtime = output_dir.parent / ".phase2-acceptance-runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    store = SQLiteStore(runtime / "acceptance.sqlite3")
    store.initialize()
    settings = cast(
        Settings,
        SimpleNamespace(
            library_min_cards=5,
            library_min_chapters=1,
            library_min_relation_coverage=0.8,
            library_min_independent_source_ratio=1.0,
            library_min_applied_tasks=5,
            library_min_fixed_tests=3,
            library_min_hidden_tests=2,
            library_min_evaluation_score=1.0,
            library_books_dir=runtime / "library-books",
        ),
    )
    service = LibraryAuthoringService(
        settings,
        store,
        cast(MissionService, object()),
    )
    label = "library-domain:phase-2-acceptance-domain"
    for index in range(1, 6):
        _add_verified_card(
            store,
            index=index,
            labels=(
                label,
                "library-fixed-evaluation"
                if index <= 3
                else "library-hidden-evaluation",
            ),
        )
    domain = service.create_domain(
        title="Phase 2 Acceptance Domain",
        description=(
            "TEST-ONLY deterministic domain proving readiness gates, source traceability, "
            "relations, revisions, and duplicate suppression."
        ),
        knowledge_selector={
            "capability_prefixes": ["acceptance.phase2"],
            "required_capabilities": ["acceptance.phase2"],
        },
    )
    cards = domain["cards"]
    for left, right in zip(cards, cards[1:], strict=False):
        service.add_relation(
            source_knowledge_id=str(left["knowledge_id"]),
            target_knowledge_id=str(right["knowledge_id"]),
            relation_type="extension",
            rationale="Deterministic acceptance graph edge.",
            evidence_ids=[],
            verified=True,
        )
    report = service.evaluate(str(domain["domain_id"]))
    revision = service.author(
        str(domain["domain_id"]),
        reason="Phase 2 deterministic acceptance generation.",
    )
    final_domain = service.domain(str(domain["domain_id"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    _write(output_dir / "domain.json", final_domain)
    _write(output_dir / "knowledge-cards.json", final_domain["cards"])
    _write(output_dir / "readiness-report.json", report)
    _write(output_dir / "book-revision.json", revision)
    shutil.copy2(Path(str(revision["markdown_path"])), output_dir / "test-book.md")
    shutil.copy2(Path(str(revision["manifest_path"])), output_dir / "test-book-manifest.json")
    summary = {
        "test_only": True,
        "domain_id": domain["domain_id"],
        "card_count": len(cards),
        "readiness_passed": report["passed"],
        "book_revision": revision["revision"],
        "content_sha256": revision["content_sha256"],
        "source_database": "isolated generated acceptance database (not committed)",
    }
    _write(output_dir / "acceptance-summary.json", summary)
    shutil.rmtree(runtime)
    return summary


def _add_verified_card(
    store: SQLiteStore,
    *,
    index: int,
    labels: tuple[str, ...],
) -> None:
    goal = f"Verify Phase 2 acceptance claim {index}."
    action = ActionProposal(
        capability="acceptance.phase2",
        operation="verify",
        payload={"case": index},
        declared_risk=RiskLevel.LOW,
    )
    contract = SuccessContract(
        goal=goal,
        checks=(
            VerificationCheck(
                name="acceptance passed",
                path="passed",
                operator=ComparisonOperator.EQ,
                expected=True,
            ),
        ),
        required_evidence=(EvidenceSource.UNIT_TEST,),
        require_reproducible=True,
    )
    task_id = new_id("task")
    store.create_task(
        task_id,
        TaskCreate(goal=goal, success_contract=contract, action=action, labels=labels),
        RiskLevel.LOW,
    )
    evidence = (
        Evidence(
            source=EvidenceSource.UNIT_TEST,
            claim=f"Independent acceptance source A for case {index}.",
            payload={"url": f"eck://acceptance-a/case-{index}"},
        ),
        Evidence(
            source=EvidenceSource.FORMAL_CHECK,
            claim=f"Independent acceptance source B for case {index}.",
            payload={"url": f"eck://acceptance-b/case-{index}"},
        ),
    )
    now = utc_now()
    result = CapabilityResult(
        action_id=action.action_id,
        capability=action.capability,
        success=True,
        output={"passed": True, "case": index},
        evidence=evidence,
        cost_units=1,
        started_at=now,
        finished_at=now,
    )
    verification = VerificationReport(
        status=VerificationStatus.VERIFIED_SUCCESS,
        score=1.0,
        passed_checks=("acceptance passed",),
        evidence_ids=tuple(item.evidence_id for item in evidence),
        external_evidence_present=True,
        reproducible=True,
        reason="Deterministic acceptance fixture passed.",
    )
    store.update_task(
        task_id,
        status=TaskStatus.VERIFIED_SUCCESS,
        attempts=1,
        result=result,
        verification=verification,
    )
    store.add_knowledge(
        task_id=task_id,
        capability=action.capability,
        claim=f"Acceptance claim {index} is reproducibly verified by two test sources.",
        outcome=VerificationStatus.VERIFIED_SUCCESS,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        externally_grounded=True,
        reproducible=True,
        admitted=True,
    )
    store.add_reflection(
        task_id=task_id,
        capability=action.capability,
        outcome=VerificationStatus.VERIFIED_SUCCESS,
        observation=f"Case {index} passed fixed deterministic checks.",
        lesson="Formal publication requires frozen thresholds and traceable evidence.",
        next_step="Replace synthetic acceptance claims with real domain research before use.",
        verification_report_id=verification.report_id,
        evidence_ids=tuple(item.evidence_id for item in evidence),
    )


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deliverables/phase2-acceptance"),
    )
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
