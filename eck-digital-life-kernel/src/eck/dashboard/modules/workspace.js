import { request } from "./http.js";
import { bindSystemControls } from "./system_controls.js";
import { renderSleepRun } from "./workspace_quality.js";
import { ChatComponent, concise, escapeHtml, formatBytes, HomeComponent, LibraryComponent, ProjectListComponent, SkillComponent, } from "./workspace_components.js";
import { ConversationStore, WorkspaceDraftStore, } from "./workspace_state.js";
import { LibraryDomainComponent, renderArchiveStatus, ResultCenterComponent, } from "./workspace_phase2.js";
const pageTitles = {
    results: "成果中心",
    home: "首頁",
    projects: "專案",
    library: "ECK Library",
    skills: "技能",
    more: "更多",
};
const drafts = new WorkspaceDraftStore("eck-workspace-drafts-v1");
const conversations = new ConversationStore("eck-chat-history-v3");
const homeComponent = new HomeComponent();
const projectComponent = new ProjectListComponent();
const chatComponent = new ChatComponent();
const libraryComponent = new LibraryComponent();
const skillComponent = new SkillComponent();
const resultComponent = new ResultCenterComponent();
const libraryDomainComponent = new LibraryDomainComponent();
let homeState = null;
let projectLoadedCount = 0;
let projectNextOffset = null;
let projectFilter = "";
let refreshTimer = null;
let requestInFlight = false;
let commands = [];
let uptimeOrigin = null;
let libraryNextOffset = null;
let libraryQuery = "";
let skillNextOffset = null;
let skillPhase = "";
let resultNextOffset = null;
const element = (selector) => document.querySelector(selector);
function currentView() {
    const value = window.location.hash.replace("#", "") || "home";
    return value in pageTitles ? value : "home";
}
function showView() {
    const view = currentView();
    document.querySelectorAll("[data-view]").forEach((node) => {
        const active = node.dataset.view === view;
        node.hidden = !active;
        node.classList.toggle("active", active);
    });
    document.querySelectorAll("[data-view-link]").forEach((node) => {
        node.classList.toggle("active", node.dataset.viewLink === view);
    });
    const title = element("#page-title");
    if (title)
        title.textContent = pageTitles[view];
    stopPolling();
    void refreshView(true);
}
async function refreshView(force = false) {
    if (document.hidden || requestInFlight)
        return;
    const view = currentView();
    try {
        requestInFlight = true;
        const globalState = view !== "home" && homeState === null ? loadHome() : Promise.resolve();
        if (view === "home")
            await loadHome();
        if (view === "projects")
            await Promise.all([globalState, loadProjects(false, force)]);
        if (view === "results")
            await Promise.all([globalState, loadResults(false)]);
        if (view === "library") {
            await Promise.all([globalState, loadLibrary(false), loadLibraryDomains()]);
        }
        if (view === "skills")
            await Promise.all([globalState, loadSkills(false)]);
        if (view === "more")
            await Promise.all([globalState, loadSystemSummary()]);
        setConnection(true);
    }
    catch (error) {
        setConnection(false);
        toast(`更新失敗：${errorMessage(error)}`);
    }
    finally {
        requestInFlight = false;
        scheduleRefresh();
    }
}
async function loadResults(append) {
    const form = element("#result-filter-form");
    const values = form ? new FormData(form) : new FormData();
    const offset = append ? resultNextOffset ?? 0 : 0;
    const query = new URLSearchParams({ limit: "24", offset: String(offset) });
    const filters = [
        "artifact_type",
        "status",
        "storage_state",
        "project_id",
        "skill_id",
        "q",
        "created_from",
        "created_to",
    ];
    for (const key of filters) {
        const value = String(values.get(key) ?? "").trim();
        if (value)
            query.set(key, value);
    }
    const data = await request(`/v1/workspace/results?${query}`);
    if (!data)
        return;
    resultNextOffset = data.page.next_offset;
    resultComponent.render(data, append);
}
async function openResult(artifactId) {
    const dialog = element("#result-detail-dialog");
    const body = element("#result-detail-body");
    if (body)
        body.innerHTML = '<div class="dialog-loading">正在讀取成果與證據…</div>';
    if (dialog && !dialog.open)
        dialog.showModal();
    try {
        const item = await request(`/v1/workspace/results/${artifactId}`);
        if (item && body)
            body.innerHTML = resultComponent.detail(item);
    }
    catch (error) {
        if (body) {
            body.innerHTML = `<div class="empty-state"><p>${escapeHtml(errorMessage(error))}</p></div>`;
        }
    }
}
async function loadLibraryDomains() {
    const data = await request("/v1/workspace/library/domains");
    if (!data)
        return;
    libraryDomainComponent.render(data);
    bindLibrarySuggestionForms();
}
async function openBook(bookId) {
    const dialog = element("#result-detail-dialog");
    const body = element("#result-detail-body");
    if (body)
        body.innerHTML = '<div class="dialog-loading">正在讀取書籍版本…</div>';
    if (dialog && !dialog.open)
        dialog.showModal();
    try {
        const book = await request(`/v1/workspace/library/books/${bookId}`);
        if (book && body)
            body.innerHTML = libraryDomainComponent.renderBook(book);
    }
    catch (error) {
        if (body) {
            body.innerHTML = `<div class="empty-state"><p>${escapeHtml(errorMessage(error))}</p></div>`;
        }
    }
}
function bindLibrarySuggestionForms() {
    document.querySelectorAll(".library-suggestion-form").forEach((form) => {
        const bookId = form.dataset.bookId ?? "";
        const scope = `library-suggestion:${bookId}`;
        drafts.restore(scope, form);
        form.addEventListener("input", () => drafts.capture(scope, form));
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            void submitLibrarySuggestion(form, scope, bookId);
        });
    });
}
async function submitLibrarySuggestion(form, scope, bookId) {
    const values = new FormData(form);
    setFormBusy(form, true);
    try {
        await request(`/v1/workspace/library/books/${bookId}/suggestions`, {
            method: "POST",
            body: JSON.stringify({
                content: String(values.get("content") ?? ""),
                suggestion_type: String(values.get("suggestion_type") ?? "revision"),
            }),
        });
        drafts.clear(scope);
        form.reset();
        toast("建議已保存，並建立可追蹤的 Library 修訂任務。");
        await loadLibraryDomains();
    }
    catch (error) {
        toast(`建議送出失敗：${errorMessage(error)}`);
    }
    finally {
        setFormBusy(form, false);
    }
}
async function loadLibrary(append) {
    const offset = append ? libraryNextOffset ?? 0 : 0;
    const query = libraryQuery ? `&q=${encodeURIComponent(libraryQuery)}` : "";
    const data = await request(`/v1/workspace/library?limit=18&offset=${offset}${query}`);
    if (!data)
        return;
    libraryNextOffset = data.page.next_offset;
    libraryComponent.render(data, append);
}
async function loadSkills(append) {
    const offset = append ? skillNextOffset ?? 0 : 0;
    const phase = skillPhase ? `&phase=${encodeURIComponent(skillPhase)}` : "";
    const data = await request(`/v1/workspace/skills?limit=18&offset=${offset}${phase}`);
    if (!data)
        return;
    skillNextOffset = data.page.next_offset;
    skillComponent.render(data, append);
}
async function loadHome() {
    const data = await request("/v1/workspace/home");
    if (!data)
        return;
    homeState = data;
    homeComponent.render(data);
    uptimeOrigin = data.kernel.started_at ? new Date(data.kernel.started_at) : null;
    updateUptime();
}
async function loadProjects(append = false, force = false) {
    const offset = append ? projectNextOffset ?? 0 : 0;
    const limit = append ? 12 : force ? Math.min(48, Math.max(12, projectLoadedCount)) : 12;
    const status = projectFilter ? `&status=${encodeURIComponent(projectFilter)}` : "";
    const data = await request(`/v1/workspace/projects?limit=${limit}&offset=${offset}${status}`);
    if (!data)
        return;
    projectLoadedCount = append
        ? projectLoadedCount + data.items.length
        : data.items.length;
    projectNextOffset = data.page.next_offset;
    projectComponent.render(data.items, append);
    const count = element("#project-count");
    if (count)
        count.textContent = `${data.page.total} 個專案`;
    const more = element("#load-more-projects");
    if (more)
        more.hidden = data.page.next_offset === null;
}
async function openProject(projectId) {
    const dialog = element("#project-detail-dialog");
    const body = element("#project-detail-body");
    if (body)
        body.innerHTML = '<div class="dialog-loading">正在讀取專案紀錄…</div>';
    if (dialog && !dialog.open)
        dialog.showModal();
    try {
        const detail = await request(`/v1/workspace/projects/${projectId}`);
        if (!detail)
            return;
        projectComponent.renderDetail(detail);
        bindProjectDetailForms(projectId);
    }
    catch (error) {
        if (body)
            body.innerHTML = `<div class="empty-state"><p>${escapeHtml(errorMessage(error))}</p></div>`;
    }
}
function bindProjectDetailForms(projectId) {
    const edit = element("#project-edit-form");
    if (edit) {
        const scope = `project-edit:${projectId}`;
        drafts.restore(scope, edit);
        edit.addEventListener("input", () => drafts.capture(scope, edit));
        edit.addEventListener("submit", (event) => {
            event.preventDefault();
            void submitProjectEdit(edit, scope, projectId);
        });
    }
    document.querySelectorAll("[data-rollback-project]").forEach((button) => {
        button.addEventListener("click", () => {
            const revisionId = button.dataset.revisionId;
            if (revisionId)
                void rollbackProject(projectId, revisionId);
        });
    });
    const review = element("#project-review-form");
    if (review) {
        const scope = `project-review:${projectId}`;
        drafts.restore(scope, review);
        review.addEventListener("input", () => drafts.capture(scope, review));
        review.addEventListener("submit", (event) => {
            event.preventDefault();
            const submitter = event.submitter;
            const decision = submitter instanceof HTMLButtonElement ? submitter.value : "revise";
            void submitProjectReview(review, scope, decision === "approve");
        });
    }
    element("[data-reopen-project]")?.addEventListener("click", async () => {
        try {
            await request(`/v1/missions/${projectId}/reopen`, { method: "POST" });
            toast("專案已重新排入改善流程。");
            element("#project-detail-dialog")?.close();
            await Promise.all([loadProjects(false, true), loadHome()]);
        }
        catch (error) {
            toast(`重新執行失敗：${errorMessage(error)}`);
        }
    });
}
async function submitProjectEdit(form, scope, projectId) {
    const values = new FormData(form);
    const targetMonth = String(values.get("target_month") ?? "").trim();
    setFormBusy(form, true);
    try {
        await request(`/v1/missions/${projectId}`, {
            method: "PATCH",
            body: JSON.stringify({
                title: String(values.get("title") ?? ""),
                objective: String(values.get("objective") ?? ""),
                completion_requirements: String(values.get("completion_requirements") ?? ""),
                priority: String(values.get("priority") ?? "normal"),
                target_month: targetMonth || null,
                edit_reason: String(values.get("edit_reason") ?? ""),
            }),
        });
        drafts.clear(scope);
        toast("專案修改已保存，可從編輯歷史回滾。");
        await Promise.all([openProject(projectId), loadProjects(false, true), loadHome()]);
    }
    catch (error) {
        toast(`修改失敗：${errorMessage(error)}；草稿仍已保存。`);
    }
    finally {
        setFormBusy(form, false);
    }
}
async function rollbackProject(projectId, revisionId) {
    const reason = window.prompt("請輸入回滾原因", "使用先前已保存的專案版本");
    if (reason === null)
        return;
    if (reason.trim().length < 3) {
        toast("回滾原因至少需要 3 個字元。");
        return;
    }
    try {
        await request(`/v1/missions/${projectId}/revisions/${revisionId}/rollback`, {
            method: "POST",
            body: JSON.stringify({ reason: reason.trim() }),
        });
        drafts.clear(`project-edit:${projectId}`);
        toast("專案已回滾，且保留新的回滾紀錄。");
        await Promise.all([openProject(projectId), loadProjects(false, true), loadHome()]);
    }
    catch (error) {
        toast(`回滾失敗：${errorMessage(error)}`);
    }
}
async function submitProjectReview(form, scope, approved) {
    const feedback = String(new FormData(form).get("feedback") ?? "").trim();
    if (!approved && feedback.length < 3) {
        toast("退回改善時請提供具體驗收意見。");
        return;
    }
    const projectId = form.dataset.projectId ?? "";
    setFormBusy(form, true);
    try {
        await request(`/v1/missions/${projectId}/review`, {
            method: "POST",
            body: JSON.stringify({ approved, feedback }),
        });
        drafts.clear(scope);
        toast(approved ? "專案已驗收通過。" : "改善意見已保存並重新排程。");
        element("#project-detail-dialog")?.close();
        await Promise.all([loadProjects(false, true), loadHome()]);
    }
    catch (error) {
        toast(`送出失敗：${errorMessage(error)}`);
    }
    finally {
        setFormBusy(form, false);
    }
}
async function sendChat(form) {
    const input = element("#workspace-composer");
    if (!input)
        return;
    const message = input.value.trim();
    if (!message)
        return;
    setFormBusy(form, true);
    conversations.append({ role: "user", content: message, createdAt: new Date().toISOString() });
    chatComponent.render(conversations.list());
    try {
        const history = conversations.list().slice(-13, -1).map(({ role, content }) => ({
            role,
            content,
        }));
        const data = await request("/v1/chat", {
            method: "POST",
            body: JSON.stringify({ message, history }),
        });
        if (!data)
            throw new Error("ECK 沒有回傳內容。");
        conversations.append({
            role: "assistant",
            content: data.answer,
            model: data.model,
            artifacts: data.artifacts,
            createdAt: new Date().toISOString(),
        });
        drafts.clear("home-composer");
        input.value = "";
        hideCommandMenu();
        chatComponent.render(conversations.list());
        await loadHome();
        if (data.mission_id)
            toast("已建立持久化專案，可在專案頁追蹤。");
    }
    catch (error) {
        toast(`對話失敗：${errorMessage(error)}；輸入內容仍已保留。`);
        drafts.set("home-composer", "message", message);
        input.value = message;
    }
    finally {
        setFormBusy(form, false);
        input.focus();
    }
}
async function createProject(form) {
    const values = new FormData(form);
    setFormBusy(form, true);
    try {
        const created = await request("/v1/missions", {
            method: "POST",
            body: JSON.stringify({
                title: String(values.get("title") ?? ""),
                objective: String(values.get("objective") ?? ""),
                completion_requirements: String(values.get("completion_requirements") ?? ""),
                source: "human",
                schedule: "manual",
                priority: String(values.get("priority") ?? "normal"),
                execution_kind: String(values.get("execution_kind") ?? "auto"),
            }),
        });
        drafts.clear("project-create");
        form.reset();
        element("#project-create-dialog")?.close();
        toast("持久化專案已建立，ECK 將開始拆解工作。");
        await Promise.all([loadProjects(false, true), loadHome()]);
        if (created?.mission_id)
            await openProject(created.mission_id);
    }
    catch (error) {
        toast(`建立失敗：${errorMessage(error)}；草稿仍已保存。`);
    }
    finally {
        setFormBusy(form, false);
    }
}
async function loadCommands() {
    try {
        const data = await request("/v1/chat/commands");
        commands = data?.items ?? [];
    }
    catch {
        commands = [];
    }
}
function renderCommandMenu(value) {
    const menu = element("#command-menu");
    const input = element("#workspace-composer");
    if (!menu || !input)
        return;
    const normalized = value.trim().toLocaleLowerCase();
    if (!normalized.startsWith("/")) {
        hideCommandMenu();
        return;
    }
    const items = commands.filter((item) => item.command.toLocaleLowerCase().includes(normalized)
        || item.title.toLocaleLowerCase().includes(normalized.slice(1))).slice(0, 8);
    menu.innerHTML = items.map((item) => `
    <button type="button" role="option" data-command="${escapeHtml(item.insert)}" data-submit="${item.requires_prompt ? "false" : "true"}">
      <span>${escapeHtml(item.command)}</span><div><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.description)}</small></div>
    </button>
  `).join("");
    menu.hidden = items.length === 0;
    input.setAttribute("aria-expanded", String(items.length > 0));
}
function hideCommandMenu() {
    const menu = element("#command-menu");
    const input = element("#workspace-composer");
    if (menu)
        menu.hidden = true;
    input?.setAttribute("aria-expanded", "false");
}
async function loadSystemSummary() {
    const [data, archive, sleep] = await Promise.all([
        request("/v1/workspace/system"),
        request("/v1/workspace/archive/status"),
        request("/v1/kernel/sleep/status"),
    ]);
    renderSleepRun(sleep?.run);
    const services = data?.services;
    const resources = data?.resources;
    const evolution = data?.evolution;
    const target = element("#system-summary");
    if (!target || !services || !resources)
        return;
    const ollama = services.ollama;
    const forge = services.forge;
    const host = resources.host;
    const memory = host?.memory;
    const disk = host?.disk;
    const project = resources.project;
    target.innerHTML = [
        ["Ollama", ollama?.owned_process_running ? "按需服務中" : "可按需啟動"],
        ["Forge", forge?.runtime_state ?? "未知"],
        ["可用記憶體", formatBytes(memory?.available_bytes)],
        ["磁碟可用", formatBytes(disk?.free_bytes)],
        ["ECK 專案", project?.available ? formatBytes(project.logical_bytes) : "尚未測量"],
        ["演化交易", evolution?.transaction_count ?? 0],
        [
            "最新演化",
            evolution?.latest?.status ?? "尚無交易",
        ],
    ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join("");
    if (archive)
        renderArchiveStatus(archive);
}
function scheduleRefresh() {
    stopPolling();
    if (document.hidden)
        return;
    const view = currentView();
    if (!["home", "projects"].includes(view))
        return;
    const seconds = view === "home"
        ? Math.max(5, homeState?.refresh.poll_after_seconds ?? 30)
        : 30;
    refreshTimer = window.setTimeout(() => void refreshView(), seconds * 1000);
}
function stopPolling() {
    if (refreshTimer !== null)
        window.clearTimeout(refreshTimer);
    refreshTimer = null;
}
function updateUptime() {
    const target = element("#uptime");
    if (!target)
        return;
    if (!uptimeOrigin || Number.isNaN(uptimeOrigin.getTime())) {
        target.textContent = "00天 00:00:00";
        return;
    }
    const seconds = Math.max(0, Math.floor((Date.now() - uptimeOrigin.getTime()) / 1000));
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remaining = seconds % 60;
    target.textContent = `${String(days).padStart(2, "0")}天 ${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
}
function setConnection(online) {
    const dot = element("#connection-dot");
    const state = element("#connection-state");
    dot?.classList.toggle("online", online);
    if (state)
        state.textContent = online ? "本機核心已連線" : "核心連線中斷";
}
function toast(message) {
    const target = element("#toast");
    if (!target)
        return;
    target.textContent = message;
    target.classList.add("show");
    window.setTimeout(() => target.classList.remove("show"), 4000);
}
function setFormBusy(form, busy) {
    form.querySelectorAll("button, input, textarea, select").forEach((control) => { control.disabled = busy; });
    form.setAttribute("aria-busy", String(busy));
}
function errorMessage(error) {
    return error instanceof Error ? error.message : concise(error);
}
function bindEvents() {
    window.addEventListener("hashchange", showView);
    document.addEventListener("visibilitychange", () => {
        if (document.hidden)
            stopPolling();
        else
            void refreshView(true);
    });
    const composer = element("#workspace-composer");
    if (composer) {
        composer.value = drafts.value("home-composer", "message");
        composer.addEventListener("input", () => {
            drafts.set("home-composer", "message", composer.value);
            renderCommandMenu(composer.value);
        });
        composer.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                element("#workspace-chat-form")?.requestSubmit();
            }
        });
    }
    element("#workspace-chat-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        void sendChat(event.currentTarget);
    });
    element("#clear-conversation")?.addEventListener("click", () => {
        conversations.clear();
        chatComponent.render([]);
    });
    element("#command-menu")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-command]");
        if (!button || !composer)
            return;
        composer.value = button.dataset.command ?? "";
        drafts.set("home-composer", "message", composer.value);
        hideCommandMenu();
        composer.focus();
        if (button.dataset.submit === "true") {
            element("#workspace-chat-form")?.requestSubmit();
        }
    });
    document.addEventListener("click", (event) => {
        const project = event.target.closest("[data-open-project]");
        if (project?.dataset.openProject)
            void openProject(project.dataset.openProject);
        const result = event.target.closest("[data-open-result]");
        if (result?.dataset.openResult)
            void openResult(result.dataset.openResult);
        const evaluate = event.target.closest("[data-evaluate-domain]");
        if (evaluate?.dataset.evaluateDomain) {
            void request(`/v1/workspace/library/domains/${evaluate.dataset.evaluateDomain}/evaluate`, { method: "POST" }).then(() => loadLibraryDomains()).catch((error) => toast(errorMessage(error)));
        }
        const author = event.target.closest("[data-author-domain]");
        if (author?.dataset.authorDomain) {
            void request(`/v1/workspace/library/domains/${author.dataset.authorDomain}/author`, {
                method: "POST",
                body: JSON.stringify({
                    reason: "User requested publication after readiness passed.",
                }),
            }).then(() => loadLibraryDomains()).catch((error) => toast(errorMessage(error)));
        }
        const book = event.target.closest("[data-open-book]");
        if (book?.dataset.openBook)
            void openBook(book.dataset.openBook);
        const archive = event.target.closest("[data-archive-artifact]");
        if (archive?.dataset.archiveArtifact) {
            const artifactId = archive.dataset.archiveArtifact;
            void request(`/v1/workspace/results/${artifactId}/archive`, {
                method: "POST",
                body: JSON.stringify({ remove_local: null }),
            }).then(() => openResult(artifactId)).catch((error) => toast(errorMessage(error)));
        }
        const restore = event.target.closest("[data-restore-artifact]");
        if (restore?.dataset.restoreArtifact) {
            const artifactId = restore.dataset.restoreArtifact;
            void request(`/v1/workspace/results/${artifactId}/restore`, {
                method: "POST",
            }).then(async () => {
                toast("成果已驗證並還原至本機快取。");
                await openResult(artifactId);
            })
                .catch((error) => toast(errorMessage(error)));
        }
        const remove = event.target.closest("[data-delete-artifact]");
        if (remove?.dataset.deleteArtifact) {
            void deleteArtifact(remove.dataset.deleteArtifact);
        }
    });
    const createDialog = element("#project-create-dialog");
    const createForm = element("#project-create-form");
    if (createDialog && createForm) {
        drafts.restore("project-create", createForm);
        createForm.addEventListener("input", () => drafts.capture("project-create", createForm));
        createForm.addEventListener("submit", (event) => {
            event.preventDefault();
            void createProject(createForm);
        });
        element("#new-project")?.addEventListener("click", () => createDialog.showModal());
    }
    document.querySelectorAll("[data-close-dialog]").forEach((button) => {
        button.addEventListener("click", () => button.closest("dialog")?.close());
    });
    document.querySelectorAll("dialog").forEach((dialog) => {
        dialog.addEventListener("click", (event) => {
            if (event.target === dialog)
                dialog.close();
        });
    });
    element("#project-filter")?.addEventListener("change", (event) => {
        projectFilter = event.currentTarget.value;
        projectLoadedCount = 0;
        projectNextOffset = null;
        void loadProjects(false, true);
    });
    element("#load-more-projects")?.addEventListener("click", () => {
        void loadProjects(true, true);
    });
    element("#result-filter-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        resultNextOffset = null;
        void loadResults(false);
    });
    element("#load-more-results")?.addEventListener("click", () => {
        void loadResults(true);
    });
    element("#library-search-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        libraryQuery = String(new FormData(form).get("query") ?? "").trim();
        libraryNextOffset = null;
        void loadLibrary(false);
    });
    element("#load-more-library")?.addEventListener("click", () => {
        void loadLibrary(true);
    });
    element("#skill-filter")?.addEventListener("change", (event) => {
        skillPhase = event.currentTarget.value;
        skillNextOffset = null;
        void loadSkills(false);
    });
    element("#load-more-skills")?.addEventListener("click", () => {
        void loadSkills(true);
    });
    bindSystemControls(request, async () => {
        await loadHome();
        if (currentView() === "more")
            await loadSystemSummary();
    }, setConnection, toast);
}
async function deleteArtifact(artifactId) {
    try {
        const plan = await request(`/v1/workspace/results/${artifactId}/deletion-plan?include_derived=true`);
        if (!plan)
            return;
        if (!plan.deletable) {
            toast(`目前不能刪除：${plan.blockers.join("、")}`);
            return;
        }
        const confirmation = window.prompt(`將徹底刪除 ${plan.artifact_ids.length} 項索引及本機、NAS、快取與衍生檔案。請完整輸入成果名稱：`);
        if (confirmation === null)
            return;
        await request(`/v1/workspace/results/${artifactId}`, {
            method: "DELETE",
            body: JSON.stringify({
                plan_sha256: plan.plan_sha256,
                confirm_title: confirmation,
                include_derived: true,
            }),
        });
        element("#result-detail-dialog")?.close();
        toast("成果與可追溯衍生檔案已徹底刪除。");
        resultNextOffset = null;
        await Promise.all([loadResults(false), loadHome()]);
    }
    catch (error) {
        toast(`刪除失敗：${errorMessage(error)}`);
    }
}
chatComponent.render(conversations.list());
bindEvents();
void loadCommands();
showView();
window.setInterval(updateUptime, 1000);
