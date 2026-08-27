import assert from "node:assert/strict";
import test from "node:test";

import {
  ConversationStore,
  WorkspaceDraftStore,
} from "../modules/workspace_state.js";
import { concise } from "../modules/workspace_components.js";

class MemoryStorage {
  values = new Map();

  getItem(key) {
    return this.values.get(key) ?? null;
  }

  setItem(key, value) {
    this.values.set(key, value);
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

test("workspace drafts survive store reconstruction", () => {
  const storage = new MemoryStorage();
  const first = new WorkspaceDraftStore("drafts", storage);
  first.set("home-composer", "message", "尚未送出的任務");
  first.set("project-review:1", "feedback", "請改善導覽");

  first.set("library-suggestion:book-1", "content", "重新查證第三章來源");
  first.set("project-edit:project-1", "edit_reason", "修正驗收條件");

  const restored = new WorkspaceDraftStore("drafts", storage);
  assert.equal(restored.value("home-composer", "message"), "尚未送出的任務");
  assert.equal(restored.value("project-review:1", "feedback"), "請改善導覽");

  assert.equal(
    restored.value("library-suggestion:book-1", "content"),
    "重新查證第三章來源",
  );
  assert.equal(
    restored.value("project-edit:project-1", "edit_reason"),
    "修正驗收條件",
  );
  restored.clear("home-composer");
  assert.equal(restored.value("home-composer", "message"), "");
  assert.equal(restored.value("project-review:1", "feedback"), "請改善導覽");
});

test("conversation history survives refresh and clears explicitly", () => {
  const storage = new MemoryStorage();
  const first = new ConversationStore("chat", storage);
  first.append({
    role: "user",
    content: "建立一個專案",
    createdAt: "2026-08-12T00:00:00Z",
  });

  const restored = new ConversationStore("chat", storage);
  assert.equal(restored.list()[0].content, "建立一個專案");
  restored.clear();
  assert.deepEqual(new ConversationStore("chat", storage).list(), []);
});

test("empty structured summaries render as missing evidence", () => {
  assert.equal(concise({}), "");
  assert.equal(concise([]), "");
  assert.equal(concise({ status: "verified" }), '{"status":"verified"}');
});
