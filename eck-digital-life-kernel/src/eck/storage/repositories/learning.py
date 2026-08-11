from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from eck.core.ids import new_id
from eck.core.time import iso_now
from eck.domain.enums import ChallengeStatus, VerificationStatus
from eck.domain.models import (
    AutonomyPolicy,
    BenchmarkRunCreate,
    BenchmarkRunRecord,
    ChallengeDraftCreate,
    ChallengeDraftRecord,
    ChallengeProgress,
    ChallengeRecord,
    ExperienceRecord,
    KnowledgeRecord,
    LearningThemeCreate,
    LearningThemeRecord,
    ReflectionRecord,
    SkillRecord,
    SocialEngagementContract,
    SocialPostObservation,
    SocialPostObservationCreate,
)
from eck.storage.repositories.base import SQLiteRepositoryMixin
from eck.storage.repositories.common import load_json as _load
from eck.storage.repositories.common import to_json as _json


class LearningRepositoryMixin(SQLiteRepositoryMixin):
    def add_experience(
        self,
        *,
        task_id: str,
        capability: str,
        outcome: VerificationStatus,
        summary: str,
        evidence_ids: tuple[str, ...],
        admitted: bool,
        admission_reason: str,
    ) -> ExperienceRecord:
        experience_id = new_id("experience")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO experiences (
                    experience_id, task_id, capability, outcome, summary,
                    evidence_ids_json, admitted, admission_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experience_id,
                    task_id,
                    capability,
                    outcome.value,
                    summary,
                    _json(evidence_ids),
                    int(admitted),
                    admission_reason,
                    created_at,
                ),
            )
        return ExperienceRecord(
            experience_id=experience_id,
            task_id=task_id,
            capability=capability,
            outcome=outcome,
            summary=summary,
            evidence_ids=evidence_ids,
            admitted=admitted,
            admission_reason=admission_reason,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_experiences(self, limit: int = 100) -> list[ExperienceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM experiences ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ExperienceRecord(
                experience_id=row["experience_id"],
                task_id=row["task_id"],
                capability=row["capability"],
                outcome=VerificationStatus(row["outcome"]),
                summary=row["summary"],
                evidence_ids=tuple(_load(row["evidence_ids_json"], [])),
                admitted=bool(row["admitted"]),
                admission_reason=row["admission_reason"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def count_experiences(self, *, admitted: bool | None = None) -> int:
        where = "" if admitted is None else "WHERE admitted = ?"
        params: tuple[object, ...] = () if admitted is None else (int(admitted),)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM experiences {where}",
                params,
            ).fetchone()
        return int(row["count"])

    def latest_experience(self, *, admitted: bool = False) -> ExperienceRecord | None:
        where = "WHERE admitted = 1" if admitted else ""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM experiences {where} ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return ExperienceRecord(
            experience_id=row["experience_id"],
            task_id=row["task_id"],
            capability=row["capability"],
            outcome=VerificationStatus(row["outcome"]),
            summary=row["summary"],
            evidence_ids=tuple(_load(row["evidence_ids_json"], [])),
            admitted=bool(row["admitted"]),
            admission_reason=row["admission_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def revoke_task_learning(self, task_id: str, reason: str) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            experience = conn.execute(
                "SELECT admitted FROM experiences WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not experience or not bool(experience["admitted"]):
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                """
                UPDATE experiences
                SET admitted = 0, admission_reason = ?
                WHERE task_id = ?
                """,
                (f"Revoked after relevance audit: {reason}", task_id),
            )
            conn.execute(
                "UPDATE knowledge_items SET admitted = 0 WHERE task_id = ?",
                (task_id,),
            )
            conn.execute("COMMIT")
        return True

    def add_knowledge(
        self,
        *,
        task_id: str,
        capability: str,
        claim: str,
        outcome: VerificationStatus,
        evidence_ids: tuple[str, ...],
        externally_grounded: bool,
        reproducible: bool,
        admitted: bool,
    ) -> KnowledgeRecord:
        knowledge_id = new_id("knowledge")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_items (
                    knowledge_id, task_id, capability, claim, outcome,
                    evidence_ids_json, externally_grounded, reproducible,
                    admitted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    knowledge_id,
                    task_id,
                    capability,
                    claim,
                    outcome.value,
                    _json(evidence_ids),
                    int(externally_grounded),
                    int(reproducible),
                    int(admitted),
                    created_at,
                ),
            )
        return KnowledgeRecord(
            knowledge_id=knowledge_id,
            task_id=task_id,
            capability=capability,
            claim=claim,
            outcome=outcome,
            evidence_ids=evidence_ids,
            externally_grounded=externally_grounded,
            reproducible=reproducible,
            admitted=admitted,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_knowledge(self, limit: int = 100) -> list[KnowledgeRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_items ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            KnowledgeRecord(
                knowledge_id=row["knowledge_id"],
                task_id=row["task_id"],
                capability=row["capability"],
                claim=row["claim"],
                outcome=VerificationStatus(row["outcome"]),
                evidence_ids=tuple(_load(row["evidence_ids_json"], [])),
                externally_grounded=bool(row["externally_grounded"]),
                reproducible=bool(row["reproducible"]),
                admitted=bool(row["admitted"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def count_knowledge(self) -> int:
        return self._count_table("knowledge_items")

    def add_reflection(
        self,
        *,
        task_id: str,
        capability: str,
        outcome: VerificationStatus,
        observation: str,
        lesson: str,
        next_step: str,
        verification_report_id: str,
        evidence_ids: tuple[str, ...],
        generator: str = "deterministic-template.v1",
    ) -> ReflectionRecord:
        reflection_id = new_id("reflection")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reflections (
                    reflection_id, task_id, capability, outcome, observation,
                    lesson, next_step, verification_report_id, evidence_ids_json,
                    generator, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reflection_id,
                    task_id,
                    capability,
                    outcome.value,
                    observation,
                    lesson,
                    next_step,
                    verification_report_id,
                    _json(evidence_ids),
                    generator,
                    created_at,
                ),
            )
        return ReflectionRecord(
            reflection_id=reflection_id,
            task_id=task_id,
            capability=capability,
            outcome=outcome,
            observation=observation,
            lesson=lesson,
            next_step=next_step,
            verification_report_id=verification_report_id,
            evidence_ids=evidence_ids,
            generator=generator,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_reflections(self, limit: int = 100) -> list[ReflectionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reflections ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            ReflectionRecord(
                reflection_id=row["reflection_id"],
                task_id=row["task_id"],
                capability=row["capability"],
                outcome=VerificationStatus(row["outcome"]),
                observation=row["observation"],
                lesson=row["lesson"],
                next_step=row["next_step"],
                verification_report_id=row["verification_report_id"],
                evidence_ids=tuple(_load(row["evidence_ids_json"], [])),
                generator=row["generator"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def count_reflections(self) -> int:
        return self._count_table("reflections")

    def upsert_skill_success(
        self,
        *,
        fingerprint: str,
        name: str,
        capability: str,
        procedure: dict[str, Any],
        verification_basis: dict[str, Any],
        activation_threshold: int = 2,
    ) -> SkillRecord:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM skills WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if row:
                success_count = int(row["success_count"]) + 1
                conn.execute(
                    """
                    UPDATE skills SET
                        procedure_json = ?, verification_basis_json = ?,
                        success_count = ?, active = ?, updated_at = ?
                    WHERE fingerprint = ?
                    """,
                    (
                        _json(procedure),
                        _json(verification_basis),
                        success_count,
                        int(success_count >= activation_threshold),
                        now,
                        fingerprint,
                    ),
                )
            else:
                success_count = 1
                conn.execute(
                    """
                    INSERT INTO skills (
                        skill_id, fingerprint, name, capability, procedure_json,
                        verification_basis_json, success_count, failure_count,
                        active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
                    """,
                    (
                        new_id("skill"),
                        fingerprint,
                        name,
                        capability,
                        _json(procedure),
                        _json(verification_basis),
                        int(success_count >= activation_threshold),
                        now,
                        now,
                    ),
                )
            conn.execute("COMMIT")
        skill = self.get_skill(fingerprint)
        assert skill is not None
        return skill

    def get_skill(self, fingerprint: str) -> SkillRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return self._skill_from_row(row) if row else None

    def list_skills(self, limit: int = 100) -> list[SkillRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM skills ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._skill_from_row(row) for row in rows]

    def count_skills(self) -> int:
        return self._count_table("skills")

    def revoke_skill_success(self, fingerprint: str) -> SkillRecord | None:
        now = iso_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT success_count, failure_count FROM skills WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return None
            success_count = max(0, int(row["success_count"]) - 1)
            conn.execute(
                """
                UPDATE skills
                SET success_count = ?, failure_count = ?, active = ?, updated_at = ?
                WHERE fingerprint = ?
                """,
                (
                    success_count,
                    int(row["failure_count"]) + 1,
                    int(success_count >= 2),
                    now,
                    fingerprint,
                ),
            )
            conn.execute("COMMIT")
        return self.get_skill(fingerprint)

    def create_challenge(
        self,
        *,
        kind: str,
        title: str,
        objective: str,
        contract: SocialEngagementContract,
        policy: AutonomyPolicy,
        next_action: str,
    ) -> ChallengeRecord:
        challenge_id = new_id("challenge")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO challenges (
                    challenge_id, kind, title, objective, status, contract_json,
                    policy_json, strategy_json, progress_json, next_action,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    challenge_id,
                    kind,
                    title,
                    objective,
                    ChallengeStatus.PLANNING.value,
                    _json(contract),
                    _json(policy),
                    _json({}),
                    _json(ChallengeProgress()),
                    next_action,
                    now,
                    now,
                ),
            )
        return self.get_challenge(challenge_id)

    def update_challenge(
        self,
        challenge_id: str,
        *,
        status: ChallengeStatus | None = None,
        strategy: dict[str, Any] | None = None,
        progress: ChallengeProgress | None = None,
        selected_platform: str | None = None,
        next_action: str | None = None,
        blocked_reason: str | None = None,
        completed_at: datetime | None = None,
    ) -> ChallengeRecord:
        assignments = ["updated_at = ?"]
        values: list[Any] = [iso_now()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status.value)
        if strategy is not None:
            assignments.append("strategy_json = ?")
            values.append(_json(strategy))
        if progress is not None:
            assignments.append("progress_json = ?")
            values.append(_json(progress))
        if selected_platform is not None:
            assignments.append("selected_platform = ?")
            values.append(selected_platform)
        if next_action is not None:
            assignments.append("next_action = ?")
            values.append(next_action)
        if blocked_reason is not None:
            assignments.append("blocked_reason = ?")
            values.append(blocked_reason)
        if completed_at is not None:
            assignments.append("completed_at = ?")
            values.append(completed_at.isoformat())
        values.append(challenge_id)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE challenges SET {', '.join(assignments)} WHERE challenge_id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown challenge: {challenge_id}")
        return self.get_challenge(challenge_id)

    def get_challenge(self, challenge_id: str) -> ChallengeRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown challenge: {challenge_id}")
        return self._challenge_from_row(row)

    def list_challenges(self, limit: int = 100) -> list[ChallengeRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM challenges ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._challenge_from_row(row) for row in rows]

    def count_challenges(self) -> int:
        return self._count_table("challenges")

    def add_social_post_observation(
        self,
        challenge_id: str,
        create: SocialPostObservationCreate,
        *,
        within_window: bool,
        cadence_compliant: bool,
        contract_satisfied: bool,
    ) -> SocialPostObservation:
        observation_id = new_id("social-observation")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO social_post_observations (
                    observation_id, challenge_id, platform, post_url, published_at,
                    observed_at, total_comments, human_verified_comments, likes,
                    disclosure_present, policy_compliant, human_reviewed,
                    within_window, cadence_compliant, contract_satisfied, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    challenge_id,
                    create.platform,
                    create.post_url,
                    create.published_at.isoformat(),
                    create.observed_at.isoformat(),
                    create.total_comments,
                    create.human_verified_comments,
                    create.likes,
                    int(create.disclosure_present),
                    int(create.policy_compliant),
                    int(create.human_reviewed),
                    int(within_window),
                    int(cadence_compliant),
                    int(contract_satisfied),
                    created_at,
                ),
            )
        return self.get_social_post_observation(observation_id)

    def get_social_post_observation(self, observation_id: str) -> SocialPostObservation:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM social_post_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown social observation: {observation_id}")
        return self._social_observation_from_row(row)

    def list_social_post_observations(
        self, challenge_id: str, limit: int = 100
    ) -> list[SocialPostObservation]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM social_post_observations
                WHERE challenge_id = ? ORDER BY published_at DESC LIMIT ?
                """,
                (challenge_id, limit),
            ).fetchall()
        return [self._social_observation_from_row(row) for row in rows]

    def add_benchmark_run(self, create: BenchmarkRunCreate) -> BenchmarkRunRecord:
        run_id = new_id("benchmark")
        created_at = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO benchmark_runs (
                    run_id, suite, benchmark_version, model, model_artifact_hash,
                    evaluator, score, sample_count, protocol_json, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    create.suite.value,
                    create.benchmark_version,
                    create.model,
                    create.model_artifact_hash,
                    create.evaluator,
                    create.score,
                    create.sample_count,
                    _json(create.protocol),
                    create.notes,
                    created_at,
                ),
            )
        return BenchmarkRunRecord(
            run_id=run_id,
            created_at=datetime.fromisoformat(created_at),
            **create.model_dump(),
        )

    def list_benchmark_runs(self, limit: int = 100) -> list[BenchmarkRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM benchmark_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._benchmark_run_from_row(row) for row in rows]

    def count_benchmark_runs(self) -> int:
        return self._count_table("benchmark_runs")

    def add_challenge_draft(self, create: ChallengeDraftCreate) -> ChallengeDraftRecord:
        draft_id = new_id("challenge-draft")
        created_at = iso_now()
        payload = {
            "goal": create.goal,
            "completion_requirements": create.completion_requirements,
            "status": "draft",
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO observations (observation_id, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (draft_id, "challenge_draft", _json(payload), created_at),
            )
        return ChallengeDraftRecord(
            draft_id=draft_id,
            goal=create.goal,
            completion_requirements=create.completion_requirements,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_challenge_drafts(self, limit: int = 100) -> list[ChallengeDraftRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM observations
                WHERE kind = ? ORDER BY created_at DESC LIMIT ?
                """,
                ("challenge_draft", limit),
            ).fetchall()
        records = []
        for row in rows:
            payload = _load(row["payload_json"], {})
            records.append(
                ChallengeDraftRecord(
                    draft_id=row["observation_id"],
                    goal=payload["goal"],
                    completion_requirements=payload["completion_requirements"],
                    status=payload.get("status", "draft"),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return records

    def add_learning_theme(self, create: LearningThemeCreate) -> LearningThemeRecord:
        title = " ".join(create.title.split())
        now = iso_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT theme_id FROM learning_themes WHERE title = ? COLLATE NOCASE",
                (title,),
            ).fetchone()
            if existing:
                theme_id = str(existing["theme_id"])
                conn.execute(
                    "UPDATE learning_themes SET active = 1, updated_at = ? WHERE theme_id = ?",
                    (now, theme_id),
                )
            else:
                theme_id = new_id("learning-theme")
                conn.execute(
                    """
                    INSERT INTO learning_themes (
                        theme_id, title, active, created_at, updated_at
                    ) VALUES (?, ?, 1, ?, ?)
                    """,
                    (theme_id, title, now, now),
                )
        return self.get_learning_theme(theme_id)

    def get_learning_theme(self, theme_id: str) -> LearningThemeRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM learning_themes WHERE theme_id = ?", (theme_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown learning theme: {theme_id}")
        return self._learning_theme_from_row(row)

    def list_learning_themes(
        self,
        *,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[LearningThemeRecord]:
        where = "WHERE active = 1" if active_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM learning_themes {where}
                ORDER BY active DESC, updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._learning_theme_from_row(row) for row in rows]

    def set_learning_theme_active(
        self,
        theme_id: str,
        *,
        active: bool,
    ) -> LearningThemeRecord:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE learning_themes SET active = ?, updated_at = ? WHERE theme_id = ?",
                (int(active), iso_now(), theme_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown learning theme: {theme_id}")
        return self.get_learning_theme(theme_id)

    def delete_learning_theme(self, theme_id: str) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM learning_themes WHERE theme_id = ?", (theme_id,)
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown learning theme: {theme_id}")

    @staticmethod
    def _challenge_from_row(row: sqlite3.Row) -> ChallengeRecord:
        return ChallengeRecord(
            challenge_id=row["challenge_id"],
            kind=row["kind"],
            title=row["title"],
            objective=row["objective"],
            status=ChallengeStatus(row["status"]),
            contract=SocialEngagementContract.model_validate(_load(row["contract_json"])),
            policy=AutonomyPolicy.model_validate(_load(row["policy_json"])),
            strategy=_load(row["strategy_json"], {}),
            progress=ChallengeProgress.model_validate(_load(row["progress_json"], {})),
            selected_platform=row["selected_platform"],
            next_action=row["next_action"],
            blocked_reason=row["blocked_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
        )

    @staticmethod
    def _social_observation_from_row(row: sqlite3.Row) -> SocialPostObservation:
        return SocialPostObservation(
            observation_id=row["observation_id"],
            challenge_id=row["challenge_id"],
            platform=row["platform"],
            post_url=row["post_url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            total_comments=row["total_comments"],
            human_verified_comments=row["human_verified_comments"],
            likes=row["likes"],
            disclosure_present=bool(row["disclosure_present"]),
            policy_compliant=bool(row["policy_compliant"]),
            human_reviewed=bool(row["human_reviewed"]),
            within_window=bool(row["within_window"]),
            cadence_compliant=bool(row["cadence_compliant"]),
            contract_satisfied=bool(row["contract_satisfied"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _benchmark_run_from_row(row: sqlite3.Row) -> BenchmarkRunRecord:
        return BenchmarkRunRecord(
            run_id=row["run_id"],
            suite=row["suite"],
            benchmark_version=row["benchmark_version"],
            model=row["model"],
            model_artifact_hash=row["model_artifact_hash"],
            evaluator=row["evaluator"],
            score=row["score"],
            sample_count=row["sample_count"],
            protocol=_load(row["protocol_json"], {}),
            notes=row["notes"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _learning_theme_from_row(row: sqlite3.Row) -> LearningThemeRecord:
        return LearningThemeRecord(
            theme_id=row["theme_id"],
            title=row["title"],
            active=bool(row["active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _skill_from_row(row: sqlite3.Row) -> SkillRecord:
        return SkillRecord(
            skill_id=row["skill_id"],
            fingerprint=row["fingerprint"],
            name=row["name"],
            capability=row["capability"],
            procedure=_load(row["procedure_json"], {}),
            verification_basis=_load(row["verification_basis_json"], {}),
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            active=bool(row["active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
