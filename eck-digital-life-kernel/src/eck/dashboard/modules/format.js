export function formatTime(value, includeDate = false) {
    if (!value)
        return "—";
    const options = includeDate
        ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
        : { hour: "2-digit", minute: "2-digit" };
    return new Intl.DateTimeFormat("zh-TW", options).format(new Date(value));
}
export function formatCount(value) {
    return new Intl.NumberFormat("zh-TW").format(Number(value || 0));
}
export function formatBytes(value, digits = 1) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0)
        return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = bytes;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size.toFixed(unit >= 3 ? digits : 0)} ${units[unit]}`;
}
export function formatDuration(value) {
    if (!value)
        return { days: "00天", clock: "00:00:00" };
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
export function safeUrl(value) {
    try {
        const url = new URL(String(value));
        return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    }
    catch {
        return "";
    }
}
