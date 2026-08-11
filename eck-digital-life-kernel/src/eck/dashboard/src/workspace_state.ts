export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

type DraftState = Record<string, Record<string, string>>;

export class WorkspaceDraftStore {
  private readonly storage: StorageLike;
  private state: DraftState;

  constructor(
    private readonly storageKey: string,
    storage?: StorageLike,
  ) {
    this.storage = storage ?? window.localStorage;
    this.state = this.load();
  }

  value(scope: string, field = "value"): string {
    return this.state[scope]?.[field] ?? "";
  }

  set(scope: string, field: string, value: string): void {
    this.state[scope] = { ...(this.state[scope] ?? {}), [field]: value };
    this.persist();
  }

  capture(scope: string, form: HTMLFormElement): void {
    const values: Record<string, string> = {};
    new FormData(form).forEach((value, key) => {
      values[key] = String(value);
    });
    this.state[scope] = values;
    this.persist();
  }

  restore(scope: string, form: HTMLFormElement): void {
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

  clear(scope: string): void {
    delete this.state[scope];
    this.persist();
  }

  snapshot(): DraftState {
    return JSON.parse(JSON.stringify(this.state)) as DraftState;
  }

  private load(): DraftState {
    try {
      const parsed: unknown = JSON.parse(this.storage.getItem(this.storageKey) ?? "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed as DraftState
        : {};
    } catch {
      return {};
    }
  }

  private persist(): void {
    this.storage.setItem(this.storageKey, JSON.stringify(this.state));
  }
}

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
  model?: string;
  artifacts?: Array<Record<string, unknown>>;
  createdAt: string;
}

export class ConversationStore {
  private readonly storage: StorageLike;
  private messages: ConversationMessage[];

  constructor(
    private readonly storageKey: string,
    storage?: StorageLike,
  ) {
    this.storage = storage ?? window.localStorage;
    this.messages = this.load();
  }

  list(): ConversationMessage[] {
    return [...this.messages];
  }

  append(message: ConversationMessage): void {
    this.messages = [...this.messages, message].slice(-40);
    this.persist();
  }

  clear(): void {
    this.messages = [];
    this.storage.removeItem(this.storageKey);
  }

  private load(): ConversationMessage[] {
    try {
      const parsed: unknown = JSON.parse(this.storage.getItem(this.storageKey) ?? "[]");
      if (!Array.isArray(parsed)) return [];
      return parsed.filter((item): item is ConversationMessage => {
        if (!item || typeof item !== "object") return false;
        const role = (item as { role?: unknown }).role;
        const content = (item as { content?: unknown }).content;
        return (role === "user" || role === "assistant") && typeof content === "string";
      }).slice(-40);
    } catch {
      return [];
    }
  }

  private persist(): void {
    this.storage.setItem(this.storageKey, JSON.stringify(this.messages));
  }
}
