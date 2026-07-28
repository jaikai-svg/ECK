from __future__ import annotations

import pytest

from eck.services.demos import DemoService


@pytest.mark.asyncio
async def test_safe_code_enters_candidate_skill(application) -> None:
    result = await DemoService(application).safe_code()
    assert result["status"] == "verified_success"
    experiences = application.store.list_experiences()
    assert experiences[0].admitted
    knowledge = application.store.list_knowledge()
    assert knowledge[0].admitted
    assert knowledge[0].externally_grounded
    assert knowledge[0].reproducible
    reflections = application.store.list_reflections()
    assert reflections[0].generator == "deterministic-template.v1"
    assert reflections[0].verification_report_id
    skills = application.store.list_skills()
    assert skills[0].success_count == 1
    assert not skills[0].active


@pytest.mark.asyncio
async def test_gridworld_second_attempt_uses_less_exploration(application) -> None:
    result = await DemoService(application).gridworld()
    measure = result["learning_measure"]
    assert measure["fewer_steps_after_experience"]
    skills = [
        skill
        for skill in application.store.list_skills()
        if skill.capability == "gridworld.navigate"
    ]
    assert skills[0].success_count == 2
    assert skills[0].active
