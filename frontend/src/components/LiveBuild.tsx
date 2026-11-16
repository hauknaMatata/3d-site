import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Circle, Loader2, Play, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { JobLogEntry } from "../lib/types";

export default function LiveBuild({ collapsed, toggle }: { collapsed: boolean; toggle: () => void }) {
  const job = useStore((s) => s.job);
  const live = useStore((s) => s.live);
  const project = useStore((s) => s.project);
  const wsState = useStore((s) => s.wsState);
  const [engines, setEngines] = useState<Record<string, boolean>>({});
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { api.get<{ engines: Record<string, boolean> }>("/api/capabilities").then((c) => setEngines(c.engines)).catch(() => setEngines({})); }, []);
  useEffect(() => { logRef.current?.scrollTo({ top: 1e9 }); }, [live.length]);

  const running = job?.status === "running";
  const stepsDone = live.filter((e) => e.type === "step_done" && !e.query).length;
  const pct = job?.engine === "planner" && job.step_total ? Math.round(((job.status === "done" ? job.step_total : Math.max(0, (job.step_index || 1) - 1)) / job.step_total) * 100) : null;
  const ctl = (p: string) => project && api.post(`/api/projects/${project.id}/jobs/${p}`).then((j) => useStore.setState({ job: j as typeof job })).catch((e) => useStore.getState().notify(e.message, "err"));

  return (
    <div className="h-full flex flex-col bg-panel border-t border-line">
      <div className="flex items-center h-8 px-3 border-b border-line gap-3 select-none">
        <button onClick={toggle} className="flex items-center gap-1.5 text-xxs uppercase tracking-wider text-dim font-medium hover:text-fg">
          {collapsed ? <ChevronUp size={13} /> : <ChevronDown size={13} />} Live Build
        </button>
        {job && (
          <span className={`flex items-center gap-1.5 text-xxs ${running ? "text-accent" : job.status === "error" ? "text-err" : job.status === "stopped" ? "text-warn" : "text-ok"}`}>
            {running ? <Loader2 size={12} className="animate-spin" /> : job.status === "error" ? <AlertTriangle size={12} /> : job.status === "stopped" ? <Square size={11} /> : <CheckCircle2 size={12} />}
            {{ running: "W toku", done: "Zakończono", error: "Błąd", stopped: "Zatrzymano" }[job.status]}
            {job.stage && <span className="text-dim">· {job.stage}</span>}
          </span>
        )}
        <div className="flex-1 flex items-center gap-2 min-w-0">
          {job && (
            <div className="flex-1 max-w-sm h-1 rounded bg-line overflow-hidden" title={pct != null ? `${pct}%` : `${stepsDone} operacji`}>
              {pct != null ? <div className="h-full bg-accent transition-all duration-300" style={{ width: `${pct}%` }} />
                : <div className={`h-full bg-accent ${running ? "w-1/3 animate-[slide_1.2s_ease-in-out_infinite]" : "w-full"}`} style={running ? { animation: "pulse-dot 1.1s infinite" } : undefined} />}
            </div>
          )}
          {job && <span className="text-xxs text-mute font-mono whitespace-nowrap">{job.engine === "planner" ? `${Math.min(job.step_index, job.step_total)}/${job.step_total} kroków` : `${stepsDone} operacji · tura ${job.step_index}`}</span>}
        </div>
        {running && <button className="btn !h-6 !text-err" onClick={() => ctl("stop")}><Square size={11} fill="currentColor" /> Stop</button>}
        {job?.resumable && !running && <button className="btn !h-6" onClick={() => ctl("resume")} title={job.engine === "planner" && job.status === "error" ? "Pomija nieudany krok i kontynuuje" : "Kontynuuj"}><Play size={11} /> Wznów</button>}
        <span className="flex items-center gap-1.5 text-xxs text-mute" title="Status połączenia WebSocket i silników 3D">
          <span className={`w-1.5 h-1.5 rounded-full ${wsState === "open" ? "bg-ok" : wsState === "connecting" ? "bg-warn pulse-dot" : "bg-err"}`} /> WS
          {Object.entries(engines).map(([k, v]) => (
            <span key={k} className={`hidden xl:inline font-mono ${v ? "text-mute" : "text-err line-through"}`} title={k}>{SHORT[k] || k}</span>
          ))}
        </span>
      </div>
      {!collapsed && (
        <div className="flex-1 flex min-h-0">
          <div className="w-56 shrink-0 border-r border-line overflow-y-auto py-1.5">
            {(job?.stages || []).map((s, i, arr) => {
              const active = running && s === job?.stage;
              const done = !active && (i < arr.length - 1 || job?.status === "done");
              const failed = job?.status === "error" && i === arr.length - 1;
              return (
                <div key={s} className="flex items-center gap-2 px-3 h-6">
                  {failed ? <AlertTriangle size={12} className="text-err" /> : active ? <Loader2 size={12} className="text-accent animate-spin" /> : done ? <CheckCircle2 size={12} className="text-ok" /> : <Circle size={12} className="text-mute" />}
                  <span className={`truncate ${active ? "text-fg" : "text-dim"}`}><span className="text-mute font-mono mr-1">{i + 1}.</span>{s}</span>
                </div>
              );
            })}
            {!job && <div className="text-mute px-3 py-2">Brak zadań. Etapy pojawią się podczas budowania.</div>}
          </div>
          <div ref={logRef} className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden font-mono text-xxs py-1">
            {live.map((e, i) => <LogLine key={i} e={e} />)}
            {job?.status === "error" && <div className="px-3 py-1 text-err">✖ {job.error}</div>}
          </div>
        </div>
      )}
    </div>
  );
}

const SHORT: Record<string, string> = { opencascade_cadquery: "OCC", trimesh: "mesh", manifold_boolean: "bool", lib3mf: "3MF", usd_core: "USD", assimp_fbx: "FBX", mesh_simplification: "LOD" };

function LogLine({ e }: { e: JobLogEntry }) {
  const t = new Date((e.ts || 0) * 1000).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const base = "px-3 leading-5 flex gap-2 min-w-0 whitespace-nowrap";
  if (e.type === "stage") return <div className={`${base} text-accent pt-1`}><span className="text-mute">{t}</span>■ {e.name}</div>;
  if (e.type === "step_start") return <div className={`${base} text-dim`}><span className="text-mute">{t}</span>▶ {e.label} <span className="text-mute">{e.tool}</span></div>;
  if (e.type === "step_done") return (
    <div className={`${base} ${e.query ? "text-[#9fc6ff]" : "text-ok"}`}><span className="text-mute">{t}</span>✓ {e.label}
      {e.ms != null && <span className="text-mute">{e.ms} ms</span>}
      {e.query && <span className="text-dim truncate min-w-0" title={typeof e.result === "string" ? e.result : JSON.stringify(e.result)}>{typeof e.result === "string" ? e.result : JSON.stringify(e.result)}</span>}
    </div>
  );
  if (e.type === "step_error") return <div className={`${base} text-err`}><span className="text-mute">{t}</span>✖ {e.tool ? `${e.tool}: ` : ""}{e.error}</div>;
  if (e.type === "assistant") return <div className={`${base.replace("whitespace-nowrap", "")} text-fg`}><span className="text-mute">{t}</span>AI: <span className="whitespace-pre-wrap font-sans min-w-0">{e.text}</span></div>;
  return <div className={`${base} ${e.level === "error" ? "text-err" : e.level === "warn" ? "text-warn" : "text-dim"}`}><span className="text-mute">{t}</span>{e.msg}</div>;
}
