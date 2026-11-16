import { AlertTriangle, ArrowDown, Copy, Plug, ArrowUp, Check, CheckCircle2, ChevronDown, ChevronRight, CircleDashed, FlaskConical, Gavel, Hammer, Loader2, MessagesSquare, Plus, RotateCcw, Search, Settings2, ShieldCheck, Sparkles, Trash2, Undo2, Users, X, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { absUrl, api } from "../lib/api";
import { useStore } from "../lib/store";
import type { AgentStatus, ProviderInfo, QAReport, Snapshot, TeamConfig, TeamIssue, TeamMemberState, TeamMessage } from "../lib/types";

export const STATUS_META: Record<AgentStatus, { label: string; cls: string; active: boolean }> = {
  idle: { label: "Idle", cls: "text-mute border-line2", active: false },
  thinking: { label: "Thinking", cls: "text-[#c9a7ff] border-[#6b4fa0]", active: true },
  analyzing: { label: "Analyzing", cls: "text-[#9fc6ff] border-[#2f4a74]", active: true },
  modeling: { label: "Modeling", cls: "text-accent border-accent/60", active: true },
  reviewing: { label: "Reviewing", cls: "text-[#f0b46a] border-[#6b4a24]", active: true },
  fixing: { label: "Fixing", cls: "text-warn border-warn/60", active: true },
  completed: { label: "Completed", cls: "text-ok border-ok/50", active: false },
  error: { label: "Error", cls: "text-err border-err/60", active: false },
};
const ROLE_SHORT: Record<string, string> = { lead: "Lead Designer", engineer: "3D Engineer", quality: "Texture & Quality", reviewer: "Reviewer" };

export function useTeamConfig() {
  const [cfg, setCfg] = useState<TeamConfig | null>(null);
  const load = () => api.get<TeamConfig>("/api/team/config").then(setCfg).catch(() => setCfg(null));
  useEffect(() => { load(); window.addEventListener("ai3d:team", load); return () => window.removeEventListener("ai3d:team", load); }, []);
  const save = async (patch: Partial<TeamConfig>) => {
    if (!cfg) return;
    try {
      const { roles: _r, effective_roles: _e, ...rest } = { ...cfg, ...patch };
      void _r; void _e;
      const n = await api.put<TeamConfig>("/api/team/config", rest);
      setCfg(n); window.dispatchEvent(new Event("ai3d:team"));
    } catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  return { cfg, save, reload: load };
}

function Avatar({ name, color, active }: { name: string; color: string; active?: boolean }) {
  return (
    <span className="relative inline-flex shrink-0">
      <span className="w-7 h-7 rounded-lg flex items-center justify-center text-[11px] font-semibold text-white" style={{ background: color }}>
        {name.slice(0, 2).toUpperCase()}
      </span>
      {active && <span className="absolute -inset-0.5 rounded-[10px] border-2 animate-pulse" style={{ borderColor: color }} />}
    </span>
  );
}

export default function TeamPanel() {
  const { cfg, save } = useTeamConfig();
  const team = useStore((s) => s.team);
  const job = useStore((s) => s.job);
  const [tab, setTab] = useState<"discussion" | "fixes" | "qa">("discussion");
  const [configOpen, setConfigOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  useEffect(() => { api.get<{ providers: ProviderInfo[] }>("/api/providers").then((r) => setProviders(r.providers)).catch(() => undefined); }, [configOpen]);
  const running = job?.status === "running" && job.engine === "team";
  const [mcpStatus, setMcpStatus] = useState<{ id: string; connected: boolean }[]>([]);
  const hasMcp = !!cfg?.members.some((m) => m.kind === "mcp" && m.enabled);
  useEffect(() => {
    if (!hasMcp) return;
    const load = () => api.get<{ id: string; connected: boolean }[]>("/api/team/mcp/agents").then(setMcpStatus).catch(() => undefined);
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [hasMcp]);
  const issues = useStore((s) => s.teamIssues);
  const pending = issues.filter((i) => i.status === "pending_user").length;

  // live member states during/after a job, otherwise the configured team with its effective roles
  const members: TeamMemberState[] = useMemo(() => {
    const live = team?.members || [];
    return (cfg?.members.filter((m) => m.enabled) || []).map((m) => {
      const l = live.find((x) => x.id === m.id);
      const prov = providers.find((p) => p.id === m.provider_id);
      const mcp = m.kind === "mcp";
      const conn = mcpStatus.find((x) => x.id === m.id)?.connected ?? false;
      return l ? { ...l, color: m.color, connected: mcp ? (l.connected ?? conn) : null } : {
        id: m.id, kind: m.kind, connected: mcp ? conn : null, name: m.label || m.model || prov?.name || "?", provider_id: m.provider_id,
        model: mcp ? `MCP · ${conn ? "połączony" : "niepołączony"}` : m.model || prov?.default_model || "",
        color: m.color, roles: cfg?.effective_roles[m.id] || [], failed: false, status: "idle", task: "", last_message: "",
        issues: 0, ops: 0, tokens_in: 0, tokens_out: 0, calls: 0, errors: 0,
      } as TeamMemberState;
    });
  }, [cfg, team, providers, mcpStatus]);

  if (!cfg) return <div className="p-4 text-mute">Ładowanie…</div>;
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="px-3 py-2 border-b border-line space-y-2">
        <div className="flex items-center gap-1.5">
          <div className="flex p-0.5 rounded-md bg-bg border border-line2">
            {(["auto", "review", "manual"] as const).map((m) => (
              <button key={m} disabled={running} onClick={() => save({ mode: m })}
                title={m === "auto" ? "Agenty same wprowadzają bezpieczne, odwracalne poprawki" : m === "review" ? "Każda poprawka wymaga Twojej zgody" : "Sam wybierasz agenta i zadanie"}
                className={`h-6 px-2 rounded text-xxs uppercase tracking-wider ${cfg.mode === m ? "bg-accent text-white" : "text-dim hover:text-fg"}`}>{m}</button>
            ))}
          </div>
          <label className="flex items-center gap-1 text-xxs text-dim" title="Agenty dyskutują nad krytyką planu przed decyzją Leada">
            <input type="checkbox" checked={cfg.debate} disabled={running} onChange={(e) => save({ debate: e.target.checked })} />Debate
          </label>
          <div className="flex-1" />
          <button className="icon-btn" title="Konfiguracja zespołu" onClick={() => setConfigOpen(true)}><Settings2 size={14} /></button>
        </div>
        {team?.budget && (
          <div className="flex gap-3 text-xxs text-mute font-mono">
            <span title="Tokeny zgłoszone przez dostawców">{team.budget.tokens.toLocaleString("pl-PL")} tok{cfg.max_tokens ? ` / ${cfg.max_tokens.toLocaleString("pl-PL")}` : ""}</span>
            <span>{team.budget.seconds}s{cfg.max_seconds ? ` / ${cfg.max_seconds}s` : ""}</span>
            {team.budget.cost > 0 && <span>${team.budget.cost.toFixed(3)}</span>}
            {team.round ? <span>runda {team.round}/{cfg.max_rounds}</span> : null}
          </div>
        )}
      </div>

      <div className="px-2 py-2 border-b border-line space-y-1.5 max-h-[42%] overflow-y-auto">
        {!members.length && (
          <div className="text-center py-4 px-3">
            <Users size={18} className="mx-auto text-mute" />
            <div className="text-dim mt-1">Brak agentów w zespole.</div>
            <button className="btn-primary mt-2" onClick={() => setConfigOpen(true)}><Plus size={13} /> Dodaj modele do zespołu</button>
            <div className="text-xxs text-mute mt-2">Bez kluczy API: dodaj „Agenta MCP” i podłącz Claude / Gemini CLI / ChatGPT przez MCP.</div>
          </div>
        )}
        {members.map((m) => <MemberCard key={m.id} m={m} />)}
        {hasMcp && <McpHelp names={cfg.members.filter((m) => m.kind === "mcp" && m.enabled).map((m) => m.label)} />}
      </div>

      <div className="flex border-b border-line px-1">
        {([["discussion", "Dyskusja", <MessagesSquare key="d" size={12} />], ["fixes", `Poprawki${pending ? ` (${pending})` : ""}`, <Hammer key="f" size={12} />],
           ["qa", "Jakość", <ShieldCheck key="q" size={12} />]] as const).map(([k, l, ic]) => (
          <button key={k} onClick={() => setTab(k)} className={`tab flex items-center gap-1 ${tab === k ? "tab-on" : ""} ${k === "fixes" && pending ? "!text-warn" : ""}`}>{ic}{l}</button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "discussion" && <Discussion />}
        {tab === "fixes" && <Fixes />}
        {tab === "qa" && <Quality />}
      </div>
      {configOpen && <TeamConfigDialog cfg={cfg} providers={providers} save={save} onClose={() => setConfigOpen(false)} />}
    </div>
  );
}

function MemberCard({ m }: { m: TeamMemberState }) {
  const meta = STATUS_META[m.status] || STATUS_META.idle;
  const [open, setOpen] = useState(false);
  return (
    <div className={`rounded-lg border px-2.5 py-2 transition-colors ${meta.active ? "bg-panel2 border-line2" : "border-line"} ${m.failed ? "opacity-70" : ""}`}>
      <div className="flex items-center gap-2">
        <Avatar name={m.name} color={m.color} active={meta.active} />
        <div className="min-w-0 flex-1">
          <div className="font-medium truncate">{m.name}</div>
          <div className="flex flex-wrap gap-1 my-0.5">
            {m.roles.map((r) => <span key={r} className="chip !h-4 !px-1 whitespace-nowrap" style={{ color: m.color, borderColor: m.color + "66" }}>{ROLE_SHORT[r] || r}</span>)}</div>
          <div className="text-xxs text-mute font-mono truncate flex items-center gap-1">
            {m.kind === "mcp" && <span className={`w-1.5 h-1.5 rounded-full ${m.connected ? "bg-ok" : "bg-mute"}`} />}{m.model}</div>
        </div>
        <span className={`chip !h-5 ${meta.cls} flex items-center gap-1`}>
          {meta.active ? <Loader2 size={10} className="animate-spin" /> : m.status === "error" ? <XCircle size={10} /> : m.status === "completed" ? <CheckCircle2 size={10} /> : <CircleDashed size={10} />}
          {meta.label}
        </span>
      </div>
      {(m.task || m.last_message) && (
        <button className="w-full text-left mt-1.5" onClick={() => setOpen(!open)}>
          {m.task && <div className="text-xxs text-dim">▶ {m.task}</div>}
          {m.last_message && <div className={`text-xxs text-mute ${open ? "whitespace-pre-wrap" : "line-clamp-2"}`}>„{m.last_message}”</div>}
        </button>
      )}
      <div className="flex gap-3 mt-1.5 text-xxs text-mute font-mono">
        <span title="Zgłoszone problemy">⚑ {m.issues}</span><span title="Wykonane operacje">⚙ {m.ops}</span>
        <span title="Tokeny wejście/wyjście">{m.tokens_in + m.tokens_out > 0 ? `${(m.tokens_in / 1000).toFixed(1)}k/${(m.tokens_out / 1000).toFixed(1)}k tok` : "— tok"}</span>
        {m.errors > 0 && <span className="text-err" title={m.last_error}>✖ {m.errors}</span>}
      </div>
    </div>
  );
}

function CopyBlock({ text }: { text: string }) {
  return (
    <div className="relative group">
      <pre className="text-xxs font-mono bg-bg border border-line rounded p-1.5 pr-7 whitespace-pre-wrap break-all">{text}</pre>
      <button className="icon-btn !w-5 !h-5 absolute top-1 right-1" title="Kopiuj"
        onClick={() => { navigator.clipboard?.writeText(text); useStore.getState().notify("Skopiowano", "ok"); }}><Copy size={11} /></button>
    </div>
  );
}

function McpHelp({ names }: { names: string[] }) {
  const [open, setOpen] = useState(false);
  const [app, setApp] = useState<"code" | "desktop" | "gemini" | "chatgpt">("code");
  const url = (() => { const u = absUrl("/mcp/"); return u.startsWith("http") ? u : window.location.origin.replace(":5173", ":8000") + "/mcp/"; })();
  const prompt = (n: string) => `Dołącz do zespołu AI 3D Studio: wywołaj narzędzie team_join z agent_name="${n}" i postępuj według zwróconych instrukcji — w pętli wywołuj team_wait_for_task, wykonuj zadania (modelowanie tylko przez team_run_operation) i oddawaj wyniki przez team_submit. Nie przerywaj, dopóki nie dostaniesz statusu "stop".`;
  return (
    <div className="rounded-lg border border-accent/40 bg-accent/5">
      <button className="w-full flex items-center gap-1.5 px-2.5 py-2 text-left" onClick={() => setOpen(!open)}>
        <Plug size={13} className="text-accent" /><span className="font-medium">Jak podłączyć agentów MCP (bez kluczy API)</span>
        <span className="ml-auto">{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</span>
      </button>
      {open && (
        <div className="px-2.5 pb-2.5 space-y-2">
          <div className="text-xxs text-dim">Agent działa w Twojej aplikacji AI (na Twojej subskrypcji / logowaniu) i łączy się do tego studia. Adres serwera MCP:</div>
          <CopyBlock text={url} />
          <div className="flex flex-wrap gap-1">
            {([["code", "Claude Code"], ["desktop", "Claude Desktop"], ["gemini", "Gemini CLI"], ["chatgpt", "ChatGPT"]] as const).map(([k, l]) => (
              <button key={k} onClick={() => setApp(k)} className={`btn !h-6 text-xxs ${app === k ? "!border-accent" : ""}`}>{l}</button>
            ))}
          </div>
          {app === "code" && <><div className="text-xxs text-dim">1. W terminalu:</div><CopyBlock text={`claude mcp add --transport http ai3d ${url}`} /></>}
          {app === "desktop" && <><div className="text-xxs text-dim">1. Settings → Developer → Edit Config (wymaga Node.js), potem restart Claude Desktop:</div>
            <CopyBlock text={JSON.stringify({ mcpServers: { ai3d: { command: "npx", args: ["-y", "mcp-remote", url] } } }, null, 2)} /></>}
          {app === "gemini" && <><div className="text-xxs text-dim">1. Dopisz do ~/.gemini/settings.json i uruchom <span className="font-mono">gemini</span>:</div>
            <CopyBlock text={JSON.stringify({ mcpServers: { ai3d: { httpUrl: url } } }, null, 2)} /></>}
          {app === "chatgpt" && <div className="text-xxs text-dim">1. ChatGPT łączy się tylko z publicznym adresem HTTPS: wystaw backend (hosting) albo tunel, np. <span className="font-mono">cloudflared tunnel --url http://localhost:8000</span>, i dodaj w ChatGPT własny connector MCP (tryb deweloperski) z adresem <span className="font-mono">https://…/mcp/</span>. Dostępność zależy od Twojego planu ChatGPT.</div>}
          <div className="text-xxs text-dim">2. Wklej do tej aplikacji polecenie (dla każdego agenta jego nazwa):</div>
          {names.map((n) => <CopyBlock key={n} text={prompt(n)} />)}
          <div className="text-xxs text-mute">3. Kropka przy agencie zmieni się na zieloną. Wpisz polecenie poniżej i kliknij „Zespół”. Agent musi pracować w pętli — jeśli aplikacja AI przerwie, napisz jej „kontynuuj”.</div>
        </div>
      )}
    </div>
  );
}

const KIND_META: Record<string, { label: string; icon: JSX.Element }> = {
  plan: { label: "Plan", icon: <Sparkles size={11} /> }, critique: { label: "Krytyka", icon: <Search size={11} /> },
  debate: { label: "Debata", icon: <MessagesSquare size={11} /> }, decision: { label: "Decyzja", icon: <Gavel size={11} /> },
  build: { label: "Wykonanie", icon: <Hammer size={11} /> }, review: { label: "Przegląd", icon: <ShieldCheck size={11} /> },
  fix_check: { label: "Weryfikacja", icon: <Check size={11} /> }, summary: { label: "Podsumowanie", icon: <CheckCircle2 size={11} /> },
  error: { label: "Błąd", icon: <AlertTriangle size={11} /> }, info: { label: "Info", icon: <CircleDashed size={11} /> },
};

function Discussion() {
  const msgs = useStore((s) => s.teamMessages);
  const team = useStore((s) => s.team);
  const job = useStore((s) => s.job);
  const shown = job?.engine === "team" ? msgs.filter((m) => m.job_id === job.id) : msgs;
  if (!shown.length) return <div className="text-mute text-center px-6 py-8">Dyskusja zespołu pojawi się tutaj — plan, niezależne krytyki, debata, decyzje Leada, przeglądy i poprawki.</div>;
  return (
    <div className="p-2 space-y-2">
      {!!team?.conflicts?.length && (
        <div className="rounded-lg border border-warn/40 bg-warn/5 p-2">
          <div className="text-xxs uppercase tracking-wider text-warn mb-1">Sprzeczne opinie</div>
          {team.conflicts.map((c, i) => <div key={i} className="text-xxs"><b>{c.agent}</b> ✕ {c.item}: <span className="text-dim">{c.argument}</span></div>)}
        </div>
      )}
      {shown.map((m, i) => (
        <div key={m.id}>
          {(i === 0 || shown[i - 1].phase !== m.phase || shown[i - 1].round !== m.round) && (
            <div className="text-xxs uppercase tracking-wider text-mute mt-2 mb-1">{m.phase}{m.round ? ` · runda ${m.round}` : ""}</div>
          )}
          <MessageCard m={m} />
        </div>
      ))}
    </div>
  );
}

function MessageCard({ m }: { m: TeamMessage }) {
  const [open, setOpen] = useState(false);
  const meta = KIND_META[m.kind] || KIND_META.info;
  const d = m.data as Record<string, unknown> | null;
  const color = m.color || (m.agent_id === "system" ? "#646c76" : "#5b8cff");
  return (
    <div className="rounded-lg border border-line bg-panel2/40 slide-in overflow-hidden" style={{ borderLeft: `3px solid ${color}` }}>
      <button className="w-full text-left px-2.5 py-2" onClick={() => setOpen(!open)}>
        <div className="flex items-center gap-1.5 text-xxs">
          <b style={{ color }}>{m.agent_name}</b>
          {m.role !== "system" && <span className="text-mute">{m.role.split(",").map((r) => ROLE_SHORT[r] || r).join(" + ")}</span>}
          <span className={`chip !h-4 ml-auto ${m.kind === "error" ? "!text-err" : ""}`}>{meta.icon}{meta.label}</span>
          {d && (open ? <ChevronDown size={11} /> : <ChevronRight size={11} />)}
        </div>
        <div className="mt-1 whitespace-pre-wrap leading-relaxed">{m.content}</div>
      </button>
      {open && d && <div className="px-2.5 pb-2"><DataView kind={m.kind} d={d} /></div>}
    </div>
  );
}

function DataView({ kind, d }: { kind: string; d: Record<string, unknown> }) {
  const arr = (k: string) => (Array.isArray(d[k]) ? (d[k] as Record<string, unknown>[]) : []);
  if (kind === "critique" || kind === "review") {
    return (
      <div className="space-y-1">
        {typeof d.verdict === "string" && <div className="text-xxs">Werdykt: <b>{d.verdict}</b>{d.confidence != null && ` · pewność ${String(d.confidence)}%`}</div>}
        {arr("issues").map((it, i) => (
          <div key={i} className="text-xxs border-l-2 border-line2 pl-2">
            <span className={sevCls(String(it.severity))}>{String(it.severity)}</span> {it.target ? <b>{String(it.target)}: </b> : null}{String(it.problem || "")}
            {it.proposal ? <div className="text-mute">→ {String(it.proposal)}</div> : null}
            {it.fix ? <div className="font-mono text-mute">fix: {JSON.stringify(it.fix)}</div> : null}
          </div>
        ))}
        {d.ai_scores ? <div className="text-xxs text-mute">Oceny AI (opinia): {Object.entries(d.ai_scores as Record<string, number>).map(([k, v]) => `${k} ${v}`).join(" · ")}</div> : null}
      </div>
    );
  }
  if (kind === "debate") {
    return <div className="space-y-1">{arr("stances").map((s, i) => (
      <div key={i} className="text-xxs"><span className={s.stance === "disagree" ? "text-err" : s.stance === "partial" ? "text-warn" : "text-ok"}>{String(s.stance)}</span> {String(s.item)} — <span className="text-dim">{String(s.argument)}</span></div>
    ))}</div>;
  }
  if (kind === "decision") {
    return (
      <div className="space-y-1">
        {arr("decisions").map((x, i) => (
          <div key={i} className="text-xxs"><span className={["accept", "approve"].includes(String(x.decision)) ? "text-ok" : x.decision === "modify" ? "text-warn" : "text-err"}>{String(x.decision)}</span> {String(x.item || x.issue_id)} — <span className="text-dim">{String(x.reason)}</span></div>
        ))}
        {arr("conflicts_resolved").map((x, i) => <div key={i} className="text-xxs text-warn">⚖ {String(x.topic)}: {String(x.ruling)} <span className="text-mute">({String(x.sides)})</span></div>)}
      </div>
    );
  }
  return <pre className="text-xxs text-mute font-mono whitespace-pre-wrap max-h-60 overflow-auto">{JSON.stringify(d, null, 1)}</pre>;
}

const sevCls = (s: string) => (s === "critical" ? "text-err font-semibold" : s === "major" ? "text-warn" : "text-dim");
const ORDER: Record<string, number> = { pending_user: 0, applied: 1, approved: 2, user_approved: 2, validated: 3, disputed: 3, proposed: 3,
  failed: 4, reverted: 4, rejected: 6, user_rejected: 6, invalid: 7 };
const ISSUE_STATUS: Record<string, string> = {
  proposed: "zaproponowana", validated: "zweryfikowana", disputed: "zakwestionowana", approved: "zatwierdzona", rejected: "odrzucona",
  invalid: "odrzucona przez walidację", pending_user: "czeka na Ciebie", user_approved: "zatwierdzona przez Ciebie",
  user_rejected: "odrzucona przez Ciebie", applied: "zastosowana", failed: "nieudana", reverted: "cofnięta",
};

function Fixes() {
  const issues = useStore((s) => s.teamIssues);
  const team = useStore((s) => s.team);
  const project = useStore((s) => s.project);
  const job = useStore((s) => s.job);
  const running = job?.status === "running";
  const act = async (i: TeamIssue, what: "approve" | "reject" | "revert" | "restore") => {
    if (!project) return;
    try {
      if (what === "revert") { const r = await api.post<{ snapshot: Snapshot }>(`/api/projects/${project.id}/team/issues/${i.id}/revert`); useStore.getState().setSnap(r.snapshot); }
      else if (what === "restore" && i.version_before) { const r = await api.post<{ snapshot: Snapshot }>(`/api/projects/${project.id}/versions/${i.version_before}/restore`); useStore.getState().setSnap(r.snapshot); useStore.getState().notify("Przywrócono wersję sprzed poprawek", "ok"); }
      else await api.post(`/api/projects/${project.id}/team/issues/${i.id}/decide`, { approve: what === "approve" });
      useStore.getState().refreshTeam();
    } catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  return (
    <div className="p-2 space-y-2">
      {team?.comparisons?.map((c) => (
        <div key={c.round} className="rounded-lg border border-line2 p-2">
          <div className="text-xxs uppercase tracking-wider text-mute mb-1">Porównanie przed / po — runda {c.round} ({c.applied} poprawek)</div>
          <div className="flex items-center gap-2 text-xs"><span className="font-mono">{c.before.score ?? "—"}</span><span className="text-mute">→</span><span className="font-mono text-ok">{c.after.score ?? "—"}</span><span className="text-xxs text-mute">wynik testów technicznych</span></div>
          <div className="mt-1 space-y-0.5">
            {Object.entries(c.after.checks).filter(([k, v]) => c.before.checks[k]?.status !== v.status).map(([k, v]) => (
              <div key={k} className="text-xxs font-mono"><span className="text-dim">{k}</span> {c.before.checks[k]?.status} ({String(c.before.checks[k]?.value)}) → <span className={v.status === "pass" ? "text-ok" : "text-warn"}>{v.status}</span> ({String(v.value)})</div>
            ))}
          </div>
        </div>
      ))}
      {!issues.length && <div className="text-mute text-center px-6 py-8">Brak wykrytych problemów.</div>}
      {[...issues].sort((a, b) => (ORDER[a.status] ?? 5) - (ORDER[b.status] ?? 5) || b.created - a.created).map((i) => (
        <div key={i.id} className={`rounded-lg border p-2 ${i.status === "pending_user" ? "border-warn/60 bg-warn/5" : "border-line"}`}>
          <div className="flex items-center gap-1.5 text-xxs">
            <span className={sevCls(i.severity)}>{i.severity}</span>
            <b className="truncate">{i.target || "—"}</b>
            <span className="text-mute">· {i.author}</span>
            <span className={`chip !h-4 ml-auto ${i.status === "applied" ? "!text-ok" : i.status === "pending_user" ? "!text-warn" : ["rejected", "invalid", "failed", "user_rejected"].includes(i.status) ? "!text-err" : ""}`}>{ISSUE_STATUS[i.status] || i.status}</span>
          </div>
          <div className="mt-1">{i.problem}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {i.backed_by_test
              ? <span className="chip !h-4 !text-ok !border-ok/40" title="Problem potwierdzony pomiarem w testach technicznych"><FlaskConical size={10} /> test: {i.qa_check}</span>
              : <span className="chip !h-4" title="Opinia modelu AI — nie potwierdzona pomiarem">opinia AI · niezweryfikowane</span>}
            {i.category && <span className="chip !h-4">{i.category}</span>}
          </div>
          {i.fix && <div className="mt-1 font-mono text-xxs text-dim break-all">⚙ {i.fix.tool}({JSON.stringify(i.fix.args)}){i.fix.description ? ` — ${i.fix.description}` : ""}</div>}
          {(i.reviews || []).map((r, k) => <div key={k} className="text-xxs mt-0.5"><span className={r.approve ? "text-ok" : "text-err"}>{r.approve ? "✓" : "✕"} {r.by}</span>: <span className="text-dim">{r.reason}</span></div>)}
          {i.decision?.reason && <div className="text-xxs mt-0.5"><Gavel size={10} className="inline" /> {i.decision.by}: <span className="text-dim">{i.decision.reason}</span></div>}
          {i.decision?.engine_error && <div className="text-xxs mt-0.5 text-err">Silnik: {i.decision.engine_error}</div>}
          <div className="flex gap-1 mt-1.5">
            {i.status === "pending_user" && <>
              <button className="btn-primary !h-6" onClick={() => act(i, "approve")}><Check size={12} /> Zatwierdź</button>
              <button className="btn !h-6" onClick={() => act(i, "reject")}><X size={12} /> Odrzuć</button></>}
            {i.status === "applied" && <button className="btn !h-6" disabled={running} onClick={() => act(i, "revert")}><Undo2 size={12} /> Cofnij poprawkę</button>}
            {i.status === "applied" && i.version_before && <button className="btn !h-6" disabled={running} title="Przywraca cały model do wersji sprzed rundy poprawek" onClick={() => act(i, "restore")}><RotateCcw size={12} /> Wersja sprzed</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

function Quality() {
  const qa = useStore((s) => s.qa);
  const team = useStore((s) => s.team);
  const project = useStore((s) => s.project);
  const [busy, setBusy] = useState(false);
  const runQA = async () => {
    if (!project) return;
    setBusy(true);
    try { useStore.setState({ qa: await api.post<QAReport>(`/api/projects/${project.id}/team/qa`) }); } catch (e) { useStore.getState().notify((e as Error).message, "err"); } finally { setBusy(false); }
  };
  const icon = (s: string) => (s === "pass" ? <CheckCircle2 size={12} className="text-ok" /> : s === "warn" ? <AlertTriangle size={12} className="text-warn" />
    : s === "fail" ? <XCircle size={12} className="text-err" /> : s === "unchecked" ? <CircleDashed size={12} className="text-mute" /> : <span className="w-3 text-center text-mute">i</span>);
  const ai = team?.ai_scores || {};
  return (
    <div className="p-2 space-y-3">
      <div className="flex items-center gap-2">
        <div className="flex-1">
          <div className="label flex items-center gap-1"><FlaskConical size={11} /> Testy techniczne (pomiar kodem)</div>
          <div className="text-xl font-semibold font-mono">{qa?.score ?? "—"}<span className="text-xs text-mute">/100</span>
            {!!qa?.unchecked.length && <span className="ml-2 text-xxs font-sans font-normal text-warn">{qa.unchecked.length} niesprawdzonych — nie wliczone</span>}</div>
        </div>
        <button className="btn" onClick={runQA} disabled={busy}>{busy ? <Loader2 size={12} className="animate-spin" /> : <FlaskConical size={12} />} Uruchom testy</button>
      </div>
      {qa ? (
        <>
          <div className="space-y-1">
            {Object.entries(qa.categories).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2 text-xxs">
                <span className="w-28 truncate text-dim">{k}</span>
                <div className="flex-1 h-1.5 rounded bg-line overflow-hidden">{v.score != null && <div className={`h-full ${v.score >= 90 ? "bg-ok" : v.score >= 60 ? "bg-warn" : "bg-err"}`} style={{ width: `${v.score}%` }} />}</div>
                <span className="w-16 text-right font-mono">{v.score ?? "niesprawdz."}</span>
              </div>
            ))}
          </div>
          <div className="space-y-0.5">
            {qa.checks.map((c) => (
              <div key={c.id} className="flex items-start gap-1.5 text-xxs py-0.5 border-b border-line/50" title={c.detail}>
                <span className="mt-0.5">{icon(c.status)}</span>
                <div className="flex-1 min-w-0"><div className={c.status === "unchecked" ? "text-mute" : ""}>{c.name}{c.status === "unchecked" && " — NIESPRAWDZONE"}</div>
                  {(c.value != null || c.detail) && <div className="font-mono text-mute truncate">{c.value != null ? JSON.stringify(c.value) : ""}{c.expected != null ? ` (oczek. ${JSON.stringify(c.expected)})` : ""} {c.detail}</div>}</div>
              </div>
            ))}
          </div>
        </>
      ) : <div className="text-mute">Brak raportu — uruchom testy lub zadanie zespołu.</div>}
      <div>
        <div className="label flex items-center gap-1 mb-1"><Sparkles size={11} /> Oceny AI (opinie modeli — nie pomiary)</div>
        {Object.keys(ai).length ? Object.entries(ai).map(([agent, v]) => (
          <div key={agent} className="text-xxs mb-1"><b>{agent}</b> <span className="text-mute">(runda {v.round}, {v.verdict})</span>: {Object.entries(v.scores || {}).map(([k, s]) => `${k} ${s}`).join(" · ") || "—"}</div>
        )) : <div className="text-xxs text-mute">Brak ocen AI.</div>}
      </div>
    </div>
  );
}

function TeamConfigDialog({ cfg, providers, save, onClose }: { cfg: TeamConfig; providers: ProviderInfo[]; save: (p: Partial<TeamConfig>) => Promise<void>; onClose: () => void }) {
  const [members, setMembers] = useState(cfg.members);
  const [lim, setLim] = useState({ max_rounds: cfg.max_rounds, max_debate_rounds: cfg.max_debate_rounds, build_max_turns: cfg.build_max_turns,
    max_tokens: cfg.max_tokens ?? "", max_seconds: cfg.max_seconds ?? "", max_cost_usd: cfg.max_cost_usd ?? "", retries: cfg.retries,
    auto_apply_max_severity: cfg.auto_apply_max_severity });
  const upd = (i: number, p: Partial<(typeof members)[0]>) => setMembers(members.map((m, k) => (k === i ? { ...m, ...p } : m)));
  const move = (i: number, d: number) => { const a = [...members]; const j = i + d; if (j < 0 || j >= a.length) return; [a[i], a[j]] = [a[j], a[i]]; setMembers(a.map((m, k) => ({ ...m, order: k }))); };
  const addMcp = () => {
    const taken = new Set(members.map((m) => m.label));
    const name = ["Claude", "Gemini", "ChatGPT", "Agent 4", "Agent 5"].find((n) => !taken.has(n)) || `Agent ${members.length + 1}`;
    setMembers([...members, { id: "", kind: "mcp", provider_id: null, model: "", role: "auto", enabled: true, order: members.length, label: name,
      color: ["#d97757", "#4285f4", "#10a37f", "#a970ff", "#e0a33a"][members.length % 5] }]);
  };
  const add = (pid: string) => { const p = providers.find((x) => x.id === pid); if (!p) return; setMembers([...members, { id: "", provider_id: p.id, model: p.default_model, role: "auto", enabled: true, order: members.length, label: p.name, color: ["#d97757", "#10a37f", "#4285f4", "#a970ff", "#e0a33a"][members.length % 5] }]); };
  const num = (v: string | number) => (v === "" || v === null ? null : Number(v));
  const doSave = async () => {
    await save({ members: members.map((m, k) => ({ ...m, order: k })), max_rounds: Number(lim.max_rounds), max_debate_rounds: Number(lim.max_debate_rounds),
      build_max_turns: Number(lim.build_max_turns), max_tokens: num(lim.max_tokens), max_seconds: num(lim.max_seconds), max_cost_usd: num(lim.max_cost_usd),
      retries: Number(lim.retries), auto_apply_max_severity: lim.auto_apply_max_severity });
    onClose();
  };
  return (
    <div className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="w-full max-w-3xl max-h-[88vh] overflow-y-auto bg-panel border border-line2 rounded-xl slide-in" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 h-11 border-b border-line"><div className="font-medium flex items-center gap-1.5"><Users size={14} /> AI TEAM — konfiguracja</div><button className="icon-btn" onClick={onClose}><X size={15} /></button></div>
        <div className="p-4 space-y-4">
          <div className="text-dim">Kolejność = kolejność współpracy. Rola „auto” dobiera się do liczby modeli: 1 model pełni wszystkie role (z osobną samokontrolą), 2 modele — Lead + Engineer/Quality, 3 modele — po jednej roli, kolejne — Reviewer. Klucze API konfigurujesz w Ustawienia → AI Connections.</div>
          <div className="space-y-1.5">
            {members.map((m, i) => {
              const p = providers.find((x) => x.id === m.provider_id);
              return (
                <div key={i} className={`grid grid-cols-[auto_1fr_1fr_150px_auto_auto] gap-2 items-center rounded-lg border border-line2 p-2 ${m.enabled ? "" : "opacity-50"}`}>
                  <div className="flex flex-col"><button className="icon-btn !w-5 !h-4" onClick={() => move(i, -1)}><ArrowUp size={11} /></button><button className="icon-btn !w-5 !h-4" onClick={() => move(i, 1)}><ArrowDown size={11} /></button></div>
                  <div className="flex items-center gap-1.5 min-w-0"><input type="color" value={m.color} onChange={(e) => upd(i, { color: e.target.value })} className="w-5 h-5 bg-transparent" />
                    <input className="input" value={m.label} placeholder={p?.name} onChange={(e) => upd(i, { label: e.target.value })} /></div>
                  {m.kind === "mcp" ? <span className="chip !h-7 justify-center" title="Agent łączy się przez MCP — nazwa podawana w team_join">Agent MCP · bez klucza</span> : (
                  <select className="input font-mono text-xxs" value={m.model} onChange={(e) => upd(i, { model: e.target.value })}>
                    {[...new Set([m.model, ...(p?.models || [])])].filter(Boolean).map((x) => <option key={x}>{x}</option>)}
                  </select>)}
                  <select className="input" value={m.role} onChange={(e) => upd(i, { role: e.target.value })}>
                    <option value="auto">auto ({(cfg.effective_roles[m.id] || []).map((r) => ROLE_SHORT[r]).join(", ") || "—"})</option>
                    {Object.entries(cfg.roles).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
                  </select>
                  <label className="flex items-center gap-1 text-xxs"><input type="checkbox" checked={m.enabled} onChange={(e) => upd(i, { enabled: e.target.checked })} />aktywny</label>
                  <button className="icon-btn" onClick={() => setMembers(members.filter((_, k) => k !== i))}><Trash2 size={13} /></button>
                  {m.kind === "mcp" ? <div className="col-span-6 text-xxs text-mute pl-6">Nazwa „{m.label}” wpisujesz w aplikacji AI (team_join). Instrukcje podłączenia są w panelu AI Team.</div> : (
                  <div className="col-span-6 flex gap-2 items-center text-xxs text-mute pl-6">
                    {p ? <>{p.label} · {p.has_key ? `klucz ${p.key_preview}` : "bez klucza"}</> : <span className="text-err">połączenie usunięte</span>}
                    <span className="ml-auto">cena $/1M tok (opcjonalnie): we</span><input className="input !w-16 !h-6" type="number" step="0.01" value={m.price_in ?? ""} onChange={(e) => upd(i, { price_in: e.target.value === "" ? null : Number(e.target.value) })} />
                    wy<input className="input !w-16 !h-6" type="number" step="0.01" value={m.price_out ?? ""} onChange={(e) => upd(i, { price_out: e.target.value === "" ? null : Number(e.target.value) })} />
                  </div>)}
                </div>
              );
            })}
            <button className="btn-primary w-full justify-center" onClick={addMcp}><Plug size={13} /> Dodaj agenta MCP (bez klucza API — Claude Desktop/Code, Gemini CLI, ChatGPT)</button>
            <select className="input" value="" onChange={(e) => add(e.target.value)}>
              <option value="">+ albo model przez klucz API (z AI Connections)…</option>
              {providers.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.name} — {p.label}</option>)}
            </select>

          </div>
          <div className="grid grid-cols-4 gap-2">
            {([["max_rounds", "Rundy kontroli"], ["max_debate_rounds", "Rundy debaty"], ["build_max_turns", "Tury narzędzi / agent"], ["retries", "Ponowienia zapytań"],
               ["max_tokens", "Limit tokenów"], ["max_seconds", "Limit czasu [s]"], ["max_cost_usd", "Limit kosztu [$]"]] as const).map(([k, l]) => (
              <div key={k}><div className="label mb-1">{l}</div><input className="input" type="number" value={String(lim[k])} onChange={(e) => setLim({ ...lim, [k]: e.target.value })} /></div>
            ))}
            <div><div className="label mb-1">AUTO stosuje do</div>
              <select className="input" value={lim.auto_apply_max_severity} onChange={(e) => setLim({ ...lim, auto_apply_max_severity: e.target.value as typeof lim.auto_apply_max_severity })}>
                <option value="minor">minor</option><option value="major">major</option><option value="critical">critical</option></select></div>
          </div>
          <div className="text-xxs text-mute">Limit kosztu działa tylko dla modeli z podaną ceną. Tokeny liczone są z danych zwracanych przez dostawców (jeśli dostawca ich nie podaje — 0).</div>
          <div className="flex justify-end gap-2"><button className="btn" onClick={onClose}>Anuluj</button><button className="btn-primary" onClick={doSave}>Zapisz zespół</button></div>
        </div>
      </div>
    </div>
  );
}
