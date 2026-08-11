export class MissionDraftStore {
    storageKey;
    storage;
    drafts;
    constructor(storageKey, storage = window.localStorage) {
        this.storageKey = storageKey;
        this.storage = storage;
        this.drafts = this.load();
    }
    value(kind, missionId, field) {
        return this.drafts[`${kind}:${missionId}`]?.[field] ?? "";
    }
    save(form) {
        const values = {};
        new FormData(form).forEach((value, key) => {
            values[key] = String(value);
        });
        this.drafts[this.formKey(form)] = values;
        this.persist();
    }
    clear(form) {
        delete this.drafts[this.formKey(form)];
        this.persist();
    }
    load() {
        try {
            const value = JSON.parse(this.storage.getItem(this.storageKey) ?? "{}");
            return value && typeof value === "object" && !Array.isArray(value)
                ? value
                : {};
        }
        catch {
            return {};
        }
    }
    formKey(form) {
        const kind = form.classList.contains("mission-review-form")
            ? "review"
            : form.classList.contains("mission-edit-form") ? "edit" : "completion";
        return `${kind}:${form.dataset.missionId ?? ""}`;
    }
    persist() {
        this.storage.setItem(this.storageKey, JSON.stringify(this.drafts));
    }
}
