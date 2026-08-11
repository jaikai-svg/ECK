from __future__ import annotations

import pytest

from eck.domain.enums import RuntimeSkillStatus
from eck.modules.skills.lifecycle import (
    RuntimeSkillTransitionPolicy,
    SkillLifecyclePhase,
    SkillSourceKind,
)


def test_skill_lifecycle_unifies_verified_memory_and_runtime_skills(application) -> None:
    status = application.skill_lifecycle.status()

    assert status["schema_version"] == "skill-lifecycle.v1"
    assert status["counts"]["runtime"] == 6
    runtime = [
        item for item in status["items"] if item["source_kind"] == SkillSourceKind.RUNTIME.value
    ]
    assert runtime
    assert all(item["phase"] == SkillLifecyclePhase.CANDIDATE.value for item in runtime)


def test_runtime_skill_transition_policy_blocks_retired_reactivation() -> None:
    with pytest.raises(ValueError, match="retired -> active"):
        RuntimeSkillTransitionPolicy.require(
            RuntimeSkillStatus.RETIRED,
            RuntimeSkillStatus.ACTIVE,
        )

