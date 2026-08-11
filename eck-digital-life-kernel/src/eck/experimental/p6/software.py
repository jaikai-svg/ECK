from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from eck.domain.enums import MissionStatus, MissionStepStatus
from eck.domain.models import MissionRecord, MissionStepRecord
from eck.experimental.p6.executor_base import (
    MissionExecutorMixinBase,
    StepOutcome,
    _ReferenceParser,
)


class MissionSoftwareArtifactMixin(MissionExecutorMixinBase):
    async def _implement_python(
        self,
        mission: MissionRecord,
        spec: dict[str, Any],
    ) -> StepOutcome:
        files: list[dict[str, str]] = []
        model = "deterministic-python-fallback.v1"
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n你是資深 Python 3.11 軟體工程師。只輸出 JSON。"
                            "交付完整可執行專案與 pytest，不是教學或片段。只使用標準函式庫與 "
                            "pytest；禁止網路、shell、假資料成功、mock、TODO 與未實作函式。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "objective": mission.objective,
                                "completion_requirements": mission.completion_requirements,
                                "spec": spec,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["path", "content"],
                            },
                        }
                    },
                    "required": ["files"],
                },
                options={"temperature": 0.2, "num_predict": 8192},
            )
            files = self._validated_python_files(self._json_object(response.content).get("files"))
            model = response.model
        except (json.JSONDecodeError, RuntimeError, ValueError):
            files = []
        if not files:
            files = self._fallback_python_files(mission)
        source_dir = self._source_dir(mission.mission_id)
        self._clear_source(source_dir)
        for item in files:
            target = source_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
        return StepOutcome(
            success=True,
            output={
                "model": model,
                "source_dir": str(source_dir),
                "files": [item["path"] for item in files],
                "bytes": self._directory_bytes(source_dir),
            },
        )

    async def _repair_python(
        self,
        mission: MissionRecord,
        source_dir: Path,
        failure: str,
    ) -> dict[str, Any]:
        current = [
            {
                "path": path.relative_to(source_dir).as_posix(),
                "content": path.read_text(encoding="utf-8", errors="replace")[:120_000],
            }
            for path in sorted(source_dir.rglob("*"))
            if path.is_file() and path.suffix.casefold() in {".py", ".toml", ".md", ".txt"}
        ]
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n只輸出 JSON。根據真實靜態檢查或 pytest 失敗修正完整 "
                            "Python 專案；維持原目標且不得用 mock 或刪除重要斷言規避失敗。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "objective": mission.objective,
                                "failure": failure,
                                "current_files": current,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {"files": {"type": "array", "items": {"type": "object"}}},
                    "required": ["files"],
                },
                options={"temperature": 0.1, "num_predict": 8192},
            )
            files = self._validated_python_files(self._json_object(response.content).get("files"))
            if not files:
                return {"attempted": True, "applied": False, "detail": "No valid repair files."}
            self._clear_source(source_dir)
            for item in files:
                target = source_dir / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["content"], encoding="utf-8")
            return {"attempted": True, "applied": True, "model": response.model}
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            return {"attempted": True, "applied": False, "detail": str(exc)}

    async def _repair_site(
        self,
        mission: MissionRecord,
        source_dir: Path,
        failure: str,
    ) -> dict[str, Any]:
        before_files = self._source_files(source_dir, "static_website")
        before_report = self._validate_site(source_dir, mission, enforce_threshold=False)
        deterministic = self._apply_site_contract_repairs(source_dir, mission)
        deterministic_report = self._validate_site(
            source_dir,
            mission,
            enforce_threshold=False,
        )
        if deterministic_report["success"]:
            return {
                "attempted": True,
                "applied": bool(deterministic["changed"]),
                "model": "deterministic-site-contract-repair.v1",
                "changes": deterministic["changes"],
                "quality_before": before_report["quality_score"],
                "quality_after": deterministic_report["quality_score"],
            }
        current = [
            {
                "path": path.relative_to(source_dir).as_posix(),
                "content": path.read_text(encoding="utf-8", errors="replace")[:120_000],
            }
            for path in sorted(source_dir.rglob("*"))
            if path.is_file() and path.suffix.casefold() in self._allowed_site_suffixes
        ]
        try:
            response = await self.coder_brain.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "/no_think\n你是前端除錯工程師。只輸出 JSON。"
                            "依驗證器的真實錯誤修正專案，回傳完整 files 陣列，不可移除原任務功能。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "objective": mission.objective,
                                "failure": failure,
                                "current_files": current,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                format_schema={
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["path", "content"],
                            },
                        }
                    },
                    "required": ["files"],
                },
                options={"temperature": 0.15, "num_predict": 8192},
            )
            files = self._validated_site_files(self._json_object(response.content).get("files"))
            if not files:
                if deterministic["changed"]:
                    return {
                        "attempted": True,
                        "applied": True,
                        "model": "deterministic-site-contract-repair.v1",
                        "changes": deterministic["changes"],
                        "detail": (
                            "No valid model repair files; deterministic repairs were retained."
                        ),
                        "quality_before": before_report["quality_score"],
                        "quality_after": deterministic_report["quality_score"],
                    }
                return {"attempted": True, "applied": False, "detail": "No valid repair files."}
            self._write_project_files(source_dir, files)
            candidate_report = self._validate_site(
                source_dir,
                mission,
                enforce_threshold=False,
            )
            baseline = deterministic_report if deterministic["changed"] else before_report
            candidate_rank = (
                len(candidate_report["issues"]),
                -int(candidate_report["quality_score"]),
            )
            baseline_rank = (len(baseline["issues"]), -int(baseline["quality_score"]))
            if candidate_rank > baseline_rank:
                self._write_project_files(source_dir, before_files)
                if deterministic["changed"]:
                    self._apply_site_contract_repairs(source_dir, mission)
                return {
                    "attempted": True,
                    "applied": bool(deterministic["changed"]),
                    "model": "deterministic-site-contract-repair.v1",
                    "changes": deterministic["changes"],
                    "candidate_rejected": True,
                    "detail": "Model repair was rolled back because it degraded validation.",
                    "quality_before": before_report["quality_score"],
                    "quality_after": baseline["quality_score"],
                }
            return {
                "attempted": True,
                "applied": True,
                "model": response.model,
                "file_count": len(files),
                "changes": deterministic["changes"],
                "quality_before": before_report["quality_score"],
                "quality_after": candidate_report["quality_score"],
            }
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            if deterministic["changed"]:
                return {
                    "attempted": True,
                    "applied": True,
                    "model": "deterministic-site-contract-repair.v1",
                    "changes": deterministic["changes"],
                    "detail": f"Model repair unavailable after deterministic repair: {exc}",
                    "quality_before": before_report["quality_score"],
                    "quality_after": deterministic_report["quality_score"],
                }
            return {"attempted": True, "applied": False, "detail": str(exc)}

    def _apply_site_contract_repairs(
        self,
        source_dir: Path,
        mission: MissionRecord,
    ) -> dict[str, Any]:
        index = source_dir / "index.html"
        css = source_dir / "styles.css"
        if not index.is_file():
            return {"changed": False, "changes": []}
        markup = index.read_text(encoding="utf-8", errors="replace")
        stylesheet = css.read_text(encoding="utf-8", errors="replace") if css.is_file() else ""
        stylesheet_changed = False
        changes: list[str] = []
        if "aria-live" not in markup.casefold():
            feedback = (
                '<div class="eck-accessible-status" aria-live="polite" aria-atomic="true">'
                "頁面已就緒，可使用導覽與互動功能。</div>"
            )
            updated = re.sub(
                r"</body\s*>",
                feedback + "\n</body>",
                markup,
                count=1,
                flags=re.I,
            )
            if updated != markup:
                markup = updated
                changes.append("accessible-feedback")
        parser = _ReferenceParser()
        parser.feed(markup)
        image_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
        css_references = re.findall(
            r"""url\(\s*['"]?([^)'" ]+)""",
            stylesheet,
            re.I,
        )
        for reference in sorted(set([*parser.references, *css_references])):
            clean = reference.split("#", 1)[0].split("?", 1)[0].strip()
            if not clean or clean.startswith(("http://", "https://", "data:")):
                continue
            try:
                relative = Path(self._safe_relative_path(clean.lstrip("/")))
            except ValueError:
                continue
            if (
                (source_dir / relative).is_file()
                or relative.suffix.casefold() not in image_suffixes
            ):
                continue
            asset_name = re.sub(r"[^a-z0-9-]+", "-", relative.stem.casefold()).strip("-")
            asset_name = asset_name or "travel-scene"
            asset = PurePosixPath("assets") / f"{asset_name}.svg"
            asset_path = source_dir / Path(*asset.parts)
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_text(
                self._site_vector_asset(asset_name, mission.title),
                encoding="utf-8",
            )
            markup = markup.replace(reference, asset.as_posix())
            if reference in stylesheet:
                stylesheet = stylesheet.replace(reference, asset.as_posix())
                stylesheet_changed = True
            changes.append(f"local-asset:{asset.as_posix()}")
        css_additions: list[str] = []
        if len(re.findall(r"--[a-z][a-z0-9-]*\s*:", stylesheet, re.I)) < 4:
            css_additions.append(
                ":root { --eck-ink: #14221b; --eck-paper: #f4f0e7; "
                "--eck-accent: #ef6c42; --eck-focus: #b8ef5a; }"
            )
            changes.append("design-tokens")
        if len(stylesheet.encode("utf-8")) < 2400:
            css_additions.append(
                ".eck-accessible-status { position: absolute; width: 1px; height: 1px; "
                "padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); "
                "white-space: nowrap; border: 0; }\n"
                ":where(a, button, input, select, textarea):focus-visible { outline: 3px solid "
                "var(--eck-focus, #b8ef5a); outline-offset: 4px; }\n"
                "img { display: block; max-width: 100%; height: auto; object-fit: cover; }\n"
                "@media (prefers-reduced-motion: reduce) { *, *::before, *::after { "
                "animation-duration: .01ms !important; transition-duration: .01ms !important; } }\n"
                ".media-frame { overflow: hidden; border-radius: 1.25rem; background: "
                "linear-gradient(135deg, rgba(184,239,90,.22), rgba(239,108,66,.18)); }\n"
                ".media-frame img { width: 100%; aspect-ratio: 4 / 3; "
                "transition: transform .35s ease; }\n"
                ".media-frame:hover img { transform: scale(1.025); }"
            )
            changes.append("complete-layout-support")
        if css_additions:
            stylesheet = stylesheet.rstrip() + "\n\n" + "\n".join(css_additions) + "\n"
            stylesheet_changed = True
        if len(stylesheet.encode("utf-8")) < 2400:
            stylesheet += """
.content-stack { display: grid; gap: clamp(1rem, 2vw, 2rem); }
.content-stack > * { min-width: 0; }
.section-shell { width: min(100% - 2rem, 76rem); margin-inline: auto; }
.section-shell > header {
  display: flex; align-items: end; justify-content: space-between; gap: 1rem;
}
.section-shell :where(h1, h2, h3, p) { text-wrap: balance; }
.action-row { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; }
.action-row :where(a, button) { min-height: 2.75rem; padding-inline: 1rem; }
.surface-card {
  border: 1px solid color-mix(in srgb, currentColor 16%, transparent);
  border-radius: 1rem;
}
.surface-card { box-shadow: 0 1rem 3rem color-mix(in srgb, currentColor 8%, transparent); }
@media (max-width: 48rem) {
  .section-shell > header { align-items: start; flex-direction: column; }
}
"""
            if "complete-layout-support" not in changes:
                changes.append("complete-layout-support")
            stylesheet_changed = True
        if markup != index.read_text(encoding="utf-8", errors="replace"):
            index.write_text(markup, encoding="utf-8")
        if stylesheet_changed:
            css.write_text(stylesheet, encoding="utf-8")
        return {"changed": bool(changes), "changes": changes}

    @staticmethod
    def _site_vector_asset(name: str, title: str) -> str:
        label = html.escape(title[:80])
        seed = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:6], 16)
        hue = seed % 360
        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800"
  role="img" aria-label="{label}">
<defs><linearGradient id="sky" x2="1" y2="1">
<stop stop-color="hsl({hue} 56% 78%)"/>
<stop offset="1" stop-color="hsl({(hue + 54) % 360} 62% 48%)"/>
</linearGradient></defs>
<rect width="1200" height="800" fill="url(#sky)"/>
<circle cx="910" cy="170" r="86" fill="#f6f0b2" opacity=".9"/>
<path d="M0 590 250 310 470 560 690 250 980 570 1200 390V800H0Z" fill="#274f47" opacity=".88"/>
<path d="M0 690C260 610 390 760 650 650s390-80 550-25v175H0Z" fill="#f2efe4" opacity=".92"/>
<path d="M130 800c210-180 330-190 520-130s320 30 470-80"
  fill="none" stroke="#ef7149" stroke-width="18" stroke-linecap="round"/>
</svg>'''

    @staticmethod
    def _mechanical_site_failure(error: str) -> bool:
        return any(
            marker in error
            for marker in (
                "Accessibility contract failed",
                "Missing local references",
                "CSS quality contract failed",
                "styles.css is missing or too small",
                "Website quality score",
            )
        )

    def _first_urgent_runnable_step(self) -> MissionStepRecord | None:
        candidates: list[tuple[object, int, MissionStepRecord]] = []
        for mission in self.store.list_missions(limit=200):
            if mission.priority != "urgent" or mission.status not in {
                MissionStatus.ACTIVE,
                MissionStatus.PREPARING,
            }:
                continue
            steps = self.store.list_mission_steps(mission.mission_id)
            statuses = {item.step_key: item.status for item in steps}
            for step in steps:
                if step.status is not MissionStepStatus.PENDING:
                    continue
                if all(
                    statuses.get(dependency) is MissionStepStatus.SUCCEEDED
                    for dependency in step.depends_on
                ):
                    candidates.append((mission.created_at, step.sequence, step))
                    break
        return min(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None

    def _validate_site(
        self,
        source_dir: Path,
        mission: MissionRecord,
        *,
        enforce_threshold: bool = True,
    ) -> dict[str, Any]:
        issues: list[str] = []
        checks: list[str] = []
        index = source_dir / "index.html"
        css = source_dir / "styles.css"
        script = source_dir / "app.js"
        if not index.is_file():
            issues.append("index.html is missing")
            return {"success": False, "issues": issues, "checks": checks}
        markup = index.read_text(encoding="utf-8", errors="replace")
        stylesheet = ""
        parser = _ReferenceParser()
        try:
            parser.feed(markup)
        except Exception as exc:
            issues.append(f"HTML parse failed: {exc}")
        if "title" not in parser.tags or not "".join(parser.title_parts).strip():
            issues.append("HTML title is missing")
        else:
            checks.append("document-title")
        for tag in ("nav", "main"):
            if tag not in parser.tags:
                issues.append(f"Semantic element <{tag}> is missing")
            else:
                checks.append(f"semantic-{tag}")
        section_count = len(re.findall(r"<section\b", markup, re.I))
        if section_count < 5:
            issues.append("Website requires at least five substantive semantic sections")
        else:
            checks.append("content-depth")
        heading_count = len(re.findall(r"<h[1-3]\b", markup, re.I))
        if heading_count < 4:
            issues.append("Content hierarchy requires at least four visible headings")
        else:
            checks.append("heading-hierarchy")
        if not re.search(r"<meta[^>]+name=[\"']viewport[\"']", markup, re.I):
            issues.append("Responsive viewport metadata is missing")
        else:
            checks.append("responsive-viewport")
        lowered = markup.casefold()
        if any(token in lowered for token in ("lorem ipsum", "todo", "coming soon")):
            issues.append("Placeholder content remains in the deliverable")
        else:
            checks.append("no-placeholder-content")
        if not css.is_file() or css.stat().st_size < 2400:
            issues.append("styles.css is missing or too small to represent a complete layout")
        elif "styles.css" not in parser.references:
            issues.append("index.html does not reference styles.css")
        else:
            stylesheet = css.read_text(encoding="utf-8", errors="replace")
            if stylesheet.count("{") != stylesheet.count("}"):
                issues.append("CSS braces are unbalanced")
            else:
                checks.append("local-css")
                css_requirements = {
                    "design-tokens": len(
                        re.findall(r"--[a-z][a-z0-9-]*\s*:", stylesheet, re.I)
                    )
                    >= 4,
                    "responsive-layout": bool(
                        re.search(r"@media\s*\([^)]*(?:max|min)-width", stylesheet, re.I)
                    ),
                    "layout-system": bool(
                        re.search(r"display\s*:\s*(?:grid|flex)", stylesheet, re.I)
                    ),
                    "focus-state": ":focus" in stylesheet,
                    "interaction-state": ":hover" in stylesheet,
                    "motion-feedback": bool(
                        re.search(r"transition|animation|@keyframes", stylesheet, re.I)
                    ),
                    "fluid-type-scale": "clamp(" in stylesheet,
                    "visual-depth": bool(
                        re.search(
                            r"linear-gradient|radial-gradient|box-shadow|backdrop-filter",
                            stylesheet,
                            re.I,
                        )
                    ),
                    "component-system": len(
                        set(re.findall(r"\.([a-z][a-z0-9_-]{2,})", stylesheet, re.I))
                    )
                    >= 6,
                }
                for name, passed in css_requirements.items():
                    if passed:
                        checks.append(name)
                    else:
                        issues.append(f"CSS quality contract failed: {name}")
                if re.search(r"\b(?:darken|lighten)\s*\(", stylesheet, re.I):
                    issues.append("CSS contains unsupported preprocessor-only color functions")
        if not script.is_file() or script.stat().st_size < 700:
            issues.append("app.js is missing or has no meaningful interaction")
        elif "app.js" not in parser.references:
            issues.append("index.html does not reference app.js")
        else:
            javascript = script.read_text(encoding="utf-8", errors="replace")
            interaction_count = len(re.findall(r"addEventListener\s*\(", javascript))
            if interaction_count < 3:
                issues.append("JavaScript requires at least three event-driven interactions")
            else:
                checks.append("interaction-depth")
            if not re.search(
                r"classList\.|textContent\s*=|innerHTML\s*=|setAttribute\s*\(",
                javascript,
            ):
                issues.append("JavaScript does not produce an observable page-state change")
            else:
                checks.append("dynamic-state-change")
            if re.search(r"\balert\s*\(", javascript):
                issues.append("JavaScript uses blocking alert dialogs instead of page feedback")
            elif re.search(r"\bsimulat(?:e|ed|ion)\b", javascript, re.I):
                issues.append("JavaScript contains simulated completion behavior")
            else:
                checks.append("trustworthy-feedback")
            checks.append("local-javascript")
        accessibility_requirements = {
            "language": bool(
                re.search(r"<html[^>]+lang=[\"'][^\"']+", markup, re.I)
            ),
            "accessible-feedback": "aria-live" in lowered,
            "form-labels": "<form" not in lowered or "<label" in lowered,
        }
        for name, passed in accessibility_requirements.items():
            if passed:
                checks.append(f"accessibility-{name}")
            else:
                issues.append(f"Accessibility contract failed: {name}")
        stylesheet_references = (
            re.findall(r"url\(\s*['\"]?([^)'\"]+)", stylesheet, re.I)
            if css.is_file()
            else []
        )
        missing_references = []
        for reference in [*parser.references, *stylesheet_references]:
            clean = reference.split("#", 1)[0].split("?", 1)[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
                continue
            try:
                relative = self._safe_relative_path(clean.lstrip("/"))
            except ValueError:
                missing_references.append(reference)
                continue
            if not (source_dir / relative).is_file():
                missing_references.append(reference)
        if missing_references:
            issues.append("Missing local references: " + ", ".join(sorted(set(missing_references))))
        else:
            checks.append("local-references")
        objective = f"{mission.title} {mission.objective}".casefold()
        if ("旅遊" in objective or "travel" in objective) and not any(
            token in lowered for token in ("旅遊", "旅行", "行程", "目的地", "travel")
        ):
            issues.append("Generated content is not relevant to the travel objective")
        else:
            checks.append("objective-relevance")
        if re.search(r"[\u3400-\u9fff]", f"{mission.title}{mission.objective}"):
            visible_cjk = len(re.findall(r"[\u3400-\u9fff]", markup))
            if visible_cjk < 20:
                issues.append("Website language does not match the Chinese mission request")
            else:
                checks.append("mission-language")
        file_count = sum(1 for path in source_dir.rglob("*") if path.is_file())
        digest = self._source_hash(source_dir)
        issues = sorted(set(issues))
        checks = sorted(set(checks))
        quality_score = round((len(checks) / max(len(checks) + len(issues), 1)) * 100)
        threshold_met = quality_score >= self.settings.mission_quality_min_score
        if enforce_threshold and not threshold_met:
            issues.append(
                f"Website quality score {quality_score} is below required "
                f"{self.settings.mission_quality_min_score}"
            )
        return {
            "success": not issues,
            "issues": issues,
            "checks": checks,
            "quality_score": quality_score,
            "quality_threshold": self.settings.mission_quality_min_score,
            "quality_threshold_met": threshold_met,
            "section_count": section_count,
            "heading_count": heading_count,
            "file_count": file_count,
            "source_sha256": digest,
            "preview_url": f"/v1/missions/{mission.mission_id}/preview/",
        }

    @staticmethod
    def _site_quality_rank(report: dict[str, Any]) -> tuple[int, int, int, int]:
        issues = report.get("issues", [])
        checks = report.get("checks", [])
        return (
            int(not issues),
            -len(issues),
            int(report.get("quality_score", 0)),
            len(checks),
        )

