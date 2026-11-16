export type Mode = "cad" | "game";
export interface ObjectInfo {
  name: string; kind: string; parent?: string | null; dims?: number[]; color?: string; material?: string;
  triangles?: number; volume?: number; hidden: boolean; locked: boolean; meta?: Record<string, unknown>;
}
export interface OpInfo { id: string; tool: string; label: string; args: Record<string, unknown>; source?: string; author?: string | null; error?: string | null }
export interface Snapshot {
  project_id: string; mode: Mode; rev: number; objects: ObjectInfo[]; ops: OpInfo[]; can_undo: boolean; can_redo: boolean;
  model_url: string | null; units: "mm" | "m"; texture_res?: number; params: Record<string, number | string>;
}
export interface Project { id: string; name: string; mode: Mode; created: number; updated: number; ops?: number }
export interface JobLogEntry {
  type: string; ts: number; msg?: string; level?: string; label?: string; tool?: string; name?: string; text?: string;
  error?: string; ms?: number; result?: unknown; query?: boolean; stage?: string; author?: string | null;
}
export interface Job {
  id: string; status: "running" | "done" | "error" | "stopped"; stage: string; stages: string[]; engine: "planner" | "llm" | "team"; team?: TeamPublic | null;
  provider?: { name: string; model: string } | null; step_index: number; step_total: number; error: string; prompt: string;
  started: number; log: JobLogEntry[]; resumable: boolean;
}
export interface Message { id: string; role: "user" | "assistant"; content: string; meta: Record<string, unknown>; created: number }
export interface Attachment { id: string; filename: string; mime: string; width?: number; height?: number; size: number; url: string }
export interface ProviderInfo {
  id: string; kind: string; name: string; base_url: string; has_key: boolean; key_preview: string; models: string[];
  default_model: string; enabled: boolean; label: string;
}
export interface McpServer {
  id: string; name: string; url: string; transport: string; enabled: boolean; permission: "ask" | "allow" | "deny";
  disabled_tools: string[]; tools: { name: string; description: string; destructive?: boolean }[]; status: string; header_names: string[];
}
export interface Confirm { id: string; title: string; detail: string }

// ---------------------------------------------------------------- AI TEAM
export type AgentStatus = "idle" | "thinking" | "analyzing" | "modeling" | "reviewing" | "fixing" | "completed" | "error";
export interface TeamMemberCfg {
  id: string; provider_id: string; model: string; role: string; enabled: boolean; order: number; label: string; color: string;
  price_in?: number | null; price_out?: number | null;
}
export interface TeamConfig {
  members: TeamMemberCfg[]; mode: "auto" | "review" | "manual"; debate: boolean; max_rounds: number; max_debate_rounds: number;
  build_max_turns: number; max_tokens: number | null; max_seconds: number | null; max_cost_usd: number | null;
  auto_apply_max_severity: "minor" | "major" | "critical"; retries: number;
  roles: Record<string, { label: string; short: string; duty: string }>; effective_roles: Record<string, string[]>;
}
export interface TeamMemberState {
  id: string; name: string; provider_id: string; model: string; color: string; roles: string[]; failed: boolean;
  status: AgentStatus; task: string; last_message: string; issues: number; ops: number; tokens_in: number; tokens_out: number;
  calls: number; errors: number; last_error?: string;
}
export interface QACheck { id: string; category: string; name: string; status: "pass" | "warn" | "fail" | "unchecked" | "info"; value: unknown; expected: unknown; detail: string; targets: string[] }
export interface QAReport {
  mode: string; rev: number; checks: QACheck[]; categories: Record<string, { score: number | null; pass: number; warn: number; fail: number; unchecked: number }>;
  score: number | null; failed: string[]; warnings: string[]; unchecked: string[]; ms: number;
}
export interface QABrief { score: number | null; rev: number; failed: string[]; warnings: string[]; categories: Record<string, number | null>; checks: Record<string, { status: string; value: unknown }> }
export interface TeamPublic {
  members?: TeamMemberState[]; budget?: { tokens: number; seconds: number; cost: number }; round?: number; failed?: string[];
  conflicts?: { round: number; agent: string; item: string; stance: string; argument: string }[];
  comparisons?: { round: number; before: QABrief; after: QABrief; applied: number }[];
  ai_scores?: Record<string, { round: number; scores: Record<string, number>; verdict: string }>; mode?: string; task?: string;
}
export interface TeamMessage {
  id: string; job_id: string; round: number; phase: string; agent_id: string; agent_name: string; role: string; kind: string;
  content: string; data: Record<string, unknown> | null; tokens: number; created: number; color?: string;
}
export interface TeamIssue {
  id: string; round: number; author: string; author_id: string; severity: "minor" | "major" | "critical"; category: string; target: string;
  problem: string; evidence: string; qa_check: string | null; backed_by_test: boolean; fix: { tool: string; args: Record<string, unknown>; description?: string } | null;
  status: string; reviews: { by: string; approve: boolean; reason: string }[] | null; decision: { by?: string; decision?: string; reason?: string; engine_error?: string } | null;
  op_ids: string[] | null; version_before: string | null; created: number; error?: string;
}
