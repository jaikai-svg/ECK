from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_evolution_status_distinguishes_verified_and_future_capabilities(
    application, monkeypatch
) -> None:
    async def worker_health():
        return {"available": True, "detail": "test worker"}

    monkeypatch.setattr(application.worker, "health", worker_health)

    status = await application.evolution.status()

    assert status["classification"] == "partial_self_improvement_not_recursive_agi"
    assert status["verified_now"]["skill_self_authoring"] is True
    assert status["verified_now"]["automatic_failed_skill_repair"] is True
    assert status["verified_now"]["isolated_worker_available"] is True
    assert status["not_yet_verified"]["automatic_structural_core_patch"] is True
    assert status["not_yet_verified"]["dual_kernel_zero_downtime_handoff"] is True
    assert status["safety_boundary"]["structural_core_change_after_tests"] == (
        "human_approval_required"
    )
