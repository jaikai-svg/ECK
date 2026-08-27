import assert from "node:assert/strict";
import test from "node:test";

import { sleepView } from "../modules/workspace_quality.js";

test("sleep view exposes real phase result and non-zero changes", () => {
  const view = sleepView({
    run_id: "sleep-1",
    trigger_kind: "manual",
    status: "completed",
    phase: "completed",
    before: { knowledge_items: 4 },
    after: { knowledge_items: 5 },
    changes: { knowledge_items: 1, active_memory_skills: 0 },
    result: {
      event_chain_valid: true,
      summary: "Event chain verified without destructive consolidation.",
    },
    error: "",
    requested_at: "2026-08-12T00:00:00Z",
  });

  assert.equal(view.phase, "整理完成");
  assert.equal(view.terminal, true);
  assert.match(view.result, /Event chain verified/);
  assert.equal(view.changes, "knowledge_items +1");
});

test("sleep view does not invent an execution before the first run", () => {
  const view = sleepView(null);

  assert.equal(view.state, "尚未執行");
  assert.equal(view.terminal, true);
  assert.match(view.result, /才會建立真實執行紀錄/);
});
