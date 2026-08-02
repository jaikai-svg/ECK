from __future__ import annotations

from eck.domain.models import AutonomousActionContext
from eck.policy.autonomy import AutonomyGate


def test_disclosed_public_post_does_not_require_approval() -> None:
    decision = AutonomyGate().evaluate(
        AutonomousActionContext(
            action_type="publish",
            public_action=True,
            ai_disclosure_present=True,
        )
    )
    assert decision.allowed
    assert not decision.requires_approval


def test_public_action_without_ai_disclosure_is_blocked() -> None:
    decision = AutonomyGate().evaluate(
        AutonomousActionContext(action_type="public_reply", public_action=True)
    )
    assert not decision.allowed
    assert "disclose" in decision.reasons[0]


def test_paid_and_artificial_engagement_are_blocked() -> None:
    decision = AutonomyGate().evaluate(
        AutonomousActionContext(
            action_type="promote",
            uses_paid_api_or_real_money=True,
            artificial_engagement=True,
        )
    )
    assert not decision.allowed
    assert len(decision.reasons) == 2


def test_structural_change_requires_tests_then_approval() -> None:
    untested = AutonomyGate().evaluate(
        AutonomousActionContext(
            action_type="self_modify",
            structural_self_modification=True,
        )
    )
    assert not untested.allowed

    tested = AutonomyGate().evaluate(
        AutonomousActionContext(
            action_type="self_modify",
            structural_self_modification=True,
            tests_passed=True,
        )
    )
    assert tested.allowed
    assert tested.requires_approval


def test_private_message_with_personal_data_is_blocked() -> None:
    decision = AutonomyGate().evaluate(
        AutonomousActionContext(
            action_type="private_message",
            contains_personal_data=True,
        )
    )
    assert not decision.allowed
