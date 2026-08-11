type DraftValues = Record<string, string>;
type DraftCollection = Record<string, DraftValues>;

export class MissionDraftStore {
  private drafts: DraftCollection;

  constructor(
    private readonly storageKey: string,
    private readonly storage: Storage = window.localStorage,
  ) {
    this.drafts = this.load();
  }

  value(kind: string, missionId: string, field: string): string {
    return this.drafts[`${kind}:${missionId}`]?.[field] ?? "";
  }

  save(form: HTMLFormElement): void {
    const values: DraftValues = {};
    new FormData(form).forEach((value, key) => {
      values[key] = String(value);
    });
    this.drafts[this.formKey(form)] = values;
    this.persist();
  }

  clear(form: HTMLFormElement): void {
    delete this.drafts[this.formKey(form)];
    this.persist();
  }

  private load(): DraftCollection {
    try {
      const value: unknown = JSON.parse(this.storage.getItem(this.storageKey) ?? "{}");
      return value && typeof value === "object" && !Array.isArray(value)
        ? value as DraftCollection
        : {};
    } catch {
      return {};
    }
  }

  private formKey(form: HTMLFormElement): string {
    const kind = form.classList.contains("mission-review-form")
      ? "review"
      : form.classList.contains("mission-edit-form") ? "edit" : "completion";
    return `${kind}:${form.dataset.missionId ?? ""}`;
  }

  private persist(): void {
    this.storage.setItem(this.storageKey, JSON.stringify(this.drafts));
  }
}
