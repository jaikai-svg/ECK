const phaseLabels = {
    queued: "等待執行",
    validating_event_chain: "驗證不可竄改事件鏈",
    measuring_authoritative_memory: "量測權威記憶與技能",
    completed: "整理完成",
    failed: "整理失敗",
};
export function sleepView(run) {
    if (!run) {
        return {
            state: "尚未執行",
            phase: "沒有睡眠整理紀錄",
            result: "按下睡眠整理後才會建立真實執行紀錄。",
            changes: "尚無變化",
            terminal: true,
        };
    }
    const changes = Object.entries(run.changes ?? {})
        .filter(([, value]) => Number(value) !== 0)
        .map(([key, value]) => `${key} ${Number(value) > 0 ? "+" : ""}${value}`);
    const resultSummary = String(run.result?.summary ?? "").trim();
    const chain = run.result?.event_chain_valid;
    const fallback = chain === true
        ? "事件鏈驗證通過；已完成權威資料量測。"
        : chain === false ? "事件鏈驗證失敗，請查看錯誤。" : "等待執行結果。";
    return {
        state: run.status,
        phase: phaseLabels[run.phase] ?? run.phase,
        result: run.error || resultSummary || fallback,
        changes: changes.length ? changes.join(" · ") : "權威計數沒有變化",
        terminal: run.status === "completed" || run.status === "failed",
    };
}
export function renderSleepRun(run) {
    const view = sleepView(run);
    const values = {
        "sleep-state": view.state,
        "sleep-phase": view.phase,
        "sleep-result": view.result,
        "sleep-changes": view.changes,
    };
    Object.entries(values).forEach(([id, value]) => {
        const target = document.querySelector(`#${id}`);
        if (target)
            target.textContent = value;
    });
}
