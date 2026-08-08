const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const CHAT_STORAGE_KEY = "eck-chat-history-v2";
let kernelStartedAt = null;

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

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`${response.status}: ${await response.text()}`);
  }
  return response.json();
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
    LearningAdmissionRevoked: "錯誤學習已撤銷",
    SupervisorReviewStarted: "監督者開始檢查",
    SupervisorReviewCompleted: "監督者完成評估",
    SupervisorReviewFailed: "監督者檢查失敗",
    SupervisorChallengeAssigned: "監督者已派發考驗",
    SupervisorSkillForged: "監督者已鍛造技能",
    SupervisorDuplicateSkipped: "監督者已略過重複考驗",
    RuntimeSkillForged: "新技能程式已建立",
    RuntimeSkillTested: "技能測試已完成",
    RuntimeSkillActivated: "技能已熱啟用",
    RuntimeVersionChanged: "核心版本已更新",
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
      ? `每 ${Math.round(supervisor.review_interval_seconds / 60)} 分鐘 · 今日 ${supervisor.reviews_last_24h}/${supervisor.max_reviews_per_day}`
      : "監督者未啟用";
}

function renderActivity(kernel, tasks, missions, supervisor, autonomousLearning) {
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
    setPresence(supervisor.mood, supervisor.activity_text, supervisor);
  }

  $("#current-activity").textContent = kernel.phase === "running" ? "整理學習結果" : `核心狀態：${kernel.phase}`;
  $("#activity-detail").textContent = kernel.phase === "running"
    ? (autonomousLearning?.activity_text || "自主課程器正在準備下一個可驗證主題。")
    : "核心目前不接受新的執行工作。";
  state.textContent = kernel.phase === "running" ? "學習間隔" : kernel.phase;
  state.className = "pill";
  indicator.className = "activity-indicator idle";
}

function renderThoughtFeed(tasks, missions, reflections, events, experiences, supervisor) {
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

function renderMissions(missions) {
  const active = missions.filter((item) => ["active", "preparing", "blocked", "rejected"].includes(item.status));
  const review = missions.filter((item) => item.status === "awaiting_review");
  const completed = missions.filter((item) => item.status === "approved");
  $("#active-mission-count").textContent = formatCount(active.length);
  $("#review-mission-count").textContent = formatCount(review.length);
  $("#completed-mission-count").textContent = formatCount(completed.length);

  $("#active-missions").innerHTML = active.map((mission) => `
    <details class="challenge-card mission-card">
      <summary><span><b>${escapeHtml(mission.title)}</b><small>${escapeHtml(mission.source === "human" ? "你建立" : "監督者建立")} · ${escapeHtml(mission.schedule === "monthly" ? "每月課題" : "一般課題")}</small></span><span class="pill ${mission.status === "blocked" ? "danger" : ""}">${escapeHtml(missionStatusLabel(mission.status))}</span></summary>
      <div class="mission-body">
        <p class="objective">${escapeHtml(mission.objective)}</p>
        <div class="mission-requirements"><b>完成要求</b><p>${escapeHtml(mission.completion_requirements)}</p></div>
        <div class="challenge-next"><b>目前進度：</b> ${escapeHtml(mission.progress?.current_step || "等待規劃")}</div>
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

  $("#review-missions").innerHTML = review.map((mission) => `
    <article class="challenge-card mission-card review-card">
      <div class="challenge-card-head"><div><h3>${escapeHtml(mission.title)}</h3><p class="objective">${escapeHtml(mission.objective)}</p></div><span class="pill">等待驗收</span></div>
      <div class="mission-result"><b>成果</b><p>${escapeHtml(mission.result_summary)}</p></div>
      <div class="mission-evidence">${(mission.evidence || []).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("") || '<span class="tag warn">未附證據</span>'}</div>
      <form class="mission-review-form" data-mission-id="${escapeHtml(mission.mission_id)}">
        <textarea name="feedback" rows="2" maxlength="4000" placeholder="驗收意見；退回時請說明需要改善的內容"></textarea>
        <div class="mission-actions"><button type="submit" data-decision="approve">勾選通過</button><button type="submit" data-decision="reject" class="secondary">退回改善</button></div>
      </form>
    </article>
  `).join("") || '<div class="empty">目前沒有等待驗收的成果。</div>';

  $("#completed-missions").innerHTML = completed.map((mission) => `
    <details class="challenge-card mission-card completed-card">
      <summary><span><b>${escapeHtml(mission.title)}</b><small>完成於 ${formatTime(mission.approved_at, true)}</small></span><span class="pill success">已通過</span></summary>
      <div class="mission-body"><p class="objective">${escapeHtml(mission.objective)}</p><div class="mission-requirements"><b>完成要求</b><p>${escapeHtml(mission.completion_requirements)}</p></div><div class="mission-result"><b>成果</b><p>${escapeHtml(mission.result_summary)}</p></div><div class="mission-evidence">${(mission.evidence || []).map((item) => `<span class="tag good">${escapeHtml(item)}</span>`).join("")}</div>${mission.review_feedback ? `<div class="mission-feedback"><b>驗收紀錄</b><p>${escapeHtml(mission.review_feedback)}</p></div>` : ""}</div>
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

function renderRoadmap(data) {
  const verified = data.verified_now || {};
  const capabilities = verified.registered_capabilities || [];
  const targets = data.targets || [];
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
  $("#roadmap-target-count").textContent = `${formatCount(targets.length)} targets`;
  $("#roadmap-targets").innerHTML = targets.map((target, index) => `
    <article class="roadmap-item">
      <span class="roadmap-index">${String(index + 1).padStart(2, "0")}</span>
      <div><div class="roadmap-item-head"><b>${escapeHtml(target.title)}</b><span class="tag ${target.state === "verified" ? "good" : target.state === "not_verified" ? "warn" : ""}">${escapeHtml(stateLabels[target.state] || target.state)}</span></div><p>${escapeHtml(target.measure)}</p></div>
    </article>
  `).join("");
  $("#roadmap-claim-policy").textContent = data.claim_policy || "";
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

function renderEvents(items) {
  const recent = items.filter((item) => item.event_type !== "Heartbeat").slice(-12).reverse();
  $("#events").innerHTML = recent.map((item) => `
    <article class="event-item">
      <span class="event-seq">${item.sequence}</span>
      <div><b>${escapeHtml(eventLabel(item.event_type))}</b><span>${formatTime(item.created_at, true)}</span></div>
    </article>
  `).join("") || '<div class="empty">尚無有效事件。</div>';
}

function updateSafety(health) {
  const chainValid = health.event_chain.valid;
  $("#chain-state").textContent = chainValid ? "事件鏈有效" : "事件鏈異常";
  $("#chain-state").className = chainValid ? "pill success" : "pill danger";
  $("#network-state").textContent = health.safety.network_enabled ? "受限來源" : "已停用";
  $("#system-state").textContent = health.safety.system_file_mutation_enabled ? "已開啟" : "未啟用";
}

async function refresh() {
  try {
    const [health, events, tasks, experiences, reflections, skills, capabilities, missions, evaluations, supervisor, runtime, roadmap] = await Promise.all([
      request("/health"),
      request("/v1/events?latest=true&limit=50"),
      request("/v1/tasks?limit=50"),
      request("/v1/experiences?limit=20"),
      request("/v1/reflections?limit=12"),
      request("/v1/skills?limit=100"),
      request("/v1/capabilities"),
      request("/v1/missions?limit=100"),
      request("/v1/evaluations"),
      request("/v1/supervisor/status"),
      request("/v1/runtime/status"),
      request("/v1/roadmap"),
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

    renderActivity(kernel, tasks.items, missions.items, supervisor, health.autonomous_learning);
    renderThoughtFeed(tasks.items, missions.items, reflections.items, events.items, experiences.items, supervisor);
    renderSupervisor(supervisor);
    renderHomeSummary(health, experiences.items, skills.items, missions.items, reflections.items, runtime);
    renderMissions(missions.items);
    renderExperiences(experiences.items);
    renderSkills(skills.items);
    renderRuntime(runtime);
    renderResearch(tasks.items, experiences.items);
    renderBenchmarks(evaluations);
    renderRoadmap(roadmap);
    renderImageStack(health);
    renderEvents(events.items);
    updateSafety(health);
    $("#capability-tags").innerHTML = capabilities.items.map((item) =>
      `<span class="tag good" title="${escapeHtml(item.description)}">${escapeHtml(item.name)}</span>`
    ).join("");
  } catch (error) {
    setConnection(false);
    $("#phase").textContent = "OFFLINE";
    console.error(error);
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
        metadata.seed !== undefined ? `seed ${metadata.seed}` : "",
        metadata.total_elapsed_seconds !== undefined
          ? `${metadata.total_elapsed_seconds}s total`
          : (metadata.elapsed_seconds !== undefined ? `${metadata.elapsed_seconds}s` : ""),
        metadata.peak_vram_mb !== undefined ? `${metadata.peak_vram_mb} MB VRAM` : "",
      ].filter(Boolean).join(" · ");
      if (artifact.type === "video") {
        return `
        <figure class="chat-artifact">
          <video class="chat-artifact-video" src="${escapeHtml(artifactUrl)}" controls preload="metadata"></video>
          <figcaption>${escapeHtml(details)}</figcaption>
        </figure>
        `;
      }
      if (artifact.type !== "image") return "";
      return `
        <figure class="chat-artifact">
          <a href="${escapeHtml(artifactUrl)}" target="_blank" rel="noopener">
            <img class="chat-artifact-image" src="${escapeHtml(artifactUrl)}" alt="ECK 生成圖片" loading="lazy">
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
        const output = task?.result?.output || {};
        message.content = `生成失敗：${output.error || output.detail || task.verification?.reason || task.status}`;
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

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;
  const priorHistory = chatHistory.slice(-10);
  chatHistory.push({ role: "user", content: message });
  saveChatHistory();
  renderChat();
  input.value = "";
  button.disabled = true;
  button.textContent = "處理中…";
  $("#chat-context").textContent = /(移除|去除).{0,8}(背景|背影)|remove.{0,8}background/i.test(message)
    ? "正在使用本機 rembg 移除背景…"
    : /(影片|視頻|動畫|短片|video|movie|animation|clip)/i.test(message)
    ? "正在建立首幀並排入本機 FramePack 影片生成…"
    : /(圖片|圖像|照片|插畫|image|picture|photo)/i.test(message)
    ? "正在規劃模型並執行本機 Forge 圖像生成…"
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
      toast("課題內容已更新");
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
      toast("成果已送交，等待你勾選驗收");
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
chatHistory.filter((item) => item.pending && item.task_id).forEach((item) => watchPendingTask(item.task_id));
showView();
refresh();
window.setInterval(refresh, 5000);
window.setInterval(updateUptime, 1000);
