from __future__ import annotations

from eck.domain.models import AutonomousActionContext, AutonomousActionDecision


class AutonomyGate:
    def evaluate(self, context: AutonomousActionContext) -> AutonomousActionDecision:
        blocked: list[str] = []
        approval: list[str] = []

        if context.uses_paid_api_or_real_money:
            blocked.append("Paid APIs and real-money actions are disabled.")
        if context.illegal_content_or_action:
            blocked.append("Illegal content or actions are prohibited.")
        if context.deceptive_or_concealed:
            blocked.append("Deception and concealment are prohibited.")
        if context.artificial_engagement:
            blocked.append("Fake engagement and metric manipulation are prohibited.")
        if context.evades_platform_controls:
            blocked.append("Rate-limit, moderation, or platform-control evasion is prohibited.")
        if context.public_action and not context.ai_disclosure_present:
            blocked.append("Every public action must disclose AI/ECK operation.")
        if context.action_type == "private_message" and context.contains_personal_data:
            blocked.append("Private messages must not process or disclose personal data.")
        if context.structural_self_modification:
            if not context.tests_passed:
                blocked.append("Structural self-modification is blocked until tests pass.")
            else:
                approval.append("Structural self-modification requires human approval.")
        if context.legal_uncertainty:
            approval.append("Legal uncertainty requires human review.")
        if context.needs_account_credentials_or_human_verification:
            approval.append("Credentials, CAPTCHA, or 2FA require the account owner.")

        if blocked:
            return AutonomousActionDecision(
                allowed=False,
                requires_approval=False,
                reasons=tuple(blocked),
            )
        return AutonomousActionDecision(
            allowed=True,
            requires_approval=bool(approval),
            reasons=tuple(approval) or ("Action is within the approved autonomous boundary.",),
        )
