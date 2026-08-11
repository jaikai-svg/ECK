import type {
  ActivitySummary,
  ArtifactLink,
  ConversationMessage,
  LibraryPage,
  ProjectDetail,
  ProjectSummary,
  SkillPage,
  WorkspaceHome,
} from "./workspace_types.js";

export const escapeHtml = (value: unknown): string => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

export const concise = (value: unknown, limit = 180): string => {
  const serialized = value && typeof value === "object" ? JSON.stringify(value) : "";
  const text = typeof value === "string"
    ? value
    : serialized === "{}" || serialized === "[]" ? "" : serialized || String(value ?? "");
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit)}…` : normalized;
};

export const formatTime = (value: string | null | undefined): string => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat("zh-TW", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
};

export const formatBytes = (value: unknown): string => {
  const bytes = Number(value ?? 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
};

const statusLabel = (status: string): string => ({
  active: "執行中",
  preparing: "規劃中",
  blocked: "受阻",
  awaiting_review: "等待驗收",
  approved: "已完成",
  rejected: "待改善",
  cancelled: "已取消",
  running: "執行中",
  queued: "排程中",
  waiting: "等待條件",
  idle: "待命",
}[status] ?? status);

const artifactMarkup = (items: ArtifactLink[]): string => items.map((item) => `
  <a class="artifact-link" href="${escapeHtml(item.url)}"
    ${item.kind === "preview" ? 'target="_blank" rel="noreferrer"' : "download"}>
    <span>${item.kind === "preview" ? "↗" : "↓"}</span>
    <b>${escapeHtml(item.label)}</b>
    ${item.bytes ? `<small>${formatBytes(item.bytes)}</small>` : ""}
  </a>
`).join("");

export class HomeComponent {
  render(data: WorkspaceHome): void {
    const activity = data.activity;
    this.text("kernel-phase", data.kernel.phase.toUpperCase());
    this.text("pending-work-count", String(data.kernel.pending_tasks));
    this.text("verified-experience-count", String(data.learning.verified_experiences));
    this.text(
      "verified-skill-count",
      String(data.learning.memory_skills + data.learning.runtime_skills),
    );
    this.renderActivity(activity, data.resources);
    this.renderProjects("running-projects", data.running_projects, "目前沒有執行中的專案。");
    this.renderProjects("recent-results", data.recent_results, "尚無可展示的近期成果。", true);
  }

  private renderActivity(activity: ActivitySummary, resources: Record<string, unknown>): void {
    const state = document.querySelector<HTMLElement>("#activity-status");
    if (state) {
      state.textContent = statusLabel(activity.state);
      state.dataset.state = activity.state;
    }
    this.text("activity-title", activity.title);
    this.text("activity-detail", activity.detail);
    const progress = document.querySelector<HTMLElement>("#activity-progress-bar");
    const progressValue = activity.progress_percent;
    if (progress) {
      progress.style.width = `${progressValue ?? 0}%`;
      progress.parentElement?.classList.toggle("is-unknown", progressValue === null);
    }
    this.text("activity-progress-value", progressValue === null ? "未提供百分比" : `${progressValue}%`);
    this.text("activity-waiting", activity.waiting_on ? `等待：${activity.waiting_on}` : "沒有等待外部條件");
    const pressure = resources.pressure as Record<string, unknown> | undefined;
    const process = resources.process as Record<string, unknown> | undefined;
    this.text("activity-resource", [
      pressure?.level ? `資源 ${String(pressure.level)}` : "資源未知",
      process?.memory_bytes ? `ECK ${formatBytes(process.memory_bytes)}` : "",
    ].filter(Boolean).join(" · "));

    const order = ["goal", "plan", "action", "observation", "correction", "verification", "conclusion"];
    const labels: Record<string, string> = {
      goal: "目標",
      plan: "計畫",
      action: "行動",
      observation: "觀察",
      correction: "修正",
      verification: "驗證",
      conclusion: "結論",
    };
    const feed = document.querySelector<HTMLElement>("#activity-summary");
    if (feed) {
      feed.innerHTML = order.map((key) => {
        const value = concise(activity.summary[key], 260);
        return `<div class="reason-row ${value ? "" : "is-empty"}"><span>${labels[key]}</span><p>${escapeHtml(value || "尚無可驗證紀錄")}</p></div>`;
      }).join("");
    }
  }

  private renderProjects(
    id: string,
    projects: ProjectSummary[],
    empty: string,
    result = false,
  ): void {
    const target = document.querySelector<HTMLElement>(`#${id}`);
    if (!target) return;
    target.innerHTML = projects.length
      ? projects.map((project) => projectCard(project, result)).join("")
      : `<div class="empty-state"><span>◇</span><p>${escapeHtml(empty)}</p></div>`;
  }

  private text(id: string, value: string): void {
    const element = document.querySelector<HTMLElement>(`#${id}`);
    if (element) element.textContent = value;
  }
}

export class ProjectListComponent {
  render(projects: ProjectSummary[], append = false): void {
    const target = document.querySelector<HTMLElement>("#project-grid");
    if (!target) return;
    const html = projects.map((project) => projectCard(project)).join("");
    target.innerHTML = append ? target.innerHTML + html : html;
    if (!target.innerHTML) {
      target.innerHTML = '<div class="empty-state wide"><span>＋</span><p>建立第一個持久化專案，ECK 會保留拆解、測試與成果。</p></div>';
    }
  }

  renderDetail(detail: ProjectDetail): void {
    const project = detail.project;
    const dialog = document.querySelector<HTMLDialogElement>("#project-detail-dialog");
    const body = document.querySelector<HTMLElement>("#project-detail-body");
    if (!dialog || !body) return;
    const steps = detail.steps.map((step) => `
      <li data-state="${escapeHtml(step.status)}">
        <i></i><div><b>${escapeHtml(step.objective)}</b><small>${escapeHtml(step.step_key)} · ${escapeHtml(step.status)} · ${escapeHtml(step.attempts)}/${escapeHtml(step.max_attempts)}</small></div>
      </li>
    `).join("");
    const cycles = detail.react_summaries.map((cycle) => `
      <details class="react-cycle">
        <summary><span>第 ${escapeHtml(cycle.attempt)} 輪</span><b>${escapeHtml(concise(cycle.plan, 90))}</b><small>${formatTime(String(cycle.created_at ?? ""))}</small></summary>
        <div class="react-grid">
          ${reasonCell("目標", cycle.goal)}
          ${reasonCell("計畫", cycle.plan)}
          ${reasonCell("行動", cycle.action)}
          ${reasonCell("觀察", cycle.observation)}
          ${reasonCell("修正", cycle.correction)}
          ${reasonCell("驗證", cycle.verification)}
          ${reasonCell("結論", cycle.conclusion)}
        </div>
      </details>
    `).join("");
    const skillUsages = detail.skill_usages.map((usage) => `
      <li><b>${escapeHtml(usage.skill_name)}</b><span>v${escapeHtml(usage.skill_version)}</span><small>${escapeHtml(usage.verification_status)}</small></li>
    `).join("");
    const review = project.status === "awaiting_review" ? `
      <form id="project-review-form" data-project-id="${escapeHtml(project.project_id)}">
        <label>驗收意見<textarea name="feedback" rows="4" maxlength="4000" placeholder="草稿會自動保存，直到成功送出。"></textarea></label>
        <div class="dialog-actions">
          <button class="secondary-button" type="submit" name="decision" value="revise">退回改善</button>
          <button class="primary-button" type="submit" name="decision" value="approve">驗收通過</button>
        </div>
      </form>
    ` : project.status === "rejected" ? `
      <button class="primary-button" type="button" data-reopen-project="${escapeHtml(project.project_id)}">依意見重新執行</button>
    ` : "";
    body.innerHTML = `
      <header class="project-detail-head">
        <div><span class="status-chip" data-state="${escapeHtml(project.status)}">${escapeHtml(statusLabel(project.status))}</span><h2>${escapeHtml(project.title)}</h2><p>${escapeHtml(project.objective)}</p></div>
        <div class="project-score"><b>${project.progress_percent}%</b><small>真實完成度</small></div>
      </header>
      <div class="project-detail-meta"><span>更新 ${formatTime(project.updated_at)}</span><span>修訂 ${project.revision}</span><span>工作區 ${formatBytes(detail.workspace_bytes)}</span></div>
      ${detail.artifacts.length ? `<section><div class="section-heading"><h3>成果</h3></div><div class="artifact-grid">${artifactMarkup(detail.artifacts)}</div></section>` : ""}
      <section><div class="section-heading"><h3>真實使用技能</h3><span>${detail.skill_usages.length} 筆</span></div>${skillUsages ? `<ul class="usage-list">${skillUsages}</ul>` : '<div class="empty-state"><p>這個專案沒有實際 Runtime Skill Worker 紀錄。</p></div>'}</section>
      <section><div class="section-heading"><h3>任務拆解</h3><span>${detail.steps.length} steps</span></div><ol class="step-timeline">${steps || "<li><div><b>等待 ECK 建立計畫</b></div></li>"}</ol></section>
      <section><div class="section-heading"><h3>可稽核思考摘要</h3><span>不包含私有 CoT</span></div><div class="react-list">${cycles || '<div class="empty-state"><p>尚無已執行的 ReAct 紀錄。</p></div>'}</div></section>
      ${project.result_summary ? `<section class="result-summary"><h3>成果摘要</h3><p>${escapeHtml(project.result_summary)}</p></section>` : ""}
      ${project.review_feedback ? `<section class="review-history"><h3>最近驗收意見</h3><p>${escapeHtml(project.review_feedback)}</p></section>` : ""}
      ${review}
    `;
    if (!dialog.open) dialog.showModal();
  }
}

export class ChatComponent {
  render(messages: ConversationMessage[]): void {
    const target = document.querySelector<HTMLElement>("#chat-messages");
    if (!target) return;
    target.innerHTML = messages.map((message) => `
      <article class="chat-message ${message.role}">
        <span>${message.role === "user" ? "你" : "ECK"}</span>
        <div><p>${escapeHtml(message.content).replaceAll("\n", "<br>")}</p>${this.artifacts(message.artifacts ?? [])}</div>
      </article>
    `).join("");
    target.hidden = messages.length === 0;
    target.scrollTop = target.scrollHeight;
  }

  private artifacts(items: Array<Record<string, unknown>>): string {
    return items.map((item) => {
      const url = String(item.url ?? item.artifact_url ?? "");
      if (!url.startsWith("/")) return "";
      const kind = String(item.kind ?? item.type ?? "artifact");
      if (kind.includes("image")) {
        return `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer"><img class="chat-artifact-image" src="${escapeHtml(url)}" alt="ECK 生成成果" loading="lazy"></a>`;
      }
      if (kind.includes("video")) {
        return `<video class="chat-artifact-video" src="${escapeHtml(url)}" controls preload="metadata"></video>`;
      }
      return `<a class="inline-result" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">開啟成果</a>`;
    }).join("");
  }
}

export class LibraryComponent {
  render(data: LibraryPage, append = false): void {
    const book = data.book;
    this.text("library-card-count", String(data.page.total));
    this.text("library-chapter-count", String(data.chapters.length));
    this.text("library-revision", `r${String(book.revision ?? 0)}`);
    this.text("library-unresolved-count", String(book.unresolved_question_count ?? 0));
    this.text("library-book-title", String(book.title ?? "ECK Verified Knowledge"));
    this.text("library-book-summary", String(book.description ?? "尚無已驗證知識。"));
    const digest = String(data.cache.content_sha256 ?? "");
    this.text("library-cache-state", `${data.cache.hit ? "靜態快取" : "已增量更新"} · ${digest.slice(0, 12) || "empty"}`);
    const chapters = document.querySelector<HTMLElement>("#library-chapters");
    if (chapters) {
      chapters.innerHTML = data.chapters.map((chapter) => `
        <span>${escapeHtml(chapter.title)} <b>${escapeHtml(chapter.card_count)}</b></span>
      `).join("") || "<span>尚無章節</span>";
    }
    const target = document.querySelector<HTMLElement>("#library-cards");
    if (!target) return;
    const html = data.items.map((card) => {
      const sources = card.sources.slice(0, 3).map((source) => {
        const url = String(source.url ?? "");
        const label = concise(source.claim, 70) || String(source.evidence_id ?? "證據");
        return url.startsWith("http")
          ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)} ↗</a>`
          : `<span>${escapeHtml(label)}</span>`;
      }).join("");
      return `
        <article class="library-card">
          <div class="library-card-top"><span>${escapeHtml(card.chapter)}</span><small>r${card.revision} · ${formatTime(card.created_at)}</small></div>
          <h3>${escapeHtml(card.title)}</h3>
          <p>${escapeHtml(card.claim)}</p>
          <div class="confidence-row"><span>可信度</span><div><i style="width:${Math.round(card.confidence * 100)}%"></i></div><b>${Math.round(card.confidence * 100)}%</b></div>
          <div class="verification-tags"><span>${card.externally_grounded ? "外部證據" : "缺少外部證據"}</span><span>${card.reproducible ? "可重現" : "未重現"}</span><span>${escapeHtml(card.verification_result)}</span></div>
          <details><summary>證據與修訂</summary><div class="library-detail"><b>來源</b><div class="source-list">${sources || "<span>僅保留證據識別碼</span>"}</div><b>反例</b><p>${escapeHtml(card.counterexamples.join(" · ") || "目前沒有記錄到反例；不代表不存在。")}</p><b>未解問題</b><p>${escapeHtml(card.unresolved_questions.join(" · ") || "目前沒有記錄。")}</p><code>${escapeHtml(card.content_sha256)}</code></div></details>
        </article>
      `;
    }).join("");
    target.innerHTML = append ? target.innerHTML + html : html;
    if (!target.innerHTML) {
      target.innerHTML = '<div class="empty-state wide"><p>尚無通過驗證准入的知識卡片。研究紀錄本身不會自動被當成知識。</p></div>';
    }
    const more = document.querySelector<HTMLButtonElement>("#load-more-library");
    if (more) more.hidden = data.page.next_offset === null;
  }

  private text(id: string, value: string): void {
    const target = document.querySelector<HTMLElement>(`#${id}`);
    if (target) target.textContent = value;
  }
}

export class SkillComponent {
  render(data: SkillPage, append = false): void {
    this.text("skill-available-count", String(data.counts.available ?? 0));
    this.text("skill-learning-count", String(data.counts.learning ?? 0));
    this.text("skill-failed-count", String(data.counts.failed ?? 0));
    this.text("skill-retired-count", String(data.counts.retired ?? 0));
    this.text("skill-policy", data.matching_policy);
    const target = document.querySelector<HTMLElement>("#skill-cards");
    if (!target) return;
    const html = data.items.map((skill) => `
      <article class="skill-card" data-phase="${escapeHtml(skill.phase)}">
        <div class="skill-card-top"><span class="status-chip" data-state="${escapeHtml(skill.phase)}">${escapeHtml(skill.phase)}</span><small>${escapeHtml(skill.source_kind)} · ${formatTime(skill.updated_at)}</small></div>
        <h3>${escapeHtml(skill.name)} ${skill.version ? `<em>v${escapeHtml(skill.version)}</em>` : ""}</h3>
        <p>${escapeHtml(skill.description || skill.capability)}</p>
          <div class="skill-facts"><span>${skill.executable ? "可執行" : "程序記憶"}</span><span>${escapeHtml(skill.source)}</span><span>${skill.relationship_kind === "executed" ? "實際執行任務" : "驗證來源任務"} ${skill.completed_task_count}</span></div>
        <details><summary>能力範圍與測試</summary><div class="skill-detail"><b>操作</b><p>${escapeHtml(skill.scope.operations.join(" · ") || "未宣告操作")}</p><b>權限</b><p>${escapeHtml(skill.scope.permissions.join(" · ") || "沒有額外權限")}</p><b>測試結果</b><pre>${escapeHtml(concise(skill.test_result, 1000) || "尚無測試報告")}</pre><b>最近完成任務</b><p>${escapeHtml(skill.completed_tasks.map((task) => String(task.goal ?? "")).join(" · ") || "尚無直接使用紀錄")}</p></div></details>
      </article>
    `).join("");
    target.innerHTML = append ? target.innerHTML + html : html;
    if (!target.innerHTML) {
      target.innerHTML = '<div class="empty-state wide"><p>這個狀態目前沒有技能。</p></div>';
    }
    const more = document.querySelector<HTMLButtonElement>("#load-more-skills");
    if (more) more.hidden = data.page.next_offset === null;
  }

  private text(id: string, value: string): void {
    const target = document.querySelector<HTMLElement>(`#${id}`);
    if (target) target.textContent = value;
  }
}

const reasonCell = (label: string, value: unknown): string => {
  const text = concise(value, 520);
  return `<div class="reason-cell ${text ? "" : "is-empty"}"><span>${escapeHtml(label)}</span><p>${escapeHtml(text || "尚無可驗證紀錄")}</p></div>`;
};

const projectCard = (project: ProjectSummary, result = false): string => `
  <article class="project-card" data-open-project="${escapeHtml(project.project_id)}">
    <button type="button" aria-label="開啟 ${escapeHtml(project.title)}"></button>
    <div class="project-card-top"><span class="status-chip" data-state="${escapeHtml(project.status)}">${escapeHtml(statusLabel(project.status))}</span><time>${formatTime(project.updated_at)}</time></div>
    <h3>${escapeHtml(project.title)}</h3>
    <p>${escapeHtml(concise(result ? project.result_summary || project.objective : project.current_step || project.objective, 150))}</p>
    <div class="project-card-bottom">
      <div class="mini-progress"><i style="width:${project.progress_percent}%"></i></div>
      <b>${project.progress_percent}%</b>
      ${project.waiting_on ? `<small>等待 ${escapeHtml(project.waiting_on)}</small>` : ""}
    </div>
  </article>
`;
