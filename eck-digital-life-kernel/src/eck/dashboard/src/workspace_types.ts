import type { ConversationMessage } from "./workspace_state.js";

export interface ArtifactLink {
  kind: string;
  label: string;
  url: string;
  filename?: string;
  bytes?: number;
}

export interface ProjectSummary {
  project_id: string;
  title: string;
  objective: string;
  status: string;
  priority: string;
  source: string;
  updated_at: string;
  created_at: string;
  progress_percent: number;
  current_step: string;
  step_counts: Record<string, number>;
  waiting_on: string;
  result_summary: string;
  review_feedback: string;
  revision: number;
  edit_revision_count?: number;
  artifacts: ArtifactLink[];
}

export interface ActivitySummary {
  kind: string;
  state: string;
  title: string;
  detail: string;
  project_id?: string;
  task_id?: string;
  current_step: string | null;
  progress_percent: number | null;
  waiting_on: string;
  summary: Record<string, unknown>;
}

export interface WorkspaceHome {
  schema_version: string;
  generated_at: string;
  kernel: {
    phase: string;
    started_at: string | null;
    event_count: number;
    pending_tasks: number;
  };
  activity: ActivitySummary;
  running_projects: ProjectSummary[];
  recent_results: ProjectSummary[];
  learning: {
    verified_experiences: number;
    knowledge_items: number;
    memory_skills: number;
    runtime_skills: number;
    available_skills?: number;
    total_memory_skills?: number;
    total_runtime_skills?: number;
  };
  resources: Record<string, unknown>;
  refresh: {
    busy: boolean;
    poll_after_seconds: number;
    pause_when_hidden: boolean;
  };
}

export interface ProjectPage {
  schema_version: string;
  items: ProjectSummary[];
  page: {
    limit: number;
    offset: number;
    total: number;
    next_offset: number | null;
  };
}

export interface ProjectDetail {
  schema_version: string;
  project: ProjectSummary;
  mission: Record<string, unknown>;
  steps: Array<Record<string, unknown>>;
  react_summaries: Array<Record<string, unknown>>;
  artifacts: ArtifactLink[];
  workspace_bytes: number;
  thinking_policy: string;
  skill_usages: Array<Record<string, unknown>>;
  edit_revisions: MissionEditRevision[];
}

export interface MissionEditRevision {
  revision_id: string;
  revision: number;
  changed_fields: string[];
  reason: string;
  actor: string;
  rollback_of_revision_id?: string | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  created_at: string;
}

export interface ChatResponse {
  answer: string;
  model?: string;
  tool?: string;
  artifacts?: Array<Record<string, unknown>>;
  mission_id?: string;
}

export interface CommandItem {
  command: string;
  insert: string;
  title: string;
  description: string;
  category: string;
  requires_prompt: boolean;
}

export interface LibraryCard {
  knowledge_id: string;
  title: string;
  claim: string;
  capability: string;
  chapter: string;
  sources: Array<Record<string, unknown>>;
  source_evidence_ids: string[];
  counterexamples: string[];
  confidence: number;
  externally_grounded: boolean;
  reproducible: boolean;
  verification_result: string;
  unresolved_questions: string[];
  reflection: Record<string, string>;
  content_sha256: string;
  revision: number;
  revision_history: Array<Record<string, unknown>>;
  created_at: string;
}

export interface LibraryPage {
  schema_version: string;
  source_authority: string;
  cache: Record<string, unknown>;
  book: Record<string, unknown>;
  chapters: Array<Record<string, unknown>>;
  items: LibraryCard[];
  page: {
    limit: number;
    offset: number;
    total: number;
    next_offset: number | null;
  };
}

export interface SkillItem {
  skill_id: string;
  name: string;
  capability: string;
  source_kind: string;
  phase: string;
  executable: boolean;
  verified: boolean;
  active: boolean;
  version: string | null;
  source: string;
  metrics: Record<string, unknown>;
  description: string;
  scope: {
    operations: string[];
    permissions: string[];
    capability: string;
  };
  source_detail: Record<string, unknown>;
  test_result: Record<string, unknown>;
  completed_task_count: number;
  completed_tasks: Array<Record<string, unknown>>;
  updated_at: string;
  relationship_kind?: string;
  actual_usage_records?: Array<Record<string, unknown>>;
}

export interface SkillPage {
  schema_version: string;
  source_authority: string;
  items: SkillItem[];
  counts: Record<string, number>;
  page: {
    limit: number;
    offset: number;
    total: number;
    next_offset: number | null;
  };
  matching_policy: string;
}

export interface ArtifactItem {
  artifact_id: string;
  artifact_type: string;
  title: string;
  status: string;
  source_kind: string;
  source_id: string;
  task_id?: string | null;
  project_id?: string | null;
  worker: string;
  model: string;
  version: string;
  content_sha256: string;
  size_bytes: number;
  local_path: string;
  storage_state: string;
  mime_type: string;
  integrity_status: string;
  created_at: string;
  metadata: Record<string, unknown>;
  skills: Array<Record<string, unknown>>;
}

export interface ArtifactPage {
  source_authority: string;
  projection_only: boolean;
  items: ArtifactItem[];
  page: {
    limit: number;
    offset: number;
    total: number;
    next_offset: number | null;
  };
}

export interface ReadinessReport {
  report_id: string;
  passed: boolean;
  metrics: Record<string, number>;
  gates: Record<string, boolean>;
  critical_gaps: string[];
  created_at: string;
}

export interface LibraryBookSummary {
  book_id: string;
  title: string;
  status: string;
  current_revision: number;
  latest_revision?: Record<string, unknown> | null;
}

export interface LibraryDomain {
  domain_id: string;
  slug: string;
  title: string;
  description: string;
  status: string;
  card_count: number;
  readiness?: ReadinessReport | null;
  books: LibraryBookSummary[];
  thresholds: Record<string, number>;
  updated_at: string;
}

export interface LibraryDomainPage {
  items: LibraryDomain[];
  source_authority: string;
}

export interface ArchiveStatus {
  provider: string;
  configured: boolean;
  online: boolean;
  state: string;
  root: string;
  cache: {
    path: string;
    size_bytes: number;
    max_bytes: number;
    entries: number;
    in_use: number;
  };
}

export interface ArtifactDeletionPlan {
  artifact_id: string;
  artifact_title: string;
  artifact_ids: string[];
  targets: Array<Record<string, unknown>>;
  blockers: string[];
  deletable: boolean;
  total_bytes: number;
  plan_sha256: string;
}

export interface SleepRun {
  run_id: string;
  trigger_kind: string;
  status: string;
  phase: string;
  before: Record<string, number>;
  after: Record<string, number>;
  changes: Record<string, number>;
  result: Record<string, unknown>;
  error: string;
  requested_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export type { ConversationMessage };
