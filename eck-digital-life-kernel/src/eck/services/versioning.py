from __future__ import annotations

from eck.domain.models import RuntimeVersionRecord
from eck.events.bus import EventBus
from eck.storage.sqlite import SQLiteStore


class VersionService:
    def __init__(self, store: SQLiteStore, events: EventBus) -> None:
        self.store = store
        self.events = events

    def status(self) -> RuntimeVersionRecord:
        return self.store.get_runtime_version()

    async def observe_verified_skills(self) -> RuntimeVersionRecord:
        current = self.store.get_runtime_version()
        learned = sum(1 for item in self.store.list_skills(limit=10000) if item.active)
        runtime = sum(
            1
            for item in self.store.list_runtime_skills(limit=10000)
            if item.status.value == "active"
        )
        total = learned + runtime
        if total <= current.verified_skill_count:
            return current

        gained = total - current.verified_skill_count
        minor = current.minor
        next_minor = current.next_minor_skill_count
        reasons = [f"Recorded {gained} newly verified skill(s)."]
        while total >= next_minor:
            minor += 1
            reasons.append(f"Verified skill milestone {next_minor} reached.")
            next_minor += 100
        updated = self.store.update_runtime_version(
            major=current.major,
            minor=minor,
            patch=current.patch,
            verified_skill_count=total,
            next_minor_skill_count=next_minor,
            pending_updates=current.pending_updates + gained,
            reason=" ".join(reasons),
        )
        if updated.version != current.version:
            await self.events.publish(
                "RuntimeVersionChanged",
                updated.version,
                {"from": current.version, "to": updated.version, "reason": updated.last_reason},
            )
        return updated

    async def approve_monthly_release(self, mission_id: str) -> RuntimeVersionRecord:
        current = await self.observe_verified_skills()
        if current.pending_updates == 0:
            return current
        updated = self.store.update_runtime_version(
            major=current.major + 1,
            minor=0,
            patch=0,
            verified_skill_count=current.verified_skill_count,
            next_minor_skill_count=current.next_minor_skill_count,
            pending_updates=0,
            reason=f"Monthly mission {mission_id} passed human review with verified updates.",
        )
        await self.events.publish(
            "RuntimeVersionChanged",
            updated.version,
            {"from": current.version, "to": updated.version, "mission_id": mission_id},
            correlation_id=mission_id,
        )
        return updated

    def record_runtime_update(self, reason: str) -> RuntimeVersionRecord:
        current = self.store.get_runtime_version()
        return self.store.update_runtime_version(
            major=current.major,
            minor=current.minor,
            patch=current.patch,
            verified_skill_count=current.verified_skill_count,
            next_minor_skill_count=current.next_minor_skill_count,
            pending_updates=current.pending_updates + 1,
            reason=reason,
        )
