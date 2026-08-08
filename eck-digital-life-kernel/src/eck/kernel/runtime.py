from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime

from eck.config import Settings
from eck.domain.enums import KernelPhase, TaskStatus
from eck.domain.models import KernelStatus, SupervisorReviewRecord, TaskRecord
from eck.events.bus import EventBus
from eck.services.autonomous_learning import AutonomousLearningService
from eck.services.supervisor import SupervisorService
from eck.services.tasks import TaskService
from eck.storage.sqlite import SQLiteStore


class LifeKernel:
    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        events: EventBus,
        tasks: TaskService,
        supervisor: SupervisorService,
        autonomous_learning: AutonomousLearningService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.tasks = tasks
        self.supervisor = supervisor
        self.autonomous_learning = autonomous_learning
        self.phase = KernelPhase.STOPPED
        self._run_task: asyncio.Task[None] | None = None
        self._execution_task: asyncio.Task[TaskRecord] | None = None
        self._supervision_task: asyncio.Task[SupervisorReviewRecord | None] | None = None
        self._curriculum_task: asyncio.Task[TaskRecord | None] | None = None
        self._stop = asyncio.Event()
        self._sleep_requested = asyncio.Event()
        self._sleep_lock = asyncio.Lock()
        self._boot_count = 0
        self._started_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None
        self._schedule_cursor = 0

    async def start(self) -> None:
        if self.phase not in {KernelPhase.STOPPED, KernelPhase.FAULTED}:
            return
        self.phase = KernelPhase.STARTING
        self._boot_count, recovered = self.store.begin_boot(self.settings.identity)
        state = self.store.get_kernel_state(self.settings.identity)
        self._started_at = (
            datetime.fromisoformat(state["started_at"]) if state and state["started_at"] else None
        )
        self._last_heartbeat_at = self._started_at
        await self.events.publish(
            "KernelRecovered" if recovered else "KernelStarted",
            self.settings.identity,
            {"boot_count": self._boot_count, "recovered_unclean_shutdown": recovered},
        )
        await self.tasks.recover_interrupted()
        self.phase = KernelPhase.RUNNING
        self.store.update_kernel_state(self.settings.identity, self.phase, heartbeat=True)
        self._stop.clear()
        self._run_task = asyncio.create_task(self._life_loop(), name="eck-life-loop")

    async def pause(self) -> None:
        if self.phase is KernelPhase.RUNNING:
            self.phase = KernelPhase.PAUSED
            self.store.update_kernel_state(self.settings.identity, self.phase)
            await self.events.publish("KernelPaused", self.settings.identity, {})

    async def resume(self) -> None:
        if self.phase is KernelPhase.PAUSED:
            self.phase = KernelPhase.RUNNING
            self.store.update_kernel_state(self.settings.identity, self.phase)
            await self.events.publish("KernelResumed", self.settings.identity, {})

    async def request_sleep(self) -> None:
        self._sleep_requested.set()

    async def run_sleep_cycle(self) -> None:
        if self.phase is not KernelPhase.RUNNING:
            return
        async with self._sleep_lock:
            if self.phase is KernelPhase.RUNNING:
                await self._sleep_cycle()

    async def stop(self, *, clean: bool = True) -> None:
        if self.phase is KernelPhase.STOPPED:
            return
        self.phase = KernelPhase.STOPPING
        self.store.update_kernel_state(self.settings.identity, self.phase)
        self._stop.set()
        if self._run_task:
            self._run_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._run_task
            self._run_task = None
        if self._execution_task:
            self._execution_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._execution_task
            self._execution_task = None
        if self._supervision_task:
            self._supervision_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._supervision_task
            self._supervision_task = None
        if self._curriculum_task:
            self._curriculum_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._curriculum_task
            self._curriculum_task = None
        await self.events.publish(
            "KernelStopped",
            self.settings.identity,
            {"clean": clean},
        )
        self.phase = KernelPhase.STOPPED
        self.store.update_kernel_state(
            self.settings.identity,
            self.phase,
            heartbeat=True,
            clean_shutdown=clean,
        )

    def status(self) -> KernelStatus:
        state = self.store.get_kernel_state(self.settings.identity)
        started_at = (
            datetime.fromisoformat(state["started_at"])
            if state and state["started_at"]
            else self._started_at
        )
        heartbeat = (
            datetime.fromisoformat(state["last_heartbeat_at"])
            if state and state["last_heartbeat_at"]
            else self._last_heartbeat_at
        )
        return KernelStatus(
            identity=self.settings.identity,
            phase=self.phase,
            boot_count=self._boot_count,
            started_at=started_at,
            last_heartbeat_at=heartbeat,
            pending_tasks=self.store.count_tasks(
                (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
            ),
            pending_approvals=self.store.count_pending_approvals(),
            event_count=self.store.count_events(),
        )

    async def _life_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time()
        next_heartbeat_event = loop.time()
        next_sleep = loop.time() + self.settings.sleep_cycle_seconds
        next_supervision = loop.time() + self.settings.supervisor_initial_delay_seconds
        try:
            while not self._stop.is_set():
                if self._execution_task and self._execution_task.done():
                    await self._execution_task
                    self._execution_task = None
                    next_supervision = (
                        loop.time() + self.settings.supervisor_initial_delay_seconds
                    )
                if (
                    self._supervision_task
                    and not self._supervision_task.done()
                    and self.tasks.has_urgent_queued()
                ):
                    self._supervision_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._supervision_task
                    self._supervision_task = None
                    next_supervision = loop.time() + self.settings.supervisor_review_seconds
                    await self.events.publish(
                        "SupervisorPreempted",
                        self.settings.identity,
                        {"reason": "urgent_human_task"},
                    )
                if self._supervision_task and self._supervision_task.done():
                    await self._supervision_task
                    self._supervision_task = None
                    next_supervision = loop.time() + self.settings.supervisor_review_seconds
                if self._curriculum_task and self._curriculum_task.done():
                    await self._curriculum_task
                    self._curriculum_task = None
                if self.phase is KernelPhase.RUNNING:
                    if (
                        self._execution_task is None
                        and self._supervision_task is None
                        and self._curriculum_task is None
                    ):
                        prefer_challenge = (
                            self._schedule_cursor >= self.settings.autonomous_learning_percent
                        )
                        queued = self.tasks.next_queued(prefer_challenge=prefer_challenge)
                        if queued:
                            self._schedule_cursor = (self._schedule_cursor + 1) % 100
                            self._execution_task = asyncio.create_task(
                                self.tasks.execute(queued.task_id),
                                name=f"eck-task-{queued.task_id}",
                            )
                        elif self.settings.supervisor_enabled and loop.time() >= next_supervision:
                            self._supervision_task = asyncio.create_task(
                                self.supervisor.review_if_idle(),
                                name="eck-supervisor-review",
                            )
                        elif self.settings.autonomous_curriculum_enabled:
                            self._curriculum_task = asyncio.create_task(
                                self.autonomous_learning.enqueue_if_idle(),
                                name="eck-autonomous-curriculum",
                            )
                    if self._sleep_requested.is_set() or loop.time() >= next_sleep:
                        await self.run_sleep_cycle()
                        next_sleep = loop.time() + self.settings.sleep_cycle_seconds
                    if loop.time() >= next_heartbeat:
                        publish_event = loop.time() >= next_heartbeat_event
                        await self._heartbeat(publish_event=publish_event)
                        if publish_event:
                            next_heartbeat_event = (
                                loop.time() + self.settings.heartbeat_event_seconds
                            )
                        next_heartbeat = loop.time() + self.settings.heartbeat_seconds
                await asyncio.sleep(self.settings.task_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.phase = KernelPhase.FAULTED
            self.store.update_kernel_state(self.settings.identity, self.phase)
            await self.events.publish(
                "KernelFaulted",
                self.settings.identity,
                {"type": type(exc).__name__, "detail": str(exc)},
            )

    async def _heartbeat(self, *, publish_event: bool = True) -> None:
        self.store.update_kernel_state(self.settings.identity, self.phase, heartbeat=True)
        state = self.store.get_kernel_state(self.settings.identity)
        self._last_heartbeat_at = (
            datetime.fromisoformat(state["last_heartbeat_at"])
            if state and state["last_heartbeat_at"]
            else None
        )
        if publish_event:
            await self.events.publish(
                "Heartbeat",
                self.settings.identity,
                {
                    "phase": self.phase.value,
                    "pending_tasks": self.store.count_tasks((TaskStatus.QUEUED,)),
                },
            )

    async def _sleep_cycle(self) -> None:
        self._sleep_requested.clear()
        self.phase = KernelPhase.SLEEPING
        self.store.update_kernel_state(self.settings.identity, self.phase)
        await self.events.publish("SleepStarted", self.settings.identity, {})
        valid, failed_sequence = self.store.verify_event_chain()
        await self.events.publish(
            "MemoryConsolidated",
            self.settings.identity,
            {
                "event_chain_valid": valid,
                "failed_sequence": failed_sequence,
                "experience_count": len(self.store.list_experiences(limit=10000)),
                "knowledge_count": len(self.store.list_knowledge(limit=10000)),
                "reflection_count": len(self.store.list_reflections(limit=10000)),
                "skill_count": len(self.store.list_skills(limit=10000)),
            },
        )
        await self.events.publish("SleepFinished", self.settings.identity, {})
        self.phase = KernelPhase.RUNNING
        self.store.update_kernel_state(self.settings.identity, self.phase, heartbeat=True)
