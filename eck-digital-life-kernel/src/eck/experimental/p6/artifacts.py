from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from eck.domain.enums import MissionCycleStatus, MissionStatus, MissionStepStatus
from eck.domain.models import MissionReactCycleRecord, MissionRecord, MissionStepRecord
from eck.experimental.p6.executor_base import MissionExecutorMixinBase


class MissionArtifactSupportMixin(MissionExecutorMixinBase):
    async def _record_cycle_event(
        self,
        cycle: MissionReactCycleRecord,
        step: MissionStepRecord,
    ) -> None:
        await self.events.publish(
            "MissionReactCycleSucceeded"
            if cycle.status is MissionCycleStatus.SUCCEEDED
            else "MissionReactCorrectionQueued"
            if cycle.status is MissionCycleStatus.NEEDS_CORRECTION
            else "MissionReactCycleFailed",
            step.step_id,
            {
                "mission_id": step.mission_id,
                "step_key": step.step_key,
                "attempt": cycle.attempt,
                "reason_summary": cycle.reason_summary,
                "action": cycle.action,
                "observation": cycle.observation,
                "correction": cycle.correction,
                "step_status": step.status.value,
            },
            correlation_id=step.mission_id,
        )

    async def _update_progress(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        if mission.status in {
            MissionStatus.AWAITING_REVIEW,
            MissionStatus.APPROVED,
            MissionStatus.CANCELLED,
        }:
            return
        steps = self.store.list_mission_steps(mission_id)
        succeeded = sum(item.status is MissionStepStatus.SUCCEEDED for item in steps)
        failed = next((item for item in steps if item.status is MissionStepStatus.FAILED), None)
        if failed:
            blocked = self.store.block_pending_mission_steps(
                mission_id,
                reason=f"Blocked by failed step {failed.step_key}.",
            )
            status = MissionStatus.BLOCKED
            current_step = f"步驟 {failed.step_key} 驗證失敗；已停止 {blocked} 個相依步驟"
        else:
            active = next(
                (
                    item
                    for item in steps
                    if item.status in {MissionStepStatus.RUNNING, MissionStepStatus.PENDING}
                ),
                None,
            )
            status = MissionStatus.ACTIVE
            current_step = (
                f"{active.step_key} · {active.objective}"
                if active
                else "所有執行步驟完成，正在整理驗收證據"
            )
        progress = {
            **mission.progress,
            "executor": self._executor_version,
            "completion_percent": round((succeeded / max(len(steps), 1)) * 100),
            "current_step": current_step,
            "steps_succeeded": succeeded,
            "steps_total": len(steps),
            "failed_step": failed.step_key if failed else None,
        }
        self.store.set_mission_status(mission_id, status, progress=progress)

    def _step_by_key(self, mission_id: str, step_key: str) -> MissionStepRecord:
        for step in self.store.list_mission_steps(mission_id):
            if step.step_key == step_key:
                return step
        raise KeyError(f"Mission step not found: {step_key}")

    def _mission_dir(self, mission_id: str) -> Path:
        if not self._mission_id_pattern.fullmatch(mission_id):
            raise ValueError("Invalid mission ID.")
        path = (self.root / mission_id).resolve()
        path.relative_to(self.root)
        return path

    def _source_dir(self, mission_id: str) -> Path:
        source = (self._mission_dir(mission_id) / "source").resolve()
        source.relative_to(self._mission_dir(mission_id))
        return source

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        normalized = PurePosixPath(value.replace("\\", "/"))
        if normalized.is_absolute() or not normalized.parts or ".." in normalized.parts:
            raise ValueError(f"Unsafe mission artifact path: {value}")
        if any(part.startswith(".") for part in normalized.parts):
            raise ValueError(f"Hidden mission artifact path is not allowed: {value}")
        return normalized.as_posix()

    def _validated_site_files(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        total = 0
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Website file entries must be objects.")
            path = self._safe_relative_path(str(item.get("path", "")))
            content = str(item.get("content", ""))
            if Path(path).suffix.casefold() not in self._allowed_site_suffixes:
                raise ValueError(f"Unsupported website file type: {path}")
            if path in seen or not content.strip():
                raise ValueError(f"Duplicate or empty website file: {path}")
            seen.add(path)
            total += len(content.encode("utf-8"))
            files.append({"path": path, "content": content})
        if len(files) > 30 or total > 1_000_000:
            raise ValueError("Website draft exceeds the file or byte contract.")
        required = {"index.html", "styles.css", "app.js", "README.md"}
        if not required.issubset(seen):
            raise ValueError("Website draft is missing required files.")
        return files

    def _validated_python_files(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        allowed = {".py", ".toml", ".md", ".txt", ".json", ".yaml", ".yml"}
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        total = 0
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Python project file entries must be objects.")
            path = self._safe_relative_path(str(item.get("path", "")))
            content = str(item.get("content", ""))
            if Path(path).suffix.casefold() not in allowed:
                raise ValueError(f"Unsupported Python project file type: {path}")
            if path in seen or not content.strip():
                raise ValueError(f"Duplicate or empty Python project file: {path}")
            seen.add(path)
            total += len(content.encode("utf-8"))
            files.append({"path": path, "content": content})
        if len(files) > 30 or total > 500_000:
            raise ValueError("Python project draft exceeds the file or byte contract.")
        if not any(path.startswith("tests/test_") and path.endswith(".py") for path in seen):
            raise ValueError("Python project requires deterministic pytest tests.")
        if not any(path.endswith(".py") and not path.startswith("tests/") for path in seen):
            raise ValueError("Python project requires executable source.")
        return files

    def _latest_step_by_action(self, mission_id: str, action_kind: str) -> MissionStepRecord:
        matching = [
            item
            for item in self.store.list_mission_steps(mission_id)
            if item.action_kind == action_kind
            and item.status in {MissionStepStatus.SUCCEEDED, MissionStepStatus.RUNNING}
        ]
        if not matching:
            raise KeyError(f"Mission action output not found: {action_kind}")
        return max(matching, key=lambda item: item.sequence)

    def _source_files(
        self,
        source_dir: Path,
        project_type: str,
    ) -> list[dict[str, str]]:
        allowed = (
            self._allowed_site_suffixes
            if project_type == "static_website"
            else {".py", ".toml", ".md", ".txt", ".json", ".yaml", ".yml"}
        )
        files: list[dict[str, str]] = []
        total = 0
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or ".git" in path.parts or path.suffix.casefold() not in allowed:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            total += len(content)
            if total > 900_000:
                raise ValueError("Mission source exceeds the review context contract.")
            files.append(
                {
                    "path": path.relative_to(source_dir).as_posix(),
                    "content": content,
                }
            )
        return files

    def _write_project_files(
        self,
        source_dir: Path,
        files: list[dict[str, str]],
    ) -> None:
        self._clear_source(source_dir)
        for item in files:
            target = source_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")

    def _fallback_quality_improvement(
        self,
        source_dir: Path,
        *,
        project_type: str,
        revision_key: str,
    ) -> list[dict[str, str]]:
        files = self._source_files(source_dir, project_type)
        slug = re.sub(r"[^a-z0-9]+", "-", revision_key.casefold()).strip("-")[:48]
        slug = slug or "quality"
        if project_type == "python_project":
            by_path = {item["path"]: item for item in files}
            by_path[f"QUALITY-{slug.upper()}.md"] = {
                "path": f"QUALITY-{slug.upper()}.md",
                "content": (
                    f"# Quality revision {slug}\n\n"
                    "The isolated deterministic tests and objective-specific interfaces remain "
                    "the acceptance evidence for this revision.\n"
                ),
            }
            return list(by_path.values())
        by_path = {item["path"]: item for item in files}
        stylesheet = by_path["styles.css"]
        marker = f"/* Deterministic quality refinement: {slug}. */"
        if marker in stylesheet["content"]:
            return files
        stylesheet["content"] += f"""

{marker}
:where(a, button, input, select, textarea):focus-visible {{
  outline: 3px solid var(--orange, #ff7d4d);
  outline-offset: 4px;
}}
html[data-quality-revision="{slug}"] .journey-card {{
  transform-origin: center bottom;
  transition: transform .25s ease, box-shadow .25s ease;
}}
html[data-quality-revision="{slug}"] .journey-card:hover {{
  transform: translateY(-4px);
  box-shadow: 0 18px 45px rgba(23, 32, 27, .12);
}}
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ scroll-behavior: auto !important; transition: none !important; }}
}}
"""
        script = by_path["app.js"]
        identifier = slug.replace("-", "_")
        script["content"] += f"""

document.documentElement.dataset.qualityRevision = '{slug}';
const qualityStatus_{identifier} = document.querySelector('#plan-result');
window.addEventListener('load', () => {{
  document.body.dataset.interfaceReady = 'true';
}});
document.addEventListener('keydown', (event) => {{
  if (event.key === 'Escape') {{
    document.querySelector('.site-header')?.classList.remove('menu-open');
  }}
}});
document.querySelectorAll('a[href^="#"]').forEach((link) => {{
  link.addEventListener('click', () => {{
    qualityStatus_{identifier}?.setAttribute('data-last-action', link.getAttribute('href') || '');
  }});
}});
"""
        return files

    def _fallback_python_files(self, mission: MissionRecord) -> list[dict[str, str]]:
        objective_tokens = [
            token
            for token in re.findall(r"[a-z][a-z0-9]{3,}", mission.objective.casefold())
            if token not in {"build", "create", "make", "project", "software", "simple"}
        ]
        focus = "_".join(objective_tokens[:3]) or "mission"
        function_name = f"build_{focus}_plan"
        source = f'''from __future__ import annotations


def {function_name}(goal: str, *, max_steps: int = 6) -> tuple[str, ...]:
    normalized = " ".join(goal.split())
    if not normalized:
        raise ValueError("goal must not be empty")
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    clauses = [item.strip() for item in normalized.replace("；", ";").split(";")]
    meaningful = [item for item in clauses if item]
    if len(meaningful) == 1:
        meaningful = [
            f"Define a measurable contract for {{normalized}}",
            f"Implement the smallest complete unit for {{normalized}}",
            f"Verify the delivered behavior for {{normalized}}",
        ]
    return tuple(meaningful[:max_steps])


def completion_ratio(completed: int, total: int) -> float:
    if total <= 0 or completed < 0 or completed > total:
        raise ValueError("invalid progress counts")
    return completed / total
'''
        tests = f'''import pytest

from mission_app import {function_name}, completion_ratio


def test_plan_is_bounded_and_goal_specific() -> None:
    goal = {mission.title!r}
    plan = {function_name}(goal, max_steps=3)
    assert 1 <= len(plan) <= 3
    assert any(goal in step for step in plan)


def test_progress_contract_rejects_invalid_counts() -> None:
    assert completion_ratio(2, 4) == 0.5
    with pytest.raises(ValueError):
        completion_ratio(5, 4)
'''
        readme = f"""# {mission.title}

{mission.objective}

This fallback is an executable, tested mission decomposition kernel. The P6 coder worker may
replace it with a more domain-specific implementation, but Docker verification remains mandatory.
"""
        return [
            {"path": "mission_app.py", "content": source},
            {"path": "tests/test_mission_app.py", "content": tests},
            {"path": "README.md", "content": readme},
        ]

    def _fallback_site_files(self, mission: MissionRecord) -> list[dict[str, str]]:
        title = html.escape(mission.title)
        objective = html.escape(mission.objective)
        travel = bool(re.search(r"旅遊|旅行|travel", f"{mission.title} {mission.objective}", re.I))
        theme_title = "緩慢出走" if travel else title
        theme_kicker = "CURATED JOURNEYS" if travel else "ECK VERIFIED DELIVERY"
        cards = (
            (
                ("山海之間", "三日東岸慢旅", "沿著海岸、部落與山徑安排不趕路的留白。"),
                ("城市漫步", "巷弄味覺地圖", "從早市到夜色，用步行距離串起城市的日常。"),
                ("島嶼週末", "兩日輕裝提案", "以交通、預算與天候為核心，快速建立可行行程。"),
            )
            if travel
            else (
                ("清楚", "從目標開始", "把任務需求整理為能被檢查的完整成果。"),
                ("可用", "直接預覽", "所有樣式與互動都由本機檔案提供。"),
                ("可驗證", "保留證據", "來源、封裝雜湊與驗收狀態皆可追溯。"),
            )
        )
        card_markup = "".join(
            f'<article class="journey-card"><span>{html.escape(kicker)}</span>'
            f"<h3>{html.escape(heading)}</h3><p>{html.escape(copy)}</p>"
            '<button class="card-action" type="button">加入靈感清單</button></article>'
            for kicker, heading, copy in cards
        )
        index = f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{objective}">
  <title>{title}</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="#top" aria-label="回到首頁">ECK<span>JOURNEY</span></a>
    <nav aria-label="主要導覽">
      <a href="#ideas">靈感</a><a href="#planner">規劃</a><a href="#about">理念</a>
    </nav>
    <button class="menu-button" type="button" aria-expanded="false">選單</button>
  </header>
  <main id="top">
    <section class="hero">
      <div class="hero-copy">
      <p class="eyebrow">{theme_kicker}</p>
      <h1>{theme_title}<br><em>把時間還給風景</em></h1>
      <p class="lead">{objective}</p><a class="primary-action" href="#ideas">開始探索</a></div>
      <div class="hero-art" role="img" aria-label="抽象山海旅行風景">
        <span class="sun"></span><span class="mountain one"></span>
        <span class="mountain two"></span><span class="route"></span>
      </div>
    </section>
    <section class="idea-section" id="ideas">
      <div class="section-heading"><p class="eyebrow">SELECTED IDEAS</p>
      <h2>從一個方向，長出自己的行程</h2></div>
      <div class="card-grid">{card_markup}</div>
    </section>
    <section class="planner" id="planner"><div><p class="eyebrow">QUICK PLANNER</p>
      <h2>今天想去哪裡？</h2><p>選擇旅行節奏，立即取得一份本機產生的起始建議。</p></div>
      <form id="planner-form"><label>旅行節奏<select id="pace">
        <option value="慢慢走">慢慢走</option>
        <option value="城市探索">城市探索</option>
        <option value="自然冒險">自然冒險</option>
      </select></label><button type="submit">產生建議</button></form>
      <output id="plan-result" aria-live="polite">選一種節奏，讓旅程開始成形。</output>
    </section>
    <section class="about" id="about"><p class="eyebrow">WHY THIS EXISTS</p>
      <h2>少一點清單，多一點真正抵達。</h2>
      <p>這份成果由 ECK 在隔離任務工作區建立，通過本機檔案引用、語意結構、
      響應式版面與主題相關性檢查後才提交。</p>
    </section>
    <section class="idea-section principles" id="principles">
      <div class="section-heading"><p class="eyebrow">TRAVEL PRINCIPLES</p>
      <h2>讓每次出發都有根據，也保留驚喜。</h2></div>
      <div class="card-grid">
        <article class="journey-card"><span>01</span><h3>先確認限制</h3>
          <p>從預算、交通與天候建立可行邊界。</p></article>
        <article class="journey-card"><span>02</span><h3>再安排節奏</h3>
          <p>每天只保留真正值得抵達的重點。</p></article>
        <article class="journey-card"><span>03</span><h3>最後留下彈性</h3>
          <p>用備選方案面對真實旅程的變化。</p></article>
      </div>
    </section>
  </main>
  <footer><span>AI/ECK 協作建立</span><span>LOCAL · VERIFIED · PORTABLE</span></footer>
  <script src="app.js"></script>
</body>
</html>
"""
        styles = """
:root {
  --ink: #17201b; --paper: #f3efe5; --lime: #d8ff72; --orange: #ff7d4d;
  --line: rgba(23, 32, 27, .18); --serif: Georgia, 'Times New Roman', serif;
  --sans: Inter, 'Noto Sans TC', system-ui, sans-serif;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; color: var(--ink); background: var(--paper); font-family: var(--sans); }
a { color: inherit; text-decoration: none; }
.site-header { position: sticky; top: 0; z-index: 10; display: flex; align-items: center;
  justify-content: space-between; padding: 1rem clamp(1rem, 4vw, 4.5rem);
  border-bottom: 1px solid var(--line); background: rgba(243, 239, 229, .9);
  backdrop-filter: blur(16px); }
.brand { font-weight: 900; letter-spacing: -.05em; }
.brand span { display: block; font-size: .5rem; letter-spacing: .24em; }
.site-header nav { display: flex; gap: 1.6rem; font-size: .78rem; }
.site-header nav a:hover { opacity: .55; }
.menu-button { display: none; border: 1px solid var(--line); background: transparent;
  padding: .55rem .8rem; }
.hero { min-height: 78vh; display: grid; grid-template-columns: 1.05fr .95fr;
  align-items: center; padding: clamp(3rem, 8vw, 8rem) clamp(1rem, 7vw, 8rem);
  overflow: hidden; }
.eyebrow { font-size: .64rem; font-weight: 800; letter-spacing: .2em; }
.hero h1, .section-heading h2, .planner h2, .about h2 {
  font: 500 clamp(3rem, 8vw, 7.5rem)/.88 var(--serif); letter-spacing: -.065em;
  margin: .4rem 0 1.5rem; }
.hero h1 em { color: var(--orange); font-weight: 400; }
.lead { max-width: 38rem; font-size: 1rem; line-height: 1.8; }
.primary-action, .planner button { display: inline-flex; margin-top: 1rem;
  padding: .9rem 1.3rem; border: 1px solid var(--ink); background: var(--ink);
  color: var(--paper); font-weight: 750; }
.hero-art { position: relative; min-height: 34rem; border-radius: 50% 50% 4% 4%;
  background: linear-gradient(#c4e3dd 0 53%, #99c8c4 53%); overflow: hidden;
  box-shadow: inset 0 0 0 1px var(--line); }
.sun { position: absolute; top: 12%; right: 18%; width: 6rem; height: 6rem;
  border-radius: 50%; background: var(--lime); }
.mountain { position: absolute; bottom: 40%; width: 0; height: 0;
  border-left: 12rem solid transparent; border-right: 12rem solid transparent;
  border-bottom: 16rem solid #415b4b; }
.mountain.one { left: -20%; }
.mountain.two { right: -26%; bottom: 35%; border-bottom-color: #6f8c75; }
.route { position: absolute; left: 50%; bottom: -10%; width: 4rem; height: 70%;
  border: 3px solid rgba(243, 239, 229, .75); border-color: rgba(243, 239, 229, .75)
  transparent transparent transparent; border-radius: 50%; transform: rotate(-8deg); }
.idea-section { padding: 7rem clamp(1rem, 7vw, 8rem); border-top: 1px solid var(--line); }
.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 2rem; }
.section-heading h2 { max-width: 14ch; font-size: clamp(2.5rem, 5vw, 5rem); }
.card-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }
.journey-card { min-height: 23rem; display: flex; flex-direction: column; padding: 1.5rem;
  border: 1px solid var(--line); background: rgba(255, 255, 255, .24); transition: .25s; }
.journey-card:hover { transform: translateY(-5px); background: var(--lime); }
.journey-card span { font-size: .6rem; letter-spacing: .15em; }
.journey-card h3 { font: 500 2rem var(--serif); margin: 3rem 0 1rem; }
.journey-card p { line-height: 1.7; }
.card-action { margin-top: auto; align-self: start; border: 0; border-bottom: 1px solid;
  background: transparent; padding: .5rem 0; cursor: pointer; }
.card-action.saved { font-weight: 800; }
.planner { display: grid; grid-template-columns: 1fr 1fr; gap: 4rem;
  padding: 7rem clamp(1rem, 7vw, 8rem); background: var(--ink); color: var(--paper); }
.planner h2 { font-size: clamp(2.5rem, 5vw, 5rem); }
#planner-form { display: flex; gap: .7rem; align-items: end; }
#planner-form label { display: grid; gap: .4rem; flex: 1; font-size: .7rem; }
select { width: 100%; padding: .85rem; border: 1px solid rgba(255, 255, 255, .3);
  background: transparent; color: var(--paper); }
select option { color: var(--ink); }
.planner button { margin: 0; background: var(--lime); color: var(--ink); }
#plan-result { grid-column: 2; padding: 1rem; border-left: 3px solid var(--orange);
  line-height: 1.7; }
.about { padding: 8rem clamp(1rem, 12vw, 14rem); text-align: center; }
.about h2 { font-size: clamp(3rem, 7vw, 7rem); }
.about > p:last-child { max-width: 48rem; margin: auto; line-height: 1.9; }
footer { display: flex; justify-content: space-between; padding: 1.5rem clamp(1rem, 4vw, 4.5rem);
  border-top: 1px solid var(--line); font-size: .62rem; letter-spacing: .12em; }
@media (max-width: 800px) {
  .site-header nav { display: none; }
  .site-header nav.open { position: absolute; top: 100%; left: 0; right: 0; display: flex;
    flex-direction: column; padding: 1rem; background: var(--paper);
    border-bottom: 1px solid var(--line); }
  .menu-button { display: block; }
  .hero { grid-template-columns: 1fr; gap: 3rem; }
  .hero-art { min-height: 26rem; }
  .section-heading { display: block; }
  .card-grid, .planner { grid-template-columns: 1fr; }
  .planner { gap: 2rem; }
  #plan-result { grid-column: 1; }
  .hero h1 { font-size: clamp(3rem, 15vw, 5.5rem); }
}
"""
        script = """
const menuButton = document.querySelector('.menu-button');
const nav = document.querySelector('.site-header nav');
menuButton.addEventListener('click', () => {
  const open = nav.classList.toggle('open');
  menuButton.setAttribute('aria-expanded', String(open));
});
document.querySelectorAll('.card-action').forEach((button) => {
  button.addEventListener('click', () => {
    const saved = button.classList.toggle('saved');
    button.textContent = saved ? '已加入靈感' : '加入靈感清單';
  });
});
const ideas = {
  '慢慢走': '保留半天空白，只選一個街區與一頓期待的晚餐。',
  '城市探索': '從市場、博物館與夜間散步建立三段式路線。',
  '自然冒險': '先確認天候與交通，再選擇一條可安全折返的步道。',
};
document.querySelector('#planner-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const pace = document.querySelector('#pace').value;
  document.querySelector('#plan-result').textContent = `${pace}提案：${ideas[pace]}`;
});
"""
        readme = f"""# {mission.title}

{mission.objective}

## 執行

直接開啟 `index.html`，或由 ECK 的任務預覽網址檢視。

## 驗證

本專案由 P6 Durable Mission Executor 建立，只有通過本機靜態網站契約後才會封裝與提交。
"""
        return [
            {"path": "index.html", "content": index},
            {"path": "styles.css", "content": styles},
            {"path": "app.js", "content": script},
            {"path": "README.md", "content": readme},
        ]

    @staticmethod
    def _clear_source(source_dir: Path) -> None:
        source_root = source_dir.resolve()
        if source_root.name != "source" or not source_root.parent.name.startswith("mission_"):
            raise ValueError("Mission source cleanup escaped the isolated workspace.")
        for path in source_root.iterdir():
            if path.name == ".git":
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    @staticmethod
    def _directory_bytes(path: Path, *, missing_ok: bool = False) -> int:
        if not path.exists():
            if missing_ok:
                return 0
            raise FileNotFoundError(path)
        total = 0
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _source_hash(source_dir: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            digest.update(path.relative_to(source_dir).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise
            value = json.loads(cleaned[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("Model response must be a JSON object.")
        return value

    @staticmethod
    def _safe_project_name(value: str, mission_id: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:50]
        if len(normalized) < 3 or not normalized[0].isalpha():
            normalized = f"eck-mission-{mission_id[-8:]}"
        return normalized

    def _project_name(self, mission: MissionRecord) -> str:
        sequence = self.store.mission_sequence(mission.mission_id)
        return f"{self._repository_topic(mission)}-task-{sequence:04d}"

    @staticmethod
    def _repository_topic(mission: MissionRecord) -> str:
        text = f"{mission.title} {mission.objective}"
        mappings = (
            (r"旅遊|旅行|travel", "travel"),
            (r"股票|投資|stock|finance", "finance"),
            (r"影片|video", "video"),
            (r"圖片|影像|image", "image"),
            (r"遊戲|game", "game"),
            (r"網站|網頁|website|landing", "website"),
            (r"app|應用", "app"),
            (r"api", "api"),
        )
        for pattern, topic in mappings:
            if re.search(pattern, text, re.I):
                return topic
        tokens = re.findall(r"[a-z][a-z0-9]{2,}", text.casefold())
        return "-".join(tokens[:3])[:36] or "software"
