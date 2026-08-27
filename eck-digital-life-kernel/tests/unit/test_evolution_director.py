from __future__ import annotations

from typing import Any

import pytest


async def _publish_repeated_supervisor_failures(application, count: int) -> None:
    for attempt in range(count):
        await application.events.publish(
            "BackgroundWorkerFailed",
            application.settings.identity,
            {
                "worker": "supervisor",
                "type": "RuntimeError",
                "detail": f"SQLite database is locked on attempt {attempt + 1}",
            },
        )


@pytest.mark.asyncio
async def test_repeated_failure_scan_is_deduplicated_and_idempotent(application) -> None:
    declared_targets = {
        *application.evolution_director._event_targets.values(),
        *application.evolution_director._worker_targets.values(),
    }
    assert all(
        (application.evolution_director.project_root / relative).is_file()
        for target in declared_targets
        for relative in target
    )
    await _publish_repeated_supervisor_failures(application, 3)

    first = await application.evolution_director.scan()
    items = application.evolution_director.list_opportunities()

    assert first["scan"]["created"] == 1
    assert first["scan"]["updated"] == 2
    assert len(items) == 1
    assert items[0]["occurrence_count"] == 3
    assert items[0]["status"] == "waiting_heldout_pack"
    assert items[0]["target_files"] == ["src/eck/services/supervisor.py"]
    assert items[0]["test_files"] == ["tests/unit/test_supervisor.py"]
    assert items[0]["readiness"]["ready_to_draft"] is False

    second = await application.evolution_director.scan()
    assert second["scan"]["created"] == 0
    assert second["scan"]["updated"] == 0
    assert application.evolution_director.list_opportunities()[0][
        "occurrence_count"
    ] == 3

    await _publish_repeated_supervisor_failures(application, 1)
    await application.evolution_director.scan()
    assert application.evolution_director.list_opportunities()[0][
        "occurrence_count"
    ] == 4


@pytest.mark.asyncio
async def test_director_requires_independent_pack_before_candidate_drafting(
    application,
    monkeypatch,
) -> None:
    await _publish_repeated_supervisor_failures(application, 3)
    await application.evolution_director.scan()
    calls: list[Any] = []

    async def forbidden_candidate(request):
        calls.append(request)
        raise AssertionError("Candidate drafting must not run without a held-out pack.")

    monkeypatch.setattr(application.core_lab, "create_candidate", forbidden_candidate)
    result = await application.evolution_director.run_if_needed(force=True)

    assert result["run"] == "no_independently_evaluable_opportunity"
    assert calls == []


@pytest.mark.asyncio
async def test_attached_pack_enables_candidate_but_stops_for_human_approval(
    application,
    monkeypatch,
) -> None:
    await _publish_repeated_supervisor_failures(application, 3)
    await application.evolution_director.scan()
    opportunity = application.evolution_director.list_opportunities()[0]
    opportunity_id = str(opportunity["opportunity_id"])

    with pytest.raises(FileNotFoundError):
        await application.evolution_director.attach_pack(
            opportunity_id,
            "missing-heldout-pack",
        )

    pack_dir = application.evolution_transactions.heldout_root / "supervisor-lock-fix"
    pack_dir.mkdir(parents=True)
    (pack_dir / "test_hidden.py").write_text(
        "def test_hidden():\n    assert True\n",
        encoding="utf-8",
    )
    await application.evolution_transactions.register_heldout_pack(
        pack_id="supervisor-lock-fix",
        description="Independent regression for repeated supervisor database locking.",
        test_files=("test_hidden.py",),
        change_kind="correctness",
        minimum_speedup_percent=0,
        allow_non_regression=False,
    )
    attached = await application.evolution_director.attach_pack(
        opportunity_id,
        "supervisor-lock-fix",
    )
    assert attached["status"] == "ready"
    assert attached["readiness"]["ready_to_draft"] is True

    requests: list[Any] = []

    async def create_candidate(request):
        requests.append(request)
        return {
            "candidate_id": "core-candidate_" + "a" * 32,
            "validation": {"passed": True},
        }

    async def evaluate(candidate_id: str, pack_id: str):
        assert candidate_id == "core-candidate_" + "a" * 32
        assert pack_id == "supervisor-lock-fix"
        return {
            "transaction_id": "evolution-tx_" + "b" * 32,
            "candidate_id": candidate_id,
            "status": "awaiting_human_approval",
            "error": "",
        }

    monkeypatch.setattr(application.core_lab, "create_candidate", create_candidate)
    monkeypatch.setattr(application.evolution_transactions, "evaluate", evaluate)

    result = await application.evolution_director.run_opportunity(opportunity_id)

    assert result["status"] == "awaiting_human_approval"
    assert len(requests) == 1
    assert requests[0].target_files == ("src/eck/services/supervisor.py",)
    assert result["candidate_id"] == "core-candidate_" + "a" * 32
    with pytest.raises(RuntimeError, match="sealed or human-reviewed"):
        await application.evolution_director.attach_pack(
            opportunity_id,
            "supervisor-lock-fix",
        )
