import { create } from "zustand";
import { api, wsUrl } from "./api";
import type { Attachment, Confirm, Job, JobLogEntry, Message, Project, Snapshot } from "./types";

export type Shading = "wireframe" | "solid" | "material" | "render";
export type Gizmo = "none" | "translate" | "rotate" | "scale";

interface State {
  project: Project | null;
  snap: Snapshot | null;
  job: Job | null;
  live: JobLogEntry[];
  messages: Message[];
  attachments: Attachment[];
  selected: string | null;
  shading: Shading;
  gizmo: Gizmo;
  hdri: string;
  confirms: Confirm[];
  wsState: "connecting" | "open" | "closed";
  toast: { msg: string; kind: "ok" | "err" | "info" } | null;
  viewCmd: { view: string; n: number } | null;
  pendingAttach: string[];
  newNames: Set<string>;
  open: (id: string) => Promise<void>;
  close: () => void;
  setSnap: (s: Snapshot) => void;
  select: (n: string | null) => void;
  set: (p: Partial<State>) => void;
  notify: (msg: string, kind?: "ok" | "err" | "info") => void;
  refreshMessages: () => Promise<void>;
  refreshAttachments: () => Promise<void>;
  setView: (v: string) => void;
}

let ws: WebSocket | null = null;
let wsTimer: number | undefined;

export const useStore = create<State>((set, get) => ({
  project: null, snap: null, job: null, live: [], messages: [], attachments: [], selected: null,
  shading: "material", gizmo: "none", hdri: "studio", confirms: [], wsState: "closed", toast: null, viewCmd: null,
  pendingAttach: [], newNames: new Set(),
  set: (p) => set(p),
  notify: (msg, kind = "info") => { set({ toast: { msg, kind } }); window.setTimeout(() => { if (get().toast?.msg === msg) set({ toast: null }); }, 4200); },
  setView: (v) => set({ viewCmd: { view: v, n: (get().viewCmd?.n || 0) + 1 } }),
  select: (n) => set({ selected: n }),
  setSnap: (s) => {
    const prev = get().snap;
    const before = new Set(prev?.objects.map((o) => o.name) || []);
    const fresh = new Set(prev && prev.project_id === s.project_id ? s.objects.map((o) => o.name).filter((n) => !before.has(n)) : []);
    const sel = get().selected;
    set({ snap: s, newNames: fresh, selected: sel && s.objects.some((o) => o.name === sel) ? sel : null });
  },
  refreshMessages: async () => { const p = get().project; if (p) set({ messages: await api.get<Message[]>(`/api/projects/${p.id}/messages`) }); },
  refreshAttachments: async () => { const p = get().project; if (p) set({ attachments: await api.get<Attachment[]>(`/api/projects/${p.id}/attachments`) }); },
  close: () => { ws?.close(); ws = null; window.clearTimeout(wsTimer); set({ project: null, snap: null, job: null, live: [], messages: [], attachments: [], selected: null }); },
  open: async (id) => {
    get().close();
    const p = await api.get<Project & { snapshot: Snapshot; job: Job | null }>(`/api/projects/${id}`);
    set({ project: { id: p.id, name: p.name, mode: p.mode, created: p.created, updated: p.updated }, snap: p.snapshot, job: p.job,
          live: p.job?.log || [], newNames: new Set(), shading: "material" });
    try { localStorage.setItem("ai3d.lastProject", id); } catch { /* */ }
    await Promise.all([get().refreshMessages(), get().refreshAttachments()]);
    const connect = () => {
      if (get().project?.id !== id) return;
      set({ wsState: "connecting" });
      ws = new WebSocket(wsUrl(`/api/ws/${id}`));
      ws.onopen = () => set({ wsState: "open" });
      ws.onclose = () => { set({ wsState: "closed" }); if (get().project?.id === id) wsTimer = window.setTimeout(connect, 2000); };
      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        const st = get();
        switch (ev.type) {
          case "hello": st.setSnap(ev.snapshot); set({ job: ev.job, confirms: ev.confirms || [], live: ev.job?.log || st.live }); break;
          case "snapshot": st.setSnap(ev.snapshot); break;
          case "job": set({ job: ev.job }); if (ev.job.status !== "running") st.refreshMessages(); break;
          case "message": set({ messages: [...st.messages.filter((m) => m.id !== ev.message.id), ev.message] }); break;
          case "attachments": st.refreshAttachments(); break;
          case "confirm_request": set({ confirms: [...st.confirms, { id: ev.id, title: ev.title, detail: ev.detail }] }); break;
          case "confirm_closed": set({ confirms: st.confirms.filter((c) => c.id !== ev.id) }); break;
          case "step_done": if (ev.snapshot) st.setSnap(ev.snapshot); set({ live: [...st.live, ev].slice(-400) });
            if (st.job) set({ job: { ...st.job, step_index: st.job.step_index } }); break;
          case "stage": set({ live: [...st.live, ev].slice(-400), job: st.job ? { ...st.job, stage: ev.name, stages: ev.stages } : st.job }); break;
          case "log": case "step_start": case "step_error": case "assistant": set({ live: [...st.live, ev].slice(-400) }); break;
        }
      };
    };
    connect();
  },
}));

export function answerConfirm(id: string, approve: boolean) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "confirm", id, approve }));
  else api.post(`/api/confirm/${id}`, { approve }).catch(() => undefined);
  useStore.setState((s) => ({ confirms: s.confirms.filter((c) => c.id !== id) }));
}

export async function runOp(tool: string, args: Record<string, unknown>, label = "") {
  const p = useStore.getState().project;
  if (!p) return;
  try {
    const r = await api.post<{ snapshot?: Snapshot; result: unknown }>(`/api/projects/${p.id}/ops`, { tool, args, label });
    if (r.snapshot) useStore.getState().setSnap(r.snapshot);
    return r.result;
  } catch (e) {
    useStore.getState().notify((e as Error).message, "err");
    throw e;
  }
}
