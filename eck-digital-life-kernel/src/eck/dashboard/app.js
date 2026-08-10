const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const CHAT_STORAGE_KEY = "eck-chat-history-v2";
const MISSION_DRAFT_STORAGE_KEY = "eck-mission-drafts-v1";
let kernelStartedAt = null;
let skillTreeLastLoaded = 0;
let skillTreeRevision = "";
let refreshInFlight = false;
let systemResourcesInFlight = false;
let systemResourcesLastLoaded = 0;
let slashCommands = [];
let slashMatches = [];
let slashSelection = 0;
let missionDrafts = loadMissionDrafts();

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 3000);
}

function loadMissionDrafts() {
  try {
    const value = JSON.parse(localStorage.getItem(MISSION_DRAFT_STORAGE_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function missionDraftKey(form) {
  const kind = form.classList.contains("mission-review-form")
    ? "review"
    : form.classList.contains("mission-edit-form") ? "edit" : "completion";
  return `${kind}:${form.dataset.missionId || ""}`;
}

function saveMissionDraft(form) {
  const values = {};
  new FormData(form).forEach((value, key) => {
    values[key] = String(value);
  });
  missionDrafts[missionDraftKey(form)] = values;
  localStorage.setItem(MISSION_DRAFT_STORAGE_KEY, JSON.stringify(missionDrafts));
}

function clearMissionDraft(form) {
  delete missionDrafts[missionDraftKey(form)];
  localStorage.setItem(MISSION_DRAFT_STORAGE_KEY, JSON.stringify(missionDrafts));
}

function missionDraft(kind, missionId, field) {
  return missionDrafts[`${kind}:${missionId}`]?.[field] || "";
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`${response.status}: ${await response.text()}`);
  }
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

function formatTime(value, includeDate = false) {
  if (!value) return "—";
  const options = includeDate
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" };
  return new Intl.DateTimeFormat("zh-TW", options).format(new Date(value));
}

function formatCount(value) {
  return new Intl.NumberFormat("zh-TW").format(Number(value || 0));
}

function formatBytes(value, digits = 1) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit >= 3 ? digits : 0)} ${units[unit]}`;
}

function formatDuration(value) {
  if (!value) return { days: "00天", clock: "00:00:00" };
  let totalSeconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  const days = Math.floor(totalSeconds / 86400);
  totalSeconds %= 86400;
  const hours = Math.floor(totalSeconds / 3600);
  totalSeconds %= 3600;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return {
    days: `${String(days).padStart(2, "0")}天`,
    clock: [hours, minutes, seconds].map((item) => String(item).padStart(2, "0")).join(":"),
  };
}

function updateUptime() {
  const duration = formatDuration(kernelStartedAt);
  $("#uptime-days").textContent = duration.days;
  $("#uptime-clock").textContent = duration.clock;
}

function safeUrl(value) {
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function outcomeLabel(value) {
  return {
    verified_success: "已驗證成功",
    verified_failure: "驗證失敗",
    unverifiable: "證據不足",
    constraint_violation: "違反限制",
  }[value] || value;
}

function challengeStatusLabel(value) {
  return {
    planning: "規劃中",
    exploring: "探索中",
    capability_gap: "等待能力接入",
    awaiting_human: "等待必要人工操作",
    active: "執行中",
    observing: "分析回饋中",
    succeeded: "已達成",
    blocked: "安全停止",
    stopped: "已停止",
  }[value] || value;
}

function missionStatusLabel(value) {
  return {
    active: "進行中",
    preparing: "準備中",
    blocked: "受阻",
    awaiting_review: "等待你驗收",
    approved: "已通過",
    rejected: "需改善",
    cancelled: "已取消",
  }[value] || value;
}

function taskStatusLabel(value) {
  return {
    queued: "已排隊",
    waiting_approval: "等待核准",
    running: "執行中",
    verified_success: "已驗證",
    verified_failure: "驗證失敗",
    unverifiable: "證據不足",
    constraint_violation: "違反限制",
    blocked: "已阻擋",
  }[value] || value;
}

function eventLabel(value) {
  return {
    TaskSubmitted: "任務已提交",
    TaskStarted: "任務執行中",
    TaskVerified: "任務已驗證",
    ExperienceRecorded: "經驗已記錄",
    KnowledgeRecorded: "知識已記錄",
    ReflectionRecorded: "反思已記錄",
    SkillUpdated: "技能已更新",
    CurriculumStarted: "研究課程已開始",
    UltimateChallengeCreated: "課題已建立",
    UltimateChallengePlanned: "課題已規劃",
    ChallengeDraftCreated: "課題草稿已保存",
    SocialPostObserved: "社群成效已觀察",
    UltimateChallengeSucceeded: "課題已成功",
    BenchmarkRecorded: "能力基準已記錄",
    ObjectiveEvaluationCompleted: "P3 客觀評估已完成",
    LearningAdmissionRevoked: "錯誤學習已撤銷",
    SupervisorReviewStarted: "監督者開始檢查",
    SupervisorReviewCompleted: "監督者完成評估",
    SupervisorReviewFailed: "監督者檢查失敗",
    SupervisorChallengeAssigned: "監督者已派發考驗",
    SupervisorSkillForged: "監督者已鍛造技能",
    SupervisorDuplicateSkipped: "監督者已略過重複考驗",
    RuntimeSkillForged: "新技能程式已建立",
    RuntimeSkillRepairForged: "技能修正版已建立",
    RuntimeSkillRepairFailed: "技能自動修復失敗",
    RuntimeSkillTested: "技能測試已完成",
    RuntimeSkillActivated: "技能已熱啟用",
    RuntimeVersionChanged: "核心版本已更新",
    ResourcePressureThrottled: "資源保護已暫緩背景工作",
    LearningThemeCreated: "長期學習主題已建立",
    LearningThemeUpdated: "長期學習主題已更新",
    LearningThemeDeleted: "長期學習主題已移除",
    DialogueImageGenerated: "本機圖片已生成",
    DialogueBackgroundRemoved: "圖片背景已移除",
    MissionCreated: "課題已建立",
    MissionUpdated: "課題已修改",
    MissionSubmittedForReview: "課題等待驗收",
    MissionApproved: "課題已通過",
    MissionRejected: "課題需改善",
    MissionCancelled: "課題已取消",
    MemoryConsolidated: "記憶已整理",
    KernelStarted: "核心已啟動",
    KernelRecovered: "核心已恢復",
  }[value] || value;
}

function concise(value, limit = 320) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

const pageTitles = {
  home: "對話與狀態",
  challenges: "課題挑戰",
  learning: "學習成果",
  "skill-tree": "技能樹",
  evaluation: "客觀評估",
  roadmap: "使命與路線圖",
  system: "系統資訊",
};

function showView() {
  const requested = window.location.hash.replace("#", "") || "home";
  const viewName = pageTitles[requested] ? requested : "home";
  $$('[data-view]').forEach((view) => {
    const active = view.dataset.view === viewName;
    view.hidden = !active;
    view.classList.toggle("active", active);
  });
  $$('[data-view-link]').forEach((link) => {
    link.classList.toggle("active", link.dataset.viewLink === viewName);
  });
  $("#page-title").textContent = pageTitles[viewName];
  if (viewName === "skill-tree") loadSkillTree();
  if (viewName === "system") loadSystemResources();
  window.scrollTo({ top: 0, behavior: "auto" });
}

function setConnection(online) {
  $("#connection-dot").className = online ? "online" : "offline";
  $("#connection-state").textContent = online ? "本機核心已連線" : "核心離線";
}

function setPresence(mood, activity, supervisor) {
  const faces = {
    focused: "•̀ᴗ•́",
    curious: "✦‿✦",
    working: "◉‿◉",
    reflecting: "˘⌣˘",
    waiting: "•‿•",
    blocked: "×﹏×",
  };
  const normalizedMood = faces[mood] ? mood : "waiting";
  $("#eck-presence").dataset.mood = normalizedMood;
  $("#eck-face").textContent = faces[normalizedMood];
  $("#dynamic-activity").textContent = concise(activity, 96).replace(/[.…]+$/, "");
  $("#supervisor-label").textContent = supervisor.reviewing
    ? "監督者正在評估近期學習"
    : supervisor.enabled
      ? supervisor.max_reviews_per_day > 0
        ? `每 ${Math.round(supervisor.review_interval_seconds / 60)} 分鐘 · 今日 ${supervisor.reviews_last_24h}/${supervisor.max_reviews_per_day}`
        : `每 ${Math.round(supervisor.review_interval_seconds / 60)} 分鐘 · 近 24 小時 ${supervisor.reviews_last_24h} · 無每日上限`
      : "監督者未啟用";
}

function missionExecutionFor(executor, missionId) {
  return (executor?.items || []).find((item) => item.mission?.mission_id === missionId);
}

function observationSummary(value) {
  if (!value || typeof value !== "object") return "尚未取得工具觀察。";
  if (value.detail) return String(value.detail);
  if (Array.isArray(value.issues) && value.issues.length) return value.issues.join("；");
  if (value.result_summary) return String(value.result_summary);
  if (value.preview_url) return `已產生可驗證預覽：${value.preview_url}`;
  return JSON.stringify(value);
}

function renderActivity(kernel, tasks, missions, supervisor, autonomousLearning, executor) {
  const activeTask = tasks.find((task) => ["queued", "running", "waiting_approval"].includes(task.status));
  const latestTask = tasks[0];
  const indicator = $("#activity-indicator");
  const state = $("#activity-state");

  if (activeTask) {
    const topic = activeTask.action.payload?.topic;
    const isResearch = ["academic.research", "web.critical_research"].includes(activeTask.action.capability);
    const activity = isResearch
      ? activeTask.status === "running"
        ? `正在學習「${topic}」，搜尋、質疑並交叉查證公開來源`
        : activeTask.status === "queued"
          ? `準備研究「${topic}」並建立可驗證問題`
          : `「${topic}」研究正在等待安全核准`
      : `${taskStatusLabel(activeTask.status)}：${activeTask.goal}`;
    $("#current-activity").textContent = isResearch
      ? `正在研究${topic ? `「${topic}」` : "與整理來源"}`
      : "正在執行任務";
    $("#activity-detail").textContent = activeTask.goal;
    state.textContent = taskStatusLabel(activeTask.status);
    state.className = "pill success";
    indicator.className = activeTask.status === "waiting_approval"
      ? "activity-indicator blocked"
      : "activity-indicator";
    setPresence(activeTask.status === "waiting_approval" ? "blocked" : "working", activity, supervisor);
    return;
  }

  const activeExecution = (executor?.items || []).find((item) =>
    ["active", "preparing"].includes(item.mission?.status)
    && item.steps?.some((step) => ["running", "pending"].includes(step.status))
  );
  if (activeExecution) {
    const running = activeExecution.steps.find((step) => step.status === "running");
    const next = running || activeExecution.steps.find((step) => step.status === "pending");
    const latestCycle = activeExecution.cycles?.[0];
    $("#current-activity").textContent = running ? "正在執行持久化軟體任務" : "準備下一個任務微步驟";
    $("#activity-detail").textContent = next?.objective || activeExecution.mission.progress?.current_step;
    state.textContent = running ? "執行中" : "可續跑";
    state.className = "pill success";
    indicator.className = "activity-indicator";
    setPresence(
      running ? "working" : "focused",
      latestCycle?.reason_summary || `正在處理 ${next?.step_key || "任務步驟"}`,
      supervisor,
    );
    return;
  }

  if (supervisor.reviewing) {
    $("#current-activity").textContent = "監督者正在設計新考驗";
    $("#activity-detail").textContent = "檢查近期已驗證經驗、技能與反思，找出下一個能力缺口。";
    state.textContent = "評估中";
    state.className = "pill success";
    indicator.className = "activity-indicator";
    setPresence("reflecting", supervisor.activity_text, supervisor);
    return;
  }

  if (latestTask?.action?.capability === "academic.research" && latestTask.result) {
    const topic = latestTask.action.payload?.topic || "最近課題";
    const succeeded = latestTask.status === "verified_success";
    setPresence(
      succeeded ? "focused" : "reflecting",
      succeeded
        ? `剛完成「${topic}」，正在整理驗證結果與新問題`
        : `正在檢查「${topic}」的證據缺口，準備下一輪改善`,
      supervisor,
    );
  } else {
    setPresence(
      "curious",
      autonomousLearning?.activity_text || "自主課程器正在準備下一個可驗證主題。",
      supervisor,
    );
  }

  $("#current-activity").textContent = kernel.phase === "running" ? "自主學習排程持續運作" : `核心狀態：${kernel.phase}`;
  $("#activity-detail").textContent = kernel.phase === "running"
    ? (autonomousLearning?.activity_text || "自主課程器正在準備下一個可驗證主題。")
    : "核心目前不接受新的執行工作。";
  state.textContent = kernel.phase === "running" ? "持續學習" : kernel.phase;
  state.className = "pill";
  indicator.className = "activity-indicator idle";
}

function renderThoughtFeed(tasks, missions, reflections, events, experiences, supervisor, executor) {
  const currentExecution = (executor?.items || []).find((item) =>
    ["active", "preparing", "blocked"].includes(item.mission?.status)
  );
  const missionCycle = currentExecution?.cycles?.[0] || executor?.latest_cycle;
  if (missionCycle && currentExecution) {
    const step = currentExecution.steps.find((item) => item.step_id === missionCycle.step_id);
    const items = [
      ["目標", currentExecution.mission.objective],
      ["思考摘要", missionCycle.reason_summary],
      ["行動", `${missionCycle.action?.tool || step?.action_kind || "工具"} · 第 ${missionCycle.attempt} 次`],
      ["觀察", observationSummary(missionCycle.observation)],
      ["修正", missionCycle.correction || (missionCycle.status === "succeeded" ? "驗證通過，前進下一個微任務。" : "等待工具結果。")],
    ];
    $("#thought-feed").innerHTML = items.map(([label, content]) => `
      <article class="thought-item"><span>${escapeHtml(label)}</span><p>${escapeHtml(concise(content))}</p></article>
    `).join("");
    return;
  }
  const activeTask = tasks.find((task) => ["queued", "running", "waiting_approval"].includes(task.status));
  const focusTask = activeTask || tasks[0];
  const items = [];
  if (focusTask) {
    const taskReflection = reflections.find((item) => item.task_id === focusTask.task_id);
    const taskExperience = experiences.find((item) => item.task_id === focusTask.task_id);
    const taskEvent = events.filter((item) =>
      item.event_type !== "Heartbeat"
      && (item.correlation_id === focusTask.task_id || item.aggregate_id === focusTask.task_id)
    ).at(-1);
    const output = focusTask.result?.output || {};
    const evidenceCount = focusTask.result?.evidence?.length || 0;
    items.push(["目標", focusTask.goal]);
    items.push(["動作", `${taskStatusLabel(focusTask.status)} · ${focusTask.action.capability}`]);
    if (["academic.research", "web.critical_research"].includes(focusTask.action.capability)) {
      const researchSummary = output.synthesis || output.conclusion || output.report?.conclusion;
      if (researchSummary) items.push(["研究", researchSummary]);
    }
    if (focusTask.result) {
      items.push(["證據", `${evidenceCount} 筆工具證據 · ${focusTask.verification?.reason || taskStatusLabel(focusTask.status)}`]);
    } else {
      items.push(["證據", "尚未產生驗證結果；任務仍在佇列或執行中。"]);
    }
    if (taskReflection?.lesson) {
      items.push(["反思", taskReflection.lesson]);
    } else if (taskExperience) {
      items.push(["結論", taskExperience.admitted ? "結果已通過學習准入。" : taskExperience.admission_reason]);
    }
    if (taskEvent) {
      items.push(["紀錄", `${eventLabel(taskEvent.event_type)} · ${formatTime(taskEvent.created_at, true)}`]);
    }
  }

  $("#thought-feed").innerHTML = items.slice(0, 5).map(([label, content]) => `
    <article class="thought-item"><span>${escapeHtml(label)}</span><p>${escapeHtml(concise(content))}</p></article>
  `).join("") || '<div class="empty">尚無可稽核的決策或行動摘要。</div>';
}

function renderSupervisor(supervisor) {
  const review = supervisor.latest_review;
  const compute = supervisor.num_gpu_layers === 0
    ? "CPU ONLY"
    : supervisor.num_gpu_layers == null
      ? "GPU AUTO"
      : `GPU ${supervisor.num_gpu_layers} 層`;
  $("#supervisor-model").textContent = supervisor.model
    ? `${supervisor.model} · ${compute} · ≤${supervisor.max_output_tokens} tokens`
    : "未設定";
  $("#supervisor-assessment").textContent = review?.assessment
    ? `最近一次派題前評估：${review.assessment}`
    : (supervisor.enabled ? "監督者會在 ECK 閒置時檢查近期學習。" : "監督者目前未啟用。");
  $("#supervisor-recommendations").innerHTML = (review?.recommendations || []).map((item) =>
    `<li>${escapeHtml(item)}</li>`
  ).join("");
  $("#supervisor-challenge").textContent = review?.challenge_topic
    ? `最新考驗：${review.challenge_topic}${review.task_id ? " · 已派入任務佇列" : ""}`
    : "下一輪會在閒置時自動提出考驗。";
}

function renderHomeSummary(health, experiences, skills, missions, reflections, runtime) {
  const activeSkills = skills.filter((item) => item.active);
  const activeRuntimeSkills = (runtime.skill_runtime.items || []).filter((item) => item.status === "active");
  const activeMissions = missions.filter((item) => !["approved", "cancelled"].includes(item.status));
  const reviewMission = missions.find((item) => item.status === "awaiting_review");
  const latestLearning = experiences.find((item) => item.admitted);
  $("#experience-count").textContent = formatCount(health.memory.admitted_experiences);
  $("#active-skill-count").textContent = formatCount(activeSkills.length + activeRuntimeSkills.length);
  $("#skill-total-label").textContent = `學習 ${formatCount(activeSkills.length)} · 熱技能 ${formatCount(activeRuntimeSkills.length)}`;
  $("#home-challenge-state").textContent = reviewMission ? "待驗收" : formatCount(activeMissions.length);
  $("#home-challenge-title").textContent = reviewMission?.title || (activeMissions.length ? "課題在背景排程" : "尚未建立");
  $("#last-learning-time").textContent = latestLearning ? formatTime(latestLearning.created_at) : "—";
  const learningProgress = health.learning_progress || {};
  $("#last-learning-label").textContent = learningProgress.stalled
    ? `停滯 · ${concise(learningProgress.detail || "沒有新的驗證學習", 32)}`
    : (latestLearning?.capability || "尚無紀錄");
  $("#last-learning-label").title = learningProgress.detail || "";

  $("#home-learning-results").innerHTML = experiences.filter((item) => item.admitted).slice(0, 4).map((item) => `
    <article class="compact-item">
      <div class="item-top"><b>${escapeHtml(item.capability)}</b><time>${formatTime(item.created_at)}</time></div>
      <p>${escapeHtml(item.summary)}</p>
    </article>
  `).join("") || '<div class="empty">尚無已准入的學習結果。</div>';

  const nextStep = reflections[0]?.next_step;
  $("#next-step-kind").textContent = reflections[0] ? "自主學習建議" : "等待中";
  $("#next-step-text").textContent = nextStep || "目前沒有可執行的下一步。";
}

function evidenceMarkup(evidence, className = "tag") {
  const value = String(evidence || "");
  if (value.startsWith("/v1/missions/") || value.startsWith("https://github.com/")) {
    return `<a class="${className}" href="${escapeHtml(value)}" target="_blank" rel="noopener">${escapeHtml(value)}</a>`;
  }
  return `<span class="${className}">${escapeHtml(value)}</span>`;
}

function missionStepMarkup(executor, missionId) {
  const execution = missionExecutionFor(executor, missionId);
  if (!execution?.steps?.length) return "";
  return `<div class="mission-step-list"><b>P6 微任務</b>${execution.steps.map((step) => `
    <div class="mission-step ${escapeHtml(step.status)}">
      <span>${escapeHtml(step.step_key)}</span>
      <small>${escapeHtml(step.status)} · ${step.attempts}/${step.max_attempts}</small>
    </div>
  `).join("")}</div>`;
}

function renderMissions(missions, executor) {
  const active = missions.filter((item) => ["active", "preparing", "blocked", "rejected"].includes(item.status));
  const review = missions.filter((item) => item.status === "awaiting_review");
  const completed = missions.filter((item) => item.status === "approved");
  $("#active-mission-count").textContent = formatCount(active.length);
  $("#review-mission-count").textContent = formatCount(review.length);
  $("#completed-mission-count").textContent = formatCount(completed.length);

  const activeMissionHtml = active.map((mission) => `
    <details class="challenge-card mission-card" data-mission-card-id="${escapeHtml(mission.mission_id)}">
      <summary><span><b>${escapeHtml(mission.title)}</b><small>${escapeHtml(mission.source === "human" ? "你建立" : "監督者建立")} · ${escapeHtml(mission.schedule === "monthly" ? "每月課題" : "一般課題")}</small></span><span class="pill ${mission.status === "blocked" ? "danger" : ""}">${escapeHtml(missionStatusLabel(mission.status))}</span></summary>
      <div class="mission-body">
        <p class="objective">${escapeHtml(mission.objective)}</p>
        <div class="mission-requirements"><b>完成要求</b><p>${escapeHtml(mission.completion_requirements)}</p></div>
        <div class="challenge-next"><b>目前進度：</b> ${escapeHtml(mission.progress?.current_step || "等待規劃")}</div>
        ${missionStepMarkup(executor, mission.mission_id)}
        ${mission.review_feedback ? `<div class="mission-feedback"><b>上次驗收意見</b><p>${escapeHtml(mission.review_feedback)}</p></div>` : ""}
        <form class="mission-edit-form" data-mission-id="${escapeHtml(mission.mission_id)}">
          <input name="title" value="${escapeHtml(mission.title)}" maxlength="240" required>
          <textarea name="objective" rows="3" maxlength="4000" required>${escapeHtml(mission.objective)}</textarea>
          <textarea name="completion_requirements" rows="4" maxlength="8000" required>${escapeHtml(mission.completion_requirements)}</textarea>
          <div class="mission-actions"><button type="submit" class="secondary">保存修改</button><button type="button" class="ghost" data-mission-action="cancel" data-mission-id="${escapeHtml(mission.mission_id)}">取消課題</button></div>
        </form>
        <form class="mission-completion-form" data-mission-id="${escapeHtml(mission.mission_id)}">
          <textarea name="result_summary" rows="3" maxlength="8000" placeholder="描述完成成果與目前可交付結果" required></textarea>
          <textarea name="evidence" rows="2" placeholder="每行一筆證據網址、檔案或驗證紀錄"></textarea>
          <button type="submit">提交成果等待驗收</button>
        </form>
      </div>
    </details>
  `).join("") || '<div class="empty">目前沒有進行中課題，自主學習仍會持續。</div>';
  const activeMissionEditorOpen = $("#active-missions details[open] .mission-edit-form");
  if (!activeMissionEditorOpen) {
    $("#active-missions").innerHTML = activeMissionHtml;
  }

  const reviewMissionHtml = review.map((mission) => `
    <article class="challenge-card mission-card review-card">
      <div class="challenge-card-head"><div><h3>${escapeHtml(mission.title)}</h3><p class="objective">${escapeHtml(mission.objective)}</p></div><span class="pill">等待驗收</span></div>
      <div class="mission-result"><b>成果</b><p>${escapeHtml(mission.result_summary)}</p></div>
      <div class="mission-evidence">${(mission.evidence || []).map((item) => evidenceMarkup(item)).join("") || '<span class="tag warn">未附證據</span>'}</div>
      ${missionStepMarkup(executor, mission.mission_id)}
      <form class="mission-review-form" data-mission-id="${escapeHtml(mission.mission_id)}">
        <textarea name="feedback" rows="3" maxlength="4000" placeholder="驗收草稿會自動保留；退回時請說明需要改善的內容">${escapeHtml(missionDraft("review", mission.mission_id, "feedback"))}</textarea>
        <div class="mission-actions"><button type="submit" data-decision="approve">勾選通過</button><button type="submit" data-decision="reject" class="secondary">退回改善</button></div>
      </form>
    </article>
  `).join("") || '<div class="empty">目前沒有等待驗收的成果。</div>';
  if (!document.activeElement?.closest(".mission-review-form")) {
    $("#review-missions").innerHTML = reviewMissionHtml;
  }

  $("#completed-missions").innerHTML = completed.map((mission) => `
    <details class="challenge-card mission-card completed-card">
      <summary><span><b>${escapeHtml(mission.title)}</b><small>完成於 ${formatTime(mission.approved_at, true)}</small></span><span class="pill success">已通過</span></summary>
      <div class="mission-body"><p class="objective">${escapeHtml(mission.objective)}</p><div class="mission-requirements"><b>完成要求</b><p>${escapeHtml(mission.completion_requirements)}</p></div><div class="mission-result"><b>成果</b><p>${escapeHtml(mission.result_summary)}</p></div><div class="mission-evidence">${(mission.evidence || []).map((item) => evidenceMarkup(item, "tag good")).join("")}</div>${mission.review_feedback ? `<div class="mission-feedback"><b>驗收紀錄</b><p>${escapeHtml(mission.review_feedback)}</p></div>` : ""}</div>
    </details>
  `).join("") || '<div class="empty">尚無已通過課題。</div>';
}

function renderExperiences(items) {
  const recent = items.slice(0, 12);
  $("#experience-label").textContent = `${formatCount(recent.length)} recent`;
  $("#experiences").innerHTML = recent.map((item) => `
    <article class="detail-item">
      <div class="item-top"><b>${escapeHtml(item.capability)}</b><time>${formatTime(item.created_at, true)}</time></div>
      <p>${escapeHtml(item.summary)}</p>
      <div class="item-meta">
        <span class="tag ${item.admitted ? "good" : "warn"}">${item.admitted ? "已准入" : "僅留存"}</span>
        <span class="tag">${escapeHtml(outcomeLabel(item.outcome))}</span>
      </div>
    </article>
  `).join("") || '<div class="empty">尚無經驗紀錄。</div>';
}

function renderSkills(items) {
  const active = items.filter((item) => item.active);
  $("#skills-label").textContent = `${formatCount(active.length)} active`;
  $("#skills").innerHTML = items.map((item) => `
    <article class="detail-item">
      <div class="item-top"><b>${escapeHtml(item.name)}</b><span class="tag ${item.active ? "good" : "warn"}">${item.active ? "ACTIVE" : "CANDIDATE"}</span></div>
      <p>${escapeHtml(item.capability)} · 成功 ${formatCount(item.success_count)} · 失敗 ${formatCount(item.failure_count)}</p>
    </article>
  `).join("") || '<div class="empty">尚無技能紀錄。</div>';
}

function renderRuntime(runtime) {
  const skills = runtime.skill_runtime.items || [];
  const active = skills.filter((item) => item.status === "active");
  $("#runtime-skills-count").textContent = `${formatCount(active.length)} active`;
  $("#runtime-skills").innerHTML = skills.map((item) => {
    const tone = item.status === "active" ? "good" : item.status === "failed" ? "warn" : "";
    const improvements = (item.improvements || []).slice(0, 3).join(" · ");
    return `
      <article class="detail-item">
        <div class="item-top"><b>${escapeHtml(item.manifest.name)} <small>v${escapeHtml(item.manifest.version)}</small></b><span class="tag ${tone}">${escapeHtml(item.status.toUpperCase())}</span></div>
        <p>${escapeHtml(item.manifest.description)}</p>
        <div class="item-meta"><span class="tag">${escapeHtml(item.manifest.category)}</span><span class="tag">${escapeHtml(item.source)}</span></div>
        ${improvements ? `<div class="skill-improvement">增強紀錄：${escapeHtml(improvements)}</div>` : ""}
      </article>
    `;
  }).join("") || '<div class="empty">尚無可熱切換技能。</div>';

  const worker = runtime.skill_runtime.worker || {};
  $("#worker-state").textContent = worker.available ? "DOCKER READY" : "DOCKER OFF";
  $("#worker-state").className = worker.available ? "pill success" : "pill danger";
  $("#runtime-version").textContent = `v${runtime.version.version}`;
  $("#runtime-reason").textContent = worker.available
    ? runtime.version.last_reason
    : `技能原始碼已就緒；${worker.detail || "請啟動 Docker Desktop"}`;
  $("#learning-share").textContent = `${runtime.scheduler.autonomous_learning_percent}%`;
  $("#challenge-share").textContent = `${runtime.scheduler.challenge_execution_percent}%`;
  $("#verified-skill-total").textContent = formatCount(runtime.version.verified_skill_count);
  $("#next-minor-skill").textContent = formatCount(runtime.version.next_minor_skill_count);
}

function renderResearch(tasks, experiences) {
  const researchTasks = tasks.filter((task) => task.action.capability === "academic.research");
  const experienceByTask = new Map(experiences.map((item) => [item.task_id, item]));
  const active = researchTasks.find((task) => ["queued", "running", "waiting_approval"].includes(task.status));
  $("#research-state").textContent = active ? taskStatusLabel(active.status) : researchTasks.length ? "課程已記錄" : "等待主題";
  $("#research-state").className = active ? "pill" : researchTasks.length ? "pill success" : "pill";
  $("#research-list").innerHTML = researchTasks.slice(0, 6).map((task) => {
    const output = task.result?.output || {};
    const metrics = output.metrics || {};
    const experience = experienceByTask.get(task.task_id);
    const revoked = Boolean(experience && !experience.admitted && task.status === "verified_success");
    const source = Array.isArray(output.sources) ? output.sources[0] : null;
    const sourceUrl = safeUrl(source?.url);
    return `
      <article class="detail-item">
        <div class="item-top"><b>${escapeHtml(output.topic || task.action.payload.topic || task.goal)}</b><span class="tag ${task.status === "verified_success" && !revoked ? "good" : "warn"}">${revoked ? "已撤銷" : escapeHtml(taskStatusLabel(task.status))}</span></div>
        <p>${escapeHtml(concise(output.synthesis || output.error || "等待研究結果。", 240))}</p>
        <div class="item-meta"><span class="tag">相關來源 ${formatCount(revoked ? 0 : (metrics.relevant_sources ?? metrics.sources_found))}</span><span class="tag">問題 ${formatCount(metrics.questions_generated)}</span></div>
        ${sourceUrl ? `<a class="research-source" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">${escapeHtml(source.title)}</a>` : ""}
      </article>
    `;
  }).join("") || '<div class="empty">尚未建立研究課程。</div>';
}

function renderLearningThemes(data) {
  const themes = data.items || [];
  const portfolio = data.portfolio || {};
  $("#learning-theme-ratio").textContent = `${formatCount(portfolio.self_development)}% 自我 · ${formatCount(portfolio.ai_research)}% AI · ${formatCount(portfolio.foundation)}% 基礎 · ${formatCount(portfolio.exploration)}% 探索`;
  $("#learning-theme-list").innerHTML = themes.map((item) => `
    <div class="learning-theme-item ${item.active ? "" : "paused"}">
      <b>${escapeHtml(item.title)}</b>
      <button type="button" class="ghost" data-theme-action="toggle" data-theme-id="${escapeHtml(item.theme_id)}" data-theme-active="${item.active ? "true" : "false"}">${item.active ? "暫停" : "啟用"}</button>
      <button type="button" class="ghost" data-theme-action="delete" data-theme-id="${escapeHtml(item.theme_id)}">移除</button>
    </div>
  `).join("") || '<div class="empty">尚未指定長期主題；ECK 仍會依核心成長與一般知識矩陣自主學習。</div>';
}

function renderBenchmarks(data) {
  const count = data.items.reduce((total, item) => total + Number(item.run_count || 0), 0);
  $("#benchmark-count").textContent = `${formatCount(count)} runs`;
  $("#benchmark-policy").textContent = data.claim_policy;
  $("#benchmarks").innerHTML = data.items.map((item) => {
    const latest = item.latest;
    const score = latest ? `${(Number(latest.score) * 100).toFixed(1)}%` : "未建立基線";
    return `
      <article class="benchmark-item">
        <div><b>${escapeHtml(item.name)}</b><p>${escapeHtml(item.measures)}</p></div>
        <span class="benchmark-score ${latest ? "" : "empty-score"}">${score}</span>
      </article>
    `;
  }).join("");
}

function renderObjectiveEvaluation(data) {
  const objective = data.objective || {};
  const comparison = objective.comparison || {};
  const latest = comparison.latest;
  const protocol = latest?.protocol || {};
  const audit = data.growth_audit || {};
  const score = latest ? `${(Number(latest.score) * 100).toFixed(1)}%` : "—";
  const reproducibility = Number.isFinite(Number(protocol.reproducibility_rate))
    ? `${(Number(protocol.reproducibility_rate) * 100).toFixed(1)}%`
    : "—";
  $("#objective-score").textContent = score;
  $("#objective-reproducibility").textContent = reproducibility;
  $("#growth-research-count").textContent = formatCount(audit.research_admissions);
  $("#growth-active-skill-count").textContent = formatCount(audit.activated_generated_skills);
  $("#objective-case-count").textContent = `${formatCount(objective.case_count)} cases`;
  $("#objective-claim-policy").textContent = data.claim_policy;

  const auditStates = {
    research_without_executable_skill_growth: ["研究未轉技能", "warning", "stalled"],
    verified_executable_skill_growth: ["技能已驗證成長", "success", "verified"],
    verified_learning_without_new_executable_skill: ["有學習、無新技能", "warning", "stalled"],
    no_verified_learning_activity: ["無驗證學習", "danger", "stalled"],
  };
  const [auditLabel, auditPill, auditPanel] = auditStates[audit.status] || ["資料不足", "", ""];
  $("#growth-audit-state").textContent = auditLabel;
  $("#growth-audit-state").className = `pill ${auditPill}`.trim();
  $("#growth-audit-panel").className = `panel growth-audit-panel ${auditPanel}`.trim();
  $("#growth-audit-message").textContent = audit.message || "尚未完成能力成長稽核。";
  const conversion = audit.research_to_active_skill_rate == null
    ? "—"
    : `${(Number(audit.research_to_active_skill_rate) * 100).toFixed(2)}%`;
  const growthMetrics = [
    ["24h 已准入經驗", audit.admitted_experiences],
    ["24h 新記憶技能", audit.new_memory_skills],
    ["24h 技能候選", audit.generated_skill_candidates],
    ["研究→啟用率", conversion],
    ["歷史活躍生成技能", audit.lifetime_active_generated_skills],
  ];
  $("#growth-metrics").innerHTML = growthMetrics.map(([label, value]) => `
    <div><span>${escapeHtml(label)}</span><b>${typeof value === "number" ? formatCount(value) : escapeHtml(value ?? "—")}</b></div>
  `).join("");

  const categoryLabels = {
    reasoning: "推理",
    evidence: "證據判斷",
    tool_routing: "工具路由",
    software_engineering: "軟體工程",
  };
  const categoryScores = protocol.category_scores || {};
  $("#evaluation-dimensions").innerHTML = Object.entries(categoryLabels).map(([key, label]) => {
    const value = categoryScores[key];
    return `<div class="evaluation-dimension"><span>${escapeHtml(label)}</span><b>${value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`}</b></div>`;
  }).join("");

  const comparisonStates = {
    no_baseline: ["尚無基線", "請先執行固定本機評估。"],
    baseline_created: ["基線已建立", "已有第一筆分數；至少再執行一次同條件評估才能比較。"],
    conditions_changed: ["條件不同", "題集、模型或模型雜湊不同，不宣稱增退。"],
    diagnostic_improved: ["固定診斷上升", "同題集、同模型雜湊的診斷分數上升；仍需保留真實任務確認。"],
    diagnostic_regressed: ["固定診斷退步", "同條件分數下降，應停止宣稱能力增強並調查回歸。"],
    diagnostic_unchanged: ["固定診斷持平", "同條件分數沒有變化。"],
  };
  const [comparisonLabel, comparisonMessage] = comparisonStates[comparison.status] || ["無法比較", "評測條件不足。"];
  $("#objective-comparison-state").textContent = comparisonLabel;
  $("#objective-comparison-state").className = comparison.status === "diagnostic_regressed"
    ? "pill danger"
    : comparison.status === "diagnostic_improved" ? "pill success" : "pill";
  $("#objective-comparison-message").textContent = comparisonMessage;
  $("#evaluation-history").innerHTML = (objective.history || []).map((item) => `
    <article class="evaluation-history-item">
      <div><b>${escapeHtml(item.model)}</b><small>${formatTime(item.created_at, true)} · ${formatCount(item.repetitions)} 輪 · ${Number(item.latency_seconds || 0).toFixed(1)} 秒</small></div>
      <span>${(Number(item.score) * 100).toFixed(1)}%</span>
    </article>
  `).join("") || '<div class="empty">尚未執行 P3 評估。</div>';
}

function renderRoadmap(data) {
  const verified = data.verified_now || {};
  const capabilities = verified.registered_capabilities || [];
  const targets = data.targets || [];
  const milestones = data.milestones || [];
  const stateLabels = {
    in_progress: "建設中",
    not_verified: "尚未驗證",
    aspirational: "長期願景",
    verified: "已驗證",
  };
  $("#roadmap-mission").textContent = data.mission || "尚未設定長期使命。";
  $("#roadmap-current-truth").textContent = data.current_truth || "尚未完成能力盤點。";
  $("#roadmap-capability-count").textContent = formatCount(capabilities.length);
  $("#roadmap-experience-count").textContent = formatCount(verified.verified_experiences);
  $("#roadmap-runtime-skill-count").textContent = formatCount(verified.active_runtime_skills);
  $("#roadmap-chain-state").textContent = verified.event_chain_valid ? "有效" : "異常";
  $("#roadmap-coder-state").textContent = verified.coder_ready
    ? verified.coder_model || "READY"
    : "尚未就緒";
  $("#roadmap-project-count").textContent = `${formatCount(verified.autonomous_projects)} · ${formatCount(verified.published_projects)} 已發布`;
  $("#roadmap-github-state").textContent = verified.github_ready ? "READY" : "等待登入";
  $("#roadmap-soul-state").textContent = verified.soul_integrity
    ? `有效 · r${formatCount(verified.soul_revision)}`
    : "異常";
  $("#roadmap-self-model-state").textContent = verified.repository_self_model
    ? "已建立"
    : "待建立";
  $("#roadmap-skill-conversion-state").textContent = verified.research_skill_conversion
    ? `${formatCount(verified.active_generated_skills)} 已啟用`
    : "尚未驗證";
  $("#roadmap-core-candidate-count").textContent = verified.live_core_mutation
    ? "警告：正式核心已變更"
    : `${formatCount(verified.core_candidate_count)} · 隔離`;
  $("#roadmap-target-count").textContent = `${formatCount(targets.length)} targets`;
  $("#roadmap-targets").innerHTML = targets.map((target, index) => `
    <article class="roadmap-item">
      <span class="roadmap-index">${String(index + 1).padStart(2, "0")}</span>
      <div><div class="roadmap-item-head"><b>${escapeHtml(target.title)}</b><span class="tag ${target.state === "verified" ? "good" : target.state === "not_verified" ? "warn" : ""}">${escapeHtml(stateLabels[target.state] || target.state)}</span></div><p>${escapeHtml(target.measure)}</p></div>
    </article>
  `).join("");
  $("#roadmap-claim-policy").textContent = data.claim_policy || "";
  $("#roadmap-milestone-count").textContent = `${formatCount(milestones.length)} milestones`;
  $("#roadmap-milestones").innerHTML = milestones.map((item) => `
    <article class="milestone-item ${escapeHtml(item.state || "not_verified")}">
      <span>${escapeHtml(item.version || "—")}</span>
      <div><b>${escapeHtml(item.title)}</b><p>${escapeHtml(item.evidence)}</p></div>
      <i>${escapeHtml(stateLabels[item.state] || item.state)}</i>
    </article>
  `).join("") || '<div class="empty">尚無工程里程碑。</div>';
}

function renderImageStack(health) {
  const generation = health.image_generation || {};
  const removal = health.image_background_removal || {};
  const installedModels = (generation.models || []).filter((item) => item.installed);
  $("#image-stack-state").textContent = generation.available ? "READY" : "NOT READY";
  $("#image-stack-state").className = generation.available ? "pill success" : "pill danger";
  $("#image-backend").textContent = String(generation.backend || "local").toUpperCase();
  $("#image-checkpoint").textContent = generation.model || "尚未選擇 checkpoint";
  $("#image-model-count").textContent = formatCount(installedModels.length);
  $("#image-adetailer").textContent = generation.extensions?.adetailer ? "已安裝" : "未安裝";
  $("#image-controlnet").textContent = generation.extensions?.controlnet ? "OpenPose READY" : "未就緒";
  $("#image-rembg").textContent = removal.available ? `${removal.model} READY` : "未就緒";
  $("#image-content-mode").textContent = generation.content_policy?.legal_adult_content
    ? "合法成人內容已開啟"
    : "已限制";
}

function renderVideoStack(health) {
  const generation = health.video_generation || {};
  const resources = generation.resources || {};
  const verification = generation.verification || {};
  const activity = generation.activity || {};
  const readyNow = resources.ready_now ?? resources.ready;
  const state = generation.available
    ? readyNow ? "READY" : "釋放資源後可執行"
    : generation.installed ? "硬體條件不足" : "NOT READY";
  $("#video-stack-state").textContent = state;
  $("#video-stack-state").className = generation.available
    ? readyNow ? "pill success" : "pill warning"
    : "pill danger";
  $("#video-backend").textContent = String(generation.backend || "local").toUpperCase();
  $("#video-model").textContent = generation.model || "未安裝";
  $("#video-resource-detail").textContent = resources.detail || "尚無資源判定。";
  $("#video-total-ram").textContent = resources.system_ram_gb == null
    ? "未知" : `${resources.system_ram_gb} GB`;
  $("#video-available-ram").textContent = resources.available_ram_gb == null
    ? "未知" : `${resources.available_ram_gb} GB`;
  $("#video-min-ram").textContent = resources.minimum_available_ram_gb == null
    ? "—" : `${resources.minimum_available_ram_gb} GB`;
  $("#video-profile").textContent = generation.quality?.offload === "sequential_cpu"
    ? "FP16 · 循序 CPU offload" : generation.quality?.teacache ? "TeaCache" : "標準";
  $("#video-activity").textContent = activity.busy ? activity.stage : "閒置";
  $("#video-verification").textContent = verification.verified
    ? `已通過 · ${verification.seconds || 1} 秒煙霧測試`
    : "尚未通過實機煙霧測試";
}

function renderSystemResources(data) {
  const project = data.project || {};
  const memory = data.host?.memory || {};
  const disk = data.host?.disk || {};
  const process = data.process || {};
  const pressure = data.pressure || {};
  const levelLabels = {
    normal: "NORMAL",
    moderate: "偏高",
    high: "HIGH",
    critical: "CRITICAL",
  };
  $("#resource-state").textContent = levelLabels[pressure.level] || "未知";
  $("#resource-state").className = pressure.level === "critical"
    ? "pill danger"
    : ["high", "moderate"].includes(pressure.level) ? "pill warning" : "pill success";
  $("#resource-pressure-detail").textContent = pressure.detail || "尚無資源壓力資料。";
  $("#project-size").textContent = formatBytes(project.logical_bytes, 2);
  $("#project-files").textContent = `${formatCount(project.file_count)} 檔案 · ${formatCount(project.scan_errors)} 個無法讀取路徑`;
  $("#process-memory").textContent = formatBytes(process.working_set_bytes);
  $("#system-memory").textContent = `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)}`;
  $("#system-memory-percent").textContent = `${Number(memory.used_percent || 0).toFixed(1)}% 使用中 · 可用 ${formatBytes(memory.available_bytes)}`;
  $("#commit-memory").textContent = memory.commit_used_bytes == null
    ? "—" : `${formatBytes(memory.commit_used_bytes)} / ${formatBytes(memory.commit_limit_bytes)}`;
  $("#disk-usage").textContent = `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)}`;
  $("#disk-free").textContent = `${Number(disk.used_percent || 0).toFixed(1)}% 容量 · 尚餘 ${formatBytes(disk.free_bytes)}`;
  $("#project-scan-age").textContent = project.scanned_at
    ? `${formatTime(project.scanned_at, true)} · ${project.cached ? "快取" : "新掃描"}` : "尚未掃描";
  const breakdown = project.workspace_breakdown || project.breakdown || [];
  $("#resource-breakdown").innerHTML = breakdown.slice(0, 10).map((item) => `
    <div class="resource-breakdown-item">
      <span>${escapeHtml(item.name)}</span>
      <b>${formatBytes(item.logical_bytes, 2)}</b>
      <small>${formatCount(item.file_count)} files</small>
    </div>
  `).join("") || '<div class="empty">沒有可讀取的專案檔案。</div>';
  $("#resource-note").textContent = `${data.notes?.project_size || ""} ${data.notes?.disk_active_time || ""}`.trim();
}

async function loadSystemResources(force = false) {
  if (systemResourcesInFlight) return;
  if (!force && Date.now() - systemResourcesLastLoaded < 15000) return;
  systemResourcesInFlight = true;
  const button = $("#refresh-resources");
  if (button) button.disabled = true;
  try {
    const data = await request(`/v1/system/resources${force ? "?refresh=true" : ""}`);
    renderSystemResources(data);
    renderVideoStack({ video_generation: data.workloads?.video_generation || {} });
    systemResourcesLastLoaded = Date.now();
  } catch (error) {
    $("#resource-pressure-detail").textContent = `資源資料讀取失敗：${error.message}`;
  } finally {
    systemResourcesInFlight = false;
    if (button) button.disabled = false;
  }
}

function renderEvents(items) {
  const recent = items.filter((item) => item.event_type !== "Heartbeat").slice(-12).reverse();
  $("#events").innerHTML = recent.map((item) => `
    <article class="event-item">
      <span class="event-seq">${item.sequence}</span>
      <div><b>${escapeHtml(eventLabel(item.event_type))}</b><span>${formatTime(item.created_at, true)}</span></div>
    </article>
  `).join("") || '<div class="empty">尚無有效事件。</div>';
}

function skillTreeStatusLabel(value) {
  return {
    acquired: "已習得",
    learning: "學習中",
    active: "已啟用",
    draft: "待測試",
    testing: "測試中",
    failed: "驗證失敗",
    supported: "資料支持",
    refuted: "資料反駁",
    inconclusive: "證據不足",
    unverified: "未驗證",
  }[value] || value;
}

function skillTreeSource(source) {
  const label = escapeHtml(source.title || source.reference || "未命名來源");
  const type = escapeHtml(source.source_type || "source");
  const url = safeUrl(source.url);
  const content = url
    ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${label}</a>`
    : `<span title="${escapeHtml(source.reference || "")}">${label}</span>`;
  return `<li><i>${type}</i>${content}</li>`;
}

function renderSkillTreeItem(item) {
  const sources = item.sources || [];
  const summary = item.description
    || item.conclusion
    || (item.improvements || []).join(" · ")
    || (item.operations || []).join(" · ")
    || (item.procedure ? JSON.stringify(item.procedure) : "已保存可重用程序與驗證紀錄。");
  const path = (item.path || []).map(escapeHtml).join(" → ");
  const sourceList = sources.length
    ? `<details class="skill-sources"><summary>${formatCount(sources.length)} 個驗證來源</summary><ul>${sources.slice(0, 12).map(skillTreeSource).join("")}</ul>${sources.length > 12 ? `<small>另有 ${formatCount(sources.length - 12)} 個來源保存在可攜記憶。</small>` : ""}</details>`
    : '<div class="skill-source-empty">尚無外部網址；保留內部測試與事件證據。</div>';
  const classes = ["skill-node", item.gold ? "gold" : "", `node-${item.type || "item"}`].filter(Boolean).join(" ");
  return `
    <article class="${classes}">
      <div class="skill-node-head">
        <span>${escapeHtml(item.type === "knowledge" ? "知識" : "技能")}</span>
        <b>${escapeHtml(skillTreeStatusLabel(item.status))}</b>
      </div>
      <h3>${escapeHtml(item.title)}</h3>
      <small>${path}</small>
      <p>${escapeHtml(concise(summary, 260))}</p>
      <div class="skill-node-meta">
        ${item.success_count !== undefined ? `<span>成功 ${formatCount(item.success_count)}</span>` : ""}
        ${item.failure_count !== undefined ? `<span>失敗 ${formatCount(item.failure_count)}</span>` : ""}
        ${item.version ? `<span>v${escapeHtml(item.version)}</span>` : ""}
      </div>
      ${sourceList}
    </article>
  `;
}

function skillTreeDescendantCount(node) {
  return (node.children || []).reduce(
    (total, child) => total + (child.type === "category" ? skillTreeDescendantCount(child) : 1),
    0,
  );
}

function renderSkillTreeBranch(node, depth = 0) {
  if (node.type !== "category") return renderSkillTreeItem(node);
  const children = node.children || [];
  return `
    <details class="skill-branch depth-${depth}" open>
      <summary><span>${escapeHtml(node.title)}</span><b>${formatCount(skillTreeDescendantCount(node))}</b></summary>
      <div class="skill-branch-content">${children.map((child) => renderSkillTreeBranch(child, depth + 1)).join("")}</div>
    </details>
  `;
}

function renderSkillTree(data) {
  const stats = data.stats || {};
  $("#tree-acquired-count").textContent = formatCount(stats.acquired_skills);
  $("#tree-learning-count").textContent = formatCount(stats.learning_skills);
  $("#tree-research-count").textContent = formatCount(stats.research_results);
  $("#tree-source-count").textContent = formatCount(stats.traceable_sources);
  $("#skill-tree-heading").textContent = "完整技能關聯";
  $("#skill-tree-portability").textContent = data.portable
    ? "技能圖由可攜 SQLite 記憶即時重建；移機後可重新索引並直接檢索已驗證程序。"
    : "技能圖目前不是可攜狀態。";
  const branches = data.tree?.children || [];
  $("#skill-tree-grid").innerHTML = branches.map((node) => renderSkillTreeBranch(node)).join("")
    || '<div class="empty">尚無技能或研究資料。</div>';
}

async function loadSkillTree(force = false, revision = "") {
  if (!force && revision && revision === skillTreeRevision) return;
  if (!force && !revision && Date.now() - skillTreeLastLoaded < 300000) return;
  try {
    const data = await request("/v1/learning/skill-tree");
    renderSkillTree(data);
    skillTreeLastLoaded = Date.now();
    skillTreeRevision = revision || JSON.stringify(data.stats || {});
  } catch (error) {
    $("#skill-tree-grid").innerHTML = `<div class="empty">技能圖讀取失敗：${escapeHtml(error.message)}</div>`;
  }
}

async function searchSkillTree(query) {
  if (!query) {
    skillTreeLastLoaded = 0;
    skillTreeRevision = "";
    await loadSkillTree(true);
    return;
  }
  const result = await request(`/v1/learning/skill-tree/search?q=${encodeURIComponent(query)}&limit=30`);
  $("#skill-tree-heading").textContent = `「${query}」相關記憶`;
  $("#skill-tree-grid").innerHTML = result.items.map(renderSkillTreeItem).join("")
    || '<div class="empty">沒有找到相關技能或研究記憶。</div>';
}

function updateSafety(health) {
  const chainValid = health.event_chain.valid;
  $("#chain-state").textContent = chainValid ? "事件鏈有效" : "事件鏈異常";
  $("#chain-state").className = chainValid ? "pill success" : "pill danger";
  $("#network-state").textContent = health.safety.network_enabled ? "受限來源" : "已停用";
  $("#system-state").textContent = health.safety.system_file_mutation_enabled ? "已開啟" : "未啟用";
}

async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const [health, events, tasks, experiences, reflections, skills, capabilities, missions, missionExecutor, evaluations, supervisor, runtime, roadmap, themes] = await Promise.all([
      request("/health"),
      request("/v1/events?latest=true&limit=50"),
      request("/v1/tasks?limit=50"),
      request("/v1/experiences?limit=20"),
      request("/v1/reflections?limit=12"),
      request("/v1/skills?limit=100"),
      request("/v1/capabilities"),
      request("/v1/missions?limit=100"),
      request("/v1/missions/executor/status"),
      request("/v1/evaluations"),
      request("/v1/supervisor/status"),
      request("/v1/runtime/status"),
      request("/v1/roadmap"),
      request("/v1/learning/themes"),
    ]);

    const { kernel, brain, memory } = health;
    setConnection(true);
    $("#version").textContent = health.version;
    $("#phase").textContent = kernel.phase.toUpperCase();
    $("#sidebar-phase").textContent = kernel.phase.toUpperCase();
    $("#sidebar-model").textContent = brain.model || "未設定";
    kernelStartedAt = kernel.started_at;
    updateUptime();

    $("#memory-experiences").textContent = formatCount(memory.experiences);
    $("#memory-admitted").textContent = formatCount(memory.admitted_experiences);
    $("#memory-knowledge").textContent = formatCount(memory.knowledge);
    $("#memory-reflections").textContent = formatCount(memory.reflections);
    $("#model-name").textContent = brain.model || "尚未設定模型";
    $("#brain-provider").textContent = brain.provider;
    $("#brain-detail").textContent = brain.detail || "Provider 已回應。";
    $("#brain-state").textContent = brain.available ? "READY" : "UNAVAILABLE";
    $("#brain-state").className = brain.available ? "pill success" : "pill danger";
    $("#kernel-identity").textContent = kernel.identity;
    $("#boot-count").textContent = formatCount(kernel.boot_count);
    $("#heartbeat-time").textContent = formatTime(kernel.last_heartbeat_at, true);
    $("#event-count").textContent = `${formatCount(kernel.event_count)} events`;

    renderActivity(kernel, tasks.items, missions.items, supervisor, health.autonomous_learning, missionExecutor);
    renderThoughtFeed(tasks.items, missions.items, reflections.items, events.items, experiences.items, supervisor, missionExecutor);
    renderSupervisor(supervisor);
    renderHomeSummary(health, experiences.items, skills.items, missions.items, reflections.items, runtime);
    renderMissions(missions.items, missionExecutor);
    renderExperiences(experiences.items);
    renderSkills(skills.items);
    renderRuntime(runtime);
    renderResearch(tasks.items, experiences.items);
    renderLearningThemes(themes);
    renderBenchmarks(evaluations);
    renderObjectiveEvaluation(evaluations);
    renderRoadmap(roadmap);
    renderImageStack(health);
    renderVideoStack(health);
    renderEvents(events.items);
    updateSafety(health);
    $("#capability-tags").innerHTML = capabilities.items.map((item) =>
      `<span class="tag good" title="${escapeHtml(item.description)}">${escapeHtml(item.name)}</span>`
    ).join("");
    if ((window.location.hash || "#home") === "#skill-tree") {
      const runtimeSkills = runtime.skill_runtime || {};
      const revision = [
        memory.admitted_experiences,
        memory.knowledge,
        memory.skills,
        runtimeSkills.active,
        runtimeSkills.testing,
        runtimeSkills.draft,
        runtimeSkills.failed,
      ].join(":");
      loadSkillTree(false, revision);
    }
    if ((window.location.hash || "#home") === "#system") {
      loadSystemResources();
    }
  } catch (error) {
    setConnection(false);
    $("#phase").textContent = "OFFLINE";
    console.error(error);
  } finally {
    refreshInFlight = false;
  }
}

function loadChatHistory() {
  try {
    const parsed = JSON.parse(localStorage.getItem(CHAT_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.slice(-20) : [];
  } catch {
    return [];
  }
}

let chatHistory = loadChatHistory();

function saveChatHistory() {
  localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(chatHistory.slice(-20)));
}

function renderChat() {
  const messages = chatHistory.length
    ? chatHistory
    : [{ role: "assistant", content: "我已連接本機核心。你可以直接提出問題、交付任務，或詢問我目前正在學什麼。" }];
  $("#chat-messages").innerHTML = messages.map((item) => {
    const artifacts = Array.isArray(item.artifacts) ? item.artifacts : [];
    const artifactMarkup = artifacts.map((artifact) => {
      const artifactUrl = String(artifact.url || "");
      if (!artifactUrl.startsWith("/artifacts/") && !artifactUrl.startsWith("/video-artifacts/")) return "";
      const metadata = artifact.metadata || {};
      const details = [
        metadata.model,
        metadata.width && metadata.height ? `${metadata.width}×${metadata.height}` : "",
        metadata.seed !== undefined ? `seed ${metadata.seed}` : "",
        metadata.total_elapsed_seconds !== undefined
          ? `${metadata.total_elapsed_seconds}s total`
          : (metadata.elapsed_seconds !== undefined ? `${metadata.elapsed_seconds}s` : ""),
        metadata.peak_vram_mb !== undefined ? `${metadata.peak_vram_mb} MB VRAM` : "",
      ].filter(Boolean).join(" · ");
      if (artifact.type === "video") {
        return `
        <figure class="chat-artifact">
          <video class="chat-artifact-video" controls playsinline preload="metadata">
            <source src="${escapeHtml(artifactUrl)}" type="video/mp4">
          </video>
          <a class="artifact-download" href="${escapeHtml(artifactUrl)}" download>下載 MP4</a>
          <figcaption>${escapeHtml(details)}</figcaption>
        </figure>
        `;
      }
      if (artifact.type !== "image") return "";
      return `
        <figure class="chat-artifact">
          <a href="${escapeHtml(artifactUrl)}" target="_blank" rel="noopener">
            <img class="chat-artifact-image" src="${escapeHtml(artifactUrl)}" alt="ECK 生成圖片" loading="lazy"${metadata.width ? ` width="${escapeHtml(metadata.width)}"` : ""}${metadata.height ? ` height="${escapeHtml(metadata.height)}"` : ""}>
          </a>
          <figcaption>${escapeHtml(details)}</figcaption>
        </figure>
      `;
    }).join("");
    return `
    <div class="chat-message ${escapeHtml(item.role)}">
      <span class="chat-role">${item.role === "user" ? "YOU" : "ECK"}</span>
      <div class="chat-content">${escapeHtml(item.content)}${item.pending ? " · 生成中" : ""}</div>
      ${artifactMarkup}
    </div>
  `;
  }).join("");
  $("#chat-messages").scrollTop = $("#chat-messages").scrollHeight;
}

const watchedTasks = new Set();

function artifactFromTask(task) {
  const output = task?.result?.output || {};
  if (!output.artifact_url) return null;
  return {
    type: String(output.artifact || "").toLowerCase().endsWith(".mp4") ? "video" : "image",
    url: output.artifact_url,
    path: output.artifact_path,
    name: output.artifact,
    metadata: output.metadata || {},
  };
}

function taskFailureText(task) {
  const output = task?.result?.output || {};
  const metadata = output.metadata || {};
  const failedChecks = task?.verification?.failed_checks || [];
  if (failedChecks.some((name) => /width|height|尺寸/i.test(name))) {
    const expected = Object.fromEntries(
      (task?.success_contract?.checks || [])
        .filter((check) => /width|height/i.test(check.name))
        .map((check) => [check.name.toLowerCase().includes("width") ? "width" : "height", check.expected])
    );
    return `尺寸驗證失敗：要求 ${expected.width || "?"}×${expected.height || "?"}，實際 ${metadata.width || "?"}×${metadata.height || "?"}。`;
  }
  if (output.error || output.detail) return output.error || output.detail;
  if (failedChecks.length) return `驗證未通過：${failedChecks.join("、")}`;
  return task?.verification?.reason || task?.last_error || task?.status || "未知錯誤";
}

async function watchPendingTask(taskId) {
  if (!taskId || watchedTasks.has(taskId)) return;
  watchedTasks.add(taskId);
  const terminal = new Set(["verified_success", "verified_failure", "unverifiable", "constraint_violation", "blocked"]);
  try {
    for (let attempt = 0; attempt < 360; attempt += 1) {
      const task = await request(`/v1/tasks/${encodeURIComponent(taskId)}`);
      if (!terminal.has(task.status)) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        continue;
      }
      const message = chatHistory.find((item) => item.task_id === taskId);
      if (!message) return;
      message.pending = false;
      if (task.status === "verified_success") {
        const artifact = artifactFromTask(task);
        message.content = artifact?.type === "video" ? "本機影片已完成並通過檔案驗證。" : "本機圖片已完成並通過檔案驗證。";
        message.artifacts = artifact ? [artifact] : [];
      } else {
        message.content = `生成失敗：${taskFailureText(task)}`;
      }
      saveChatHistory();
      renderChat();
      return;
    }
    const message = chatHistory.find((item) => item.task_id === taskId);
    if (message) {
      message.pending = false;
      message.content = "本機生成超過 30 分鐘，請查看任務時間線的錯誤證據。";
      saveChatHistory();
      renderChat();
    }
  } catch (error) {
    console.error(error);
  } finally {
    watchedTasks.delete(taskId);
  }
}

async function loadSlashCommands() {
  try {
    const data = await request("/v1/chat/commands");
    slashCommands = Array.isArray(data.items) ? data.items : [];
  } catch (error) {
    console.error(error);
  }
}

function closeSlashMenu() {
  slashMatches = [];
  slashSelection = 0;
  $("#slash-command-menu").hidden = true;
  $("#chat-input").setAttribute("aria-expanded", "false");
}

function renderSlashMenu() {
  const menu = $("#slash-command-menu");
  menu.innerHTML = slashMatches.map((item, index) => `
    <button type="button" class="slash-command-item ${index === slashSelection ? "selected" : ""}" role="option" aria-selected="${index === slashSelection}" data-slash-index="${index}">
      <code>${escapeHtml(item.command)}</code>
      <span class="slash-command-copy"><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.description)}</small></span>
      <small>${escapeHtml(item.category)}</small>
    </button>
  `).join("");
  menu.hidden = slashMatches.length === 0;
  $("#chat-input").setAttribute("aria-expanded", slashMatches.length ? "true" : "false");
  menu.querySelector(".selected")?.scrollIntoView({ block: "nearest" });
}

function updateSlashMenu() {
  const value = $("#chat-input").value;
  if (!value.startsWith("/") || value.includes("\n")) {
    closeSlashMenu();
    return;
  }
  const query = value.toLowerCase();
  slashMatches = slashCommands.filter((item) => {
    const command = String(item.command || "").toLowerCase();
    const insert = String(item.insert || "").toLowerCase();
    return command.startsWith(query) || insert.startsWith(query);
  }).slice(0, 10);
  slashSelection = Math.min(slashSelection, Math.max(0, slashMatches.length - 1));
  renderSlashMenu();
}

function chooseSlashCommand(index) {
  const item = slashMatches[index];
  if (!item) return;
  const input = $("#chat-input");
  input.value = item.insert || item.command;
  closeSlashMenu();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  if (!item.requires_prompt) $("#chat-form").requestSubmit();
}

$("#chat-input").addEventListener("input", updateSlashMenu);
$("#chat-input").addEventListener("keydown", (event) => {
  if (!slashMatches.length || $("#slash-command-menu").hidden) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const direction = event.key === "ArrowDown" ? 1 : -1;
    slashSelection = (slashSelection + direction + slashMatches.length) % slashMatches.length;
    renderSlashMenu();
  } else if (event.key === "Enter" || event.key === "Tab") {
    event.preventDefault();
    chooseSlashCommand(slashSelection);
  } else if (event.key === "Escape") {
    event.preventDefault();
    closeSlashMenu();
  }
});

$("#slash-command-menu").addEventListener("mousedown", (event) => {
  event.preventDefault();
  const target = event.target instanceof Element ? event.target : null;
  const button = target?.closest("[data-slash-index]");
  if (button) chooseSlashCommand(Number(button.dataset.slashIndex));
});

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target?.closest(".chat-composer")) closeSlashMenu();
});

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;
  closeSlashMenu();
  const priorHistory = chatHistory.slice(-10);
  chatHistory.push({ role: "user", content: message });
  saveChatHistory();
  renderChat();
  input.value = "";
  button.disabled = true;
  button.textContent = "處理中…";
  $("#chat-context").textContent = /(移除|去除).{0,8}(背景|背影)|remove.{0,8}background/i.test(message)
    ? "正在使用本機 rembg 移除背景…"
    : /^\s*\/video\b|影片|視頻|動畫|短片|video|movie|animation|clip/i.test(message)
    ? "正在規劃提示並排入本機 CogVideoX 影片生成…"
    : /^\s*\/image\b|圖片|圖像|照片|插畫|image|picture|photo/i.test(message)
    ? "正在規劃模型並執行本機 Forge 圖像生成…"
    : /網站|網頁|website|軟體|程式|專案|app|api/i.test(message)
    ? "正在建立可續跑的 P6 軟體任務…"
    : "正在使用本機思考模型…";
  try {
    const result = await request("/v1/chat", {
      method: "POST",
      body: JSON.stringify({ message, history: priorHistory }),
    });
    chatHistory.push({
      role: "assistant",
      content: result.answer,
      artifacts: Array.isArray(result.artifacts) ? result.artifacts : [],
      task_id: result.task_id || null,
      pending: Boolean(result.pending),
    });
    saveChatHistory();
    renderChat();
    if (result.pending && result.task_id) watchPendingTask(result.task_id);
    const tool = result.tool ? `${result.tool} · ` : "";
    $("#chat-context").textContent = `${tool}${result.context.verified_experiences} 經驗 · ${result.context.active_skills} 技能 · ${result.context.research_results} 研究`;
  } catch (error) {
    chatHistory.push({ role: "assistant", content: `對話失敗：${error.message}` });
    renderChat();
  } finally {
    button.disabled = false;
    button.textContent = "送出";
  }
});

$("#clear-chat").addEventListener("click", () => {
  chatHistory = [];
  localStorage.removeItem(CHAT_STORAGE_KEY);
  renderChat();
});

$("#mission-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const title = $("#mission-title").value.trim();
  const objective = $("#mission-objective").value.trim();
  const completionRequirements = $("#mission-requirements").value.trim();
  if (!title || !objective || !completionRequirements) return;
  button.disabled = true;
  try {
    await request("/v1/missions", {
      method: "POST",
      body: JSON.stringify({
        title,
        objective,
        completion_requirements: completionRequirements,
        source: "human",
        schedule: $("#mission-schedule").value,
        priority: $("#mission-priority").value,
        target_month: $("#mission-month").value || null,
      }),
    });
    event.currentTarget.reset();
    toast("課題已建立，將以背景資源排程");
    await refresh();
  } catch (error) {
    toast(`課題建立失敗：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

document.addEventListener("input", (event) => {
  const form = event.target.closest?.(
    ".mission-review-form, .mission-edit-form, .mission-completion-form",
  );
  if (form instanceof HTMLFormElement) saveMissionDraft(form);
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (form.matches(".mission-edit-form")) {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await request(`/v1/missions/${form.dataset.missionId}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: data.get("title"),
          objective: data.get("objective"),
          completion_requirements: data.get("completion_requirements"),
        }),
      });
      clearMissionDraft(form);
      toast("課題內容已更新");
      form.closest("details")?.removeAttribute("open");
      await refresh();
    } catch (error) {
      toast(`課題修改失敗：${error.message}`);
    }
  }
  if (form.matches(".mission-completion-form")) {
    event.preventDefault();
    const data = new FormData(form);
    const evidence = String(data.get("evidence") || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    try {
      await request(`/v1/missions/${form.dataset.missionId}/completion`, {
        method: "POST",
        body: JSON.stringify({ result_summary: data.get("result_summary"), evidence }),
      });
      clearMissionDraft(form);
      toast("成果已送交，等待你勾選驗收");
      form.closest("details")?.removeAttribute("open");
      await refresh();
    } catch (error) {
      toast(`成果提交失敗：${error.message}`);
    }
  }
  if (form.matches(".mission-review-form")) {
    event.preventDefault();
    const submitter = event.submitter;
    const data = new FormData(form);
    try {
      await request(`/v1/missions/${form.dataset.missionId}/review`, {
        method: "POST",
        body: JSON.stringify({
          approved: submitter?.dataset.decision === "approve",
          feedback: data.get("feedback") || "",
        }),
      });
      clearMissionDraft(form);
      toast(submitter?.dataset.decision === "approve" ? "課題已通過並永久保存" : "課題已退回改善");
      await refresh();
    } catch (error) {
      toast(`驗收失敗：${error.message}`);
    }
  }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-mission-action]");
  if (!button) return;
  if (button.dataset.missionAction === "cancel") {
    button.disabled = true;
    try {
      await request(`/v1/missions/${button.dataset.missionId}`, { method: "DELETE" });
      toast("課題已取消，不再佔用排程");
      button.closest("details")?.removeAttribute("open");
      await refresh();
    } catch (error) {
      toast(`取消失敗：${error.message}`);
    } finally {
      button.disabled = false;
    }
  }
});

$("#validate-skills").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  try {
    const result = await request("/v1/runtime/skills/validate", { method: "POST" });
    toast(`已檢查 ${result.items.length} 個技能；Docker 未啟動時會保留等待`);
    await refresh();
  } catch (error) {
    toast(`技能檢查失敗：${error.message}`);
  } finally {
    event.currentTarget.disabled = false;
  }
});

$("#run-objective-evaluation").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "評估執行中…";
  try {
    const result = await request("/v1/evaluations/objective", {
      method: "POST",
      body: JSON.stringify({ repetitions: 2 }),
    });
    const score = Number(result.run?.score || 0) * 100;
    toast(`P3 固定診斷完成：${score.toFixed(1)}%`);
    await refresh();
  } catch (error) {
    toast(`P3 評估失敗：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "執行 2 輪本機評估";
  }
});

$("#research-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const topic = $("#research-topic").value.trim();
  if (!topic) return;
  button.disabled = true;
  $("#research-state").textContent = "建立中";
  try {
    const result = await request("/v1/research/curricula", {
      method: "POST",
      body: JSON.stringify({ topic, cycles: 2 }),
    });
    $("#research-topic").value = "";
    toast(`已排入 ${result.cycles} 輪研究`);
    await refresh();
  } catch (error) {
    toast(`研究建立失敗：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

$("#learning-theme-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const title = $("#learning-theme-title").value.trim();
  if (!title) return;
  button.disabled = true;
  try {
    await request("/v1/learning/themes", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
    $("#learning-theme-title").value = "";
    toast(`已加入長期學習主題「${title}」`);
    await refresh();
  } catch (error) {
    toast(`主題建立失敗：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

$("#learning-theme-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-theme-action]");
  if (!button) return;
  button.disabled = true;
  try {
    if (button.dataset.themeAction === "delete") {
      await request(`/v1/learning/themes/${button.dataset.themeId}`, { method: "DELETE" });
      toast("長期學習主題已移除");
    } else {
      const active = button.dataset.themeActive !== "true";
      await request(`/v1/learning/themes/${button.dataset.themeId}`, {
        method: "PATCH",
        body: JSON.stringify({ active }),
      });
      toast(active ? "主題已重新啟用" : "主題已暫停");
    }
    await refresh();
  } catch (error) {
    toast(`主題更新失敗：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

$("#skill-tree-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    await searchSkillTree($("#skill-tree-query").value.trim());
  } catch (error) {
    toast(`技能記憶搜尋失敗：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

$("#refresh-resources").addEventListener("click", () => loadSystemResources(true));

$$('[data-action]').forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await request(`/v1/kernel/${button.dataset.action}`, { method: "POST" });
      await refresh();
    } catch (error) {
      toast(`操作失敗：${error.message}`);
    } finally {
      button.disabled = false;
    }
  });
});

window.addEventListener("hashchange", showView);
renderChat();
loadSlashCommands();
chatHistory.filter((item) => item.pending && item.task_id).forEach((item) => watchPendingTask(item.task_id));
showView();
refresh();
window.setInterval(refresh, 5000);
window.setInterval(updateUptime, 1000);
