import { Bot, Cpu, Paperclip, Redo2, Send, Square, Undo2, User, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { ProviderInfo, Snapshot } from "../lib/types";
import { AttachmentThumb, useUpload } from "./LeftPanel";

const EXAMPLES: Record<string, string[]> = {
  cad: ["Stwórz kwadratową obudowę 100 × 100 × 30 mm z czterema otworami montażowymi i logo na górze",
        "Płyta montażowa 120 × 80 × 5 mm z czterema otworami 4.5 mm", "Zwiększ wysokość o 5 mm", "Zmień kolor na ciemny czerwony"],
  game: ["Stwórz realistyczny, kolorowy, futurystyczny samochód sportowy z czarnymi felgami, czerwonym lakierem i szczegółowym wnętrzem",
         "Stwórz postać w niebieskiej kurtce i czarnych spodniach", "Zmień felgi na srebrne", "Zoptymalizuj model do Roblox"],
};

export default function AIPanel() {
  const project = useStore((s) => s.project);
  const messages = useStore((s) => s.messages);
  const job = useStore((s) => s.job);
  const snap = useStore((s) => s.snap);
  const selected = useStore((s) => s.selected);
  const attachments = useStore((s) => s.attachments);
  const pending = useStore((s) => s.pendingAttach);
  const live = useStore((s) => s.live);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [providerId, setProviderId] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [text, setText] = useState("");
  const [drag, setDrag] = useState(false);
  const [scopeToSel, setScopeToSel] = useState(true);
  const upload = useUpload();
  const listRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadProviders = () => api.get<{ providers: ProviderInfo[]; default_provider: string | null }>("/api/providers").then((r) => {
    const en = r.providers.filter((p) => p.enabled);
    setProviders(en);
    const stored = (() => { try { return localStorage.getItem("ai3d.provider") || ""; } catch { return ""; } })();
    const pick = en.find((p) => p.id === stored)?.id || r.default_provider || en[0]?.id || "offline";
    setProviderId(pick);
  }).catch(() => setProviderId("offline"));
  useEffect(() => { loadProviders(); window.addEventListener("ai3d:providers", loadProviders); return () => window.removeEventListener("ai3d:providers", loadProviders); }, []);
  const prov = providers.find((p) => p.id === providerId);
  useEffect(() => { setModel(prov?.default_model || prov?.models[0] || ""); }, [providerId, prov?.default_model]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { listRef.current?.scrollTo({ top: 1e9, behavior: "smooth" }); }, [messages.length, live.length]);

  const running = job?.status === "running";
  const send = async (prompt?: string) => {
    const p = (prompt ?? text).trim();
    if (!p || !project || running) return;
    try {
      useStore.setState({ live: [] });
      const j = await api.post<NonNullable<typeof job>>(`/api/projects/${project.id}/jobs`, {
        prompt: p, attachment_ids: pending, selected: scopeToSel ? selected : null,
        provider_id: providerId && providerId !== "offline" ? providerId : "offline", model: model || null,
      });
      useStore.setState({ job: j, pendingAttach: [] });
      setText("");
    } catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  const ctl = async (path: string) => {
    if (!project) return;
    try {
      const r = await api.post<{ snapshot?: Snapshot } | null>(`/api/projects/${project.id}/${path}`);
      if (r && "snapshot" in r && r.snapshot) useStore.getState().setSnap(r.snapshot);
    } catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  const currentOp = [...live].reverse().find((e) => e.type === "step_start" || e.type === "step_done");

  return (
    <div className="h-full flex flex-col bg-panel border-l border-line"
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
      onDrop={async (e) => { e.preventDefault(); setDrag(false); const ids = await upload(e.dataTransfer.files); useStore.setState({ pendingAttach: [...pending, ...ids] }); }}>
      <div className="panel-title"><span className="flex items-center gap-1.5"><Bot size={13} /> Asystent AI</span>
        <span className="flex items-center gap-0.5">
          <button className="icon-btn !w-6 !h-6" title="Cofnij (Ctrl+Z)" disabled={!snap?.can_undo || running} onClick={() => ctl("undo")}><Undo2 size={13} /></button>
          <button className="icon-btn !w-6 !h-6" title="Ponów (Ctrl+Shift+Z)" disabled={!snap?.can_redo || running} onClick={() => ctl("redo")}><Redo2 size={13} /></button>
        </span>
      </div>
      <div className="px-3 py-2 border-b border-line space-y-1.5">
        <div className="flex gap-1.5">
          <select className="input !w-1/2" value={providerId} onChange={(e) => { setProviderId(e.target.value); try { localStorage.setItem("ai3d.provider", e.target.value); } catch { /* */ } }}>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            <option value="offline">Planer offline (bez AI)</option>
          </select>
          {providerId !== "offline" ? (
            <select className="input !w-1/2 font-mono text-xxs" value={model} onChange={(e) => setModel(e.target.value)}>
              {[...new Set([model, ...(prov?.models || [])])].filter(Boolean).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : <div className="w-1/2 text-xxs text-mute leading-tight flex items-center">Reguły + szablony, bez LLM. Dodaj dostawcę w Ustawieniach.</div>}
        </div>
      </div>

      <div ref={listRef} className={`flex-1 overflow-y-auto px-3 py-2 space-y-3 ${drag ? "bg-accent/5 outline-dashed outline-1 outline-accent -outline-offset-4" : ""}`}>
        {!messages.length && (
          <div className="pt-2">
            <div className="text-dim mb-2">Opisz, co chcesz zbudować. Przykłady:</div>
            {(EXAMPLES[project?.mode || "cad"]).map((e) => (
              <button key={e} onClick={() => setText(e)} className="block w-full text-left px-2.5 py-2 mb-1.5 rounded-md border border-line hover:border-line2 hover:bg-panel2 text-dim hover:text-fg transition-colors">{e}</button>
            ))}
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className="flex gap-2 slide-in">
            <div className={`w-6 h-6 shrink-0 rounded-md flex items-center justify-center ${m.role === "user" ? "bg-panel2 text-dim" : "bg-accent/15 text-accent"}`}>
              {m.role === "user" ? <User size={13} /> : <Bot size={13} />}
            </div>
            <div className="min-w-0 flex-1">
              <div className="whitespace-pre-wrap leading-relaxed break-words">{m.content}</div>
              <div className="text-xxs text-mute mt-0.5 flex gap-2">
                <span>{new Date(m.created * 1000).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })}</span>
                {typeof m.meta?.model === "string" && <span className="font-mono">{String(m.meta.provider)} / {m.meta.model}</span>}
                {m.meta?.engine === "planner" && <span>planer offline</span>}
                {Array.isArray(m.meta?.attachments) && (m.meta.attachments as string[]).length > 0 && <span>📎 {(m.meta.attachments as string[]).length}</span>}
                {typeof m.meta?.selected === "string" && <span>→ {String(m.meta.selected)}</span>}
              </div>
            </div>
          </div>
        ))}
        {running && (
          <div className="flex gap-2 items-start">
            <div className="w-6 h-6 shrink-0 rounded-md flex items-center justify-center bg-accent/15 text-accent"><Cpu size={13} className="pulse-dot" /></div>
            <div className="text-dim">
              <div>{job?.stage || "Praca…"}</div>
              {currentOp?.label && <div className="text-xxs text-mute font-mono mt-0.5">{currentOp.type === "step_start" ? "▶" : "✓"} {currentOp.label}</div>}
              {[...live].reverse().find((e) => e.type === "assistant")?.text && (
                <div className="mt-1 text-fg whitespace-pre-wrap">{[...live].reverse().find((e) => e.type === "assistant")?.text}</div>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="p-2 border-t border-line">
        {(pending.length > 0 || selected) && (
          <div className="flex flex-wrap gap-1 mb-1.5">
            {selected && (
              <label className="chip cursor-pointer" title="Polecenie dotyczy tylko zaznaczonego elementu">
                <input type="checkbox" checked={scopeToSel} onChange={(e) => setScopeToSel(e.target.checked)} className="mr-0.5" />zaznaczony: <b className="text-fg">{selected}</b>
              </label>
            )}
            {pending.map((id) => { const a = attachments.find((x) => x.id === id); return a ? (
              <span key={id} className="chip !pl-0.5"><AttachmentThumb id={a.id} url={a.url} mime={a.mime} className="w-4 h-4 rounded object-cover" />{a.filename.slice(0, 18)}
                <button onClick={() => useStore.setState({ pendingAttach: pending.filter((x) => x !== id) })}><X size={10} /></button></span>) : null; })}
          </div>
        )}
        <div className="relative">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder={running ? "Trwa budowanie…" : "Opisz model lub zmianę… (Enter — wyślij, Shift+Enter — nowa linia)"}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            className="w-full resize-none rounded-lg bg-bg border border-line2 focus:border-accent outline-none px-2.5 py-2 pr-10 placeholder:text-mute leading-relaxed" />
        </div>
        <div className="flex items-center gap-1 mt-1">
          <button className="icon-btn" title="Dołącz obraz (PNG, JPG, WEBP, SVG)" onClick={() => fileRef.current?.click()}><Paperclip size={14} /></button>
          <input ref={fileRef} type="file" multiple accept=".png,.jpg,.jpeg,.webp,.svg" className="hidden"
            onChange={async (e) => { if (e.target.files) { const ids = await upload(e.target.files); useStore.setState({ pendingAttach: [...useStore.getState().pendingAttach, ...ids] }); e.target.value = ""; } }} />
          <span className="text-xxs text-mute flex-1 truncate">{providerId === "offline" ? "Planer offline" : `${prov?.name ?? ""} · ${model}`}</span>
          {running ? (
            <button className="btn !border-err/60 !text-err" onClick={() => ctl("jobs/stop")}><Square size={12} fill="currentColor" /> Stop</button>
          ) : (
            <button className="btn-primary" disabled={!text.trim()} onClick={() => send()}><Send size={13} /> Wyślij</button>
          )}
        </div>
      </div>
    </div>
  );
}
