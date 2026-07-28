const $ = (selector) => document.querySelector(selector);

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 2800);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}

function formatTime(value) {
  if (!value) return "–";
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit", day: "2-digit", hour: "2-digit",
    minute: "2-digit", second: "2-digit",
  }).format(new Date(value));
}

async function refresh() {
  try {
    const [health, events, skills] = await Promise.all([
      request("/health"),
      request("/v1/events?limit=12"),
      request("/v1/skills?limit=10"),
    ]);
    const kernel = health.kernel;
    $("#phase").textContent = kernel.phase.toUpperCase();
    $("#boot-count").textContent = kernel.boot_count;
    $("#event-count").textContent = kernel.event_count;
    $("#pending-tasks").textContent = kernel.pending_tasks;
    $("#pending-approvals").textContent = kernel.pending_approvals;
    const memory = health.memory;
    $("#memory-counts").textContent =
      `E ${memory.experiences} · K ${memory.knowledge} · ` +
      `R ${memory.reflections} · S ${memory.skills}`;

    const network = health.safety.network_enabled;
    $("#network-dot").className = network ? "fail" : "ok";
    $("#network-state").textContent = network ? "網路能力：已啟用" : "網路能力：預設關閉";
    const system = health.safety.system_file_mutation_enabled;
    $("#system-dot").className = system ? "fail" : "ok";
    $("#system-state").textContent = system ? "系統檔案變更：已啟用" : "系統檔案變更：禁止";

    $("#events").innerHTML = events.items.slice().reverse().map((item) => `
      <tr><td>${item.sequence}</td><td>${item.event_type}</td>
      <td title="${item.aggregate_id}">${item.aggregate_id}</td>
      <td>${formatTime(item.created_at)}</td></tr>`).join("");
    $("#skills").innerHTML = skills.items.map((item) => `
      <tr><td title="${item.fingerprint}">${item.name}</td>
      <td>${item.success_count}</td>
      <td>${item.active ? "Active" : "Candidate"}</td></tr>`).join("")
      || `<tr><td colspan="3">尚無通過驗證的候選技能</td></tr>`;
  } catch (error) {
    $("#phase").textContent = "OFFLINE";
    console.error(error);
  }
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await request(`/v1/kernel/${button.dataset.action}`, { method: "POST" });
      toast(`Kernel ${button.dataset.action} accepted`);
      await refresh();
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  });
});

$("#run-demos").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  $("#demo-output").textContent = "執行驗收情境中…";
  try {
    const result = await request("/v1/demos/all", { method: "POST" });
    const codePass = result.safe_code.status === "verified_success";
    const gridPass = result.gridworld.learning_measure.fewer_steps_after_experience;
    const persistencePass = result.persistence.acceptance;
    for (const [selector, pass] of [
      ["#demo-persistence", persistencePass],
      ["#demo-code", codePass],
      ["#demo-grid", gridPass],
    ]) {
      const node = $(selector);
      node.textContent = pass ? "PASS" : "FAIL";
      node.className = pass ? "pass" : "fail";
    }
    $("#demo-output").textContent = JSON.stringify({
      persistence: result.persistence,
      safe_code_status: result.safe_code.status,
      learning_measure: result.gridworld.learning_measure,
    }, null, 2);
    toast("驗收情境執行完成");
    await refresh();
  } catch (error) {
    $("#demo-output").textContent = error.stack || error.message;
    toast("驗收失敗");
  } finally {
    button.disabled = false;
  }
});

refresh();
setInterval(refresh, 5000);
