export type Mode = "cad" | "game";
export interface ObjectInfo {
  name: string; kind: string; parent?: string | null; dims?: number[]; color?: string; material?: string;
  triangles?: number; volume?: number; hidden: boolean; locked: boolean; meta?: Record<string, unknown>;
}
export interface OpInfo { id: string; tool: string; label: string; args: Record<string, unknown>; source?: string; error?: string | null }
export interface Snapshot {
  project_id: string; mode: Mode; rev: number; objects: ObjectInfo[]; ops: OpInfo[]; can_undo: boolean; can_redo: boolean;
  model_url: string | null; units: "mm" | "m"; texture_res?: number; params: Record<string, number | string>;
}
export interface Project { id: string; name: string; mode: Mode; created: number; updated: number; ops?: number }
export interface JobLogEntry {
  type: string; ts: number; msg?: string; level?: string; label?: string; tool?: string; name?: string; text?: string;
  error?: string; ms?: number; result?: unknown; query?: boolean; stage?: string;
}
export interface Job {
  id: string; status: "running" | "done" | "error" | "stopped"; stage: string; stages: string[]; engine: "planner" | "llm";
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
