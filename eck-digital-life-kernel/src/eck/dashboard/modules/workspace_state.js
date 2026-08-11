export class WorkspaceDraftStore {
    storageKey;
    storage;
    state;
    constructor(storageKey, storage) {
        this.storageKey = storageKey;
        this.storage = storage ?? window.localStorage;
        this.state = this.load();
    }
    value(scope, field = "value") {
        return this.state[scope]?.[field] ?? "";
    }
    set(scope, field, value) {
        this.state[scope] = { ...(this.state[scope] ?? {}), [field]: value };
        this.persist();
    }
    capture(scope, form) {
        const values = {};
        new FormData(form).forEach((value, key) => {
            values[key] = String(value);
        });
        this.state[scope] = values;
        this.persist();
    }
    restore(scope, form) {
        const values = this.state[scope] ?? {};
        Object.entries(values).forEach(([name, value]) => {
            const control = form.elements.namedItem(name);
            if (control instanceof HTMLInputElement
                || control instanceof HTMLTextAreaElement
                || control instanceof HTMLSelectElement) {
                control.value = value;
            }
        });
    }
    clear(scope) {
        delete this.state[scope];
        this.persist();
    }
    snapshot() {
        return JSON.parse(JSON.stringify(this.state));
    }
    load() {
        try {
            const parsed = JSON.parse(this.storage.getItem(this.storageKey) ?? "{}");
            return parsed && typeof parsed === "object" && !Array.isArray(parsed)
                ? parsed
                : {};
        }
        catch {
            return {};
        }
    }
    persist() {
        this.storage.setItem(this.storageKey, JSON.stringify(this.state));
    }
}
export class ConversationStore {
    storageKey;
    storage;
    messages;
    constructor(storageKey, storage) {
        this.storageKey = storageKey;
        this.storage = storage ?? window.localStorage;
        this.messages = this.load();
    }
    list() {
        return [...this.messages];
    }
    append(message) {
        this.messages = [...this.messages, message].slice(-40);
        this.persist();
    }
    clear() {
        this.messages = [];
        this.storage.removeItem(this.storageKey);
    }
    load() {
        try {
            const parsed = JSON.parse(this.storage.getItem(this.storageKey) ?? "[]");
            if (!Array.isArray(parsed))
                return [];
            return parsed.filter((item) => {
                if (!item || typeof item !== "object")
                    return false;
                const role = item.role;
                const content = item.content;
                return (role === "user" || role === "assistant") && typeof content === "string";
            }).slice(-40);
        }
        catch {
            return [];
        }
    }
    persist() {
        this.storage.setItem(this.storageKey, JSON.stringify(this.messages));
    }
}
