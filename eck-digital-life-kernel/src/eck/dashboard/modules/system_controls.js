import { renderSleepRun, sleepView } from "./workspace_quality.js";
export function bindSystemControls(request, refresh, setConnection, toast) {
    document.querySelectorAll("[data-action]").forEach((button) => {
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                const response = await request(`/v1/kernel/${button.dataset.action}`, { method: "POST" });
                if (button.dataset.action === "sleep") {
                    renderSleepRun(response.run);
                    await followSleepRun(request);
                }
                await refresh();
            }
            catch (error) {
                toast(`操作失敗：${errorMessage(error)}`);
            }
            finally {
                button.disabled = false;
            }
        });
    });
    document.querySelector("#shutdown-eck")?.addEventListener("click", async (event) => {
        const button = event.currentTarget;
        if (!(button instanceof HTMLButtonElement))
            return;
        button.disabled = true;
        button.textContent = "正在關閉…";
        try {
            await request("/v1/system/shutdown", { method: "POST" });
            setConnection(false);
            const state = document.querySelector("#connection-state");
            if (state)
                state.textContent = "ECK 正在完整關閉";
            toast("正在乾淨關閉 ECK 與其啟動的本機模型服務。");
        }
        catch (error) {
            button.disabled = false;
            button.textContent = "完全關閉";
            toast(`關閉失敗：${errorMessage(error)}`);
        }
    });
}
async function followSleepRun(request) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
        const response = await request("/v1/kernel/sleep/status");
        renderSleepRun(response.run);
        if (sleepView(response.run).terminal)
            return;
        await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
}
function errorMessage(error) {
    return error instanceof Error ? error.message : String(error);
}
