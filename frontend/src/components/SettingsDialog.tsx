import { CheckCircle2, Loader2, Plug, RefreshCw, Server, Trash2, X, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { absUrl, api, apiBase, apiToken, setConnection } from "../lib/api";
import { useStore } from "../lib/store";
import type { McpServer, ProviderInfo } from "../lib/types";

type Tab = "providers" | "tasks" | "mcp" | "connection";
interface Kind { kind: string; label: string; needs_key: boolean; default_base_url: string; suggested_models: string[] }

export default function SettingsDialog({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("providers");
  return (
    <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px] flex items-center justify-center p-4" onClick={onClose}>
      <div className="w-full max-w-3xl h-[82vh] flex flex-col bg-panel border border-line2 rounded-xl shadow-2xl slide-in" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 h-11 border-b border-line">
          <div className="flex gap-1">
            {([["providers", "AI Connections"], ["tasks", "Przypisanie zadań"], ["mcp", "Serwery MCP"], ["connection", "Połączenie"]] as [Tab, string][]).map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)} className={`tab !h-11 ${tab === k ? "tab-on" : ""}`}>{l}</button>
            ))}
          </div>
          <button className="icon-btn" onClick={onClose}><X size={15} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {tab === "providers" && <Providers />}
          {tab === "tasks" && <Tasks />}
          {tab === "mcp" && <Mcp />}
          {tab === "connection" && <Connection />}
        </div>
      </div>
    </div>
  );
}

function Providers() {
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [list, setList] = useState<ProviderInfo[]>([]);
  const [def, setDef] = useState<string | null>(null);
  const [form, setForm] = useState({ kind: "anthropic", name: "", api_key: "", base_url: "", default_model: "" });
  const [tests, setTests] = useState<Record<string, { ok: boolean; error?: string; reply?: string; model?: string } | "busy">>({});
  const [usage, setUsage] = useState<{ provider_id: string; model: string; calls: number; tokens_in: number; tokens_out: number; errors: number; retries: number; last_error: string; last_ok: number | null; last_latency_ms: number | null }[]>([]);
  const load = () => {
    api.get<typeof usage>("/api/team/usage").then(setUsage).catch(() => undefined);
    return api.get<{ providers: ProviderInfo[]; default_provider: string | null }>("/api/providers").then((r) => { setList(r.providers); setDef(r.default_provider); window.dispatchEvent(new Event("ai3d:providers")); window.dispatchEvent(new Event("ai3d:team")); });
  };
  useEffect(() => { api.get<{ provider_kinds: Kind[] }>("/api/capabilities").then((c) => setKinds(c.provider_kinds)); load(); }, []);
  const kind = kinds.find((k) => k.kind === form.kind);
  const add = async () => {
    try { await api.post("/api/providers", form); setForm({ ...form, name: "", api_key: "", default_model: "" }); load(); useStore.getState().notify("Dodano dostawcę", "ok"); }
    catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  const test = async (id: string) => {
    setTests((t) => ({ ...t, [id]: "busy" }));
    const r = await api.post<{ ok: boolean; error?: string; reply?: string; model?: string }>(`/api/providers/${id}/test`).catch((e) => ({ ok: false, error: e.message }));
    setTests((t) => ({ ...t, [id]: r })); load();
  };
  return (
    <div className="space-y-5">
      <div className="text-dim leading-relaxed">Każde połączenie ma osobny klucz, adres i wybór modelu. Możesz podłączyć jeden, dwa, trzy lub więcej modeli — skład zespołu, role i kolejność ustawiasz w zakładce AI Team. Klucze API są wysyłane jednorazowo do backendu i przechowywane <b>zaszyfrowane</b> (Fernet) w bazie serwera — nigdy w przeglądarce. Integracje używają bezpośrednio REST API dostawców (niezależnie od MCP).</div>
      <div className="rounded-lg border border-line2 p-3 grid grid-cols-2 gap-2">
        <div><div className="label mb-1">Dostawca</div>
          <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value, base_url: "" })}>
            {kinds.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
          </select></div>
        <div><div className="label mb-1">Nazwa (opcjonalnie)</div><input className="input" value={form.name} placeholder={kind?.label} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><div className="label mb-1">Klucz API {kind?.needs_key ? "" : "(opcjonalny)"}</div><input className="input font-mono" type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} /></div>
        <div><div className="label mb-1">Base URL {form.kind === "openai_compatible" ? "(wymagany)" : "(opcjonalny)"}</div>
          <input className="input font-mono" value={form.base_url} placeholder={kind?.default_base_url || "https://…/v1"} onChange={(e) => setForm({ ...form, base_url: e.target.value })} /></div>
        <div className="col-span-2"><div className="label mb-1">Domyślny model</div>
          <input className="input font-mono" list="sugg-models" value={form.default_model} placeholder={kind?.suggested_models[0] || "nazwa modelu"} onChange={(e) => setForm({ ...form, default_model: e.target.value })} />
          <datalist id="sugg-models">{kind?.suggested_models.map((m) => <option key={m} value={m} />)}</datalist></div>
        <div className="col-span-2 flex justify-between items-center">
          <span className="text-xxs text-mute">{form.kind === "ollama" ? "Ollama musi być osiągalna z serwera backendu (np. http://host:11434)." : "Po dodaniu użyj „Testuj” — pobierze listę modeli z API."}</span>
          <button className="btn-primary" onClick={add} disabled={kind?.needs_key && !form.api_key}>Dodaj dostawcę</button>
        </div>
      </div>
      <div className="space-y-2">
        {list.map((p) => {
          const t = tests[p.id];
          return (
            <div key={p.id} className="rounded-lg border border-line2 p-3">
              <div className="flex items-center gap-2">
                <input type="radio" checked={def === p.id} title="Domyślny dostawca" onChange={async () => { await api.put("/api/ai/default", { provider_id: p.id }); load(); }} />
                <div className="flex-1 min-w-0"><div className="font-medium">{p.name} <span className="text-mute font-normal">· {p.label}</span></div>
                  <div className="text-xxs text-mute font-mono truncate">{p.has_key ? `klucz ${p.key_preview}` : "bez klucza"} {p.base_url && `· ${p.base_url}`}</div></div>
                <select className="input !w-48 font-mono text-xxs" value={p.default_model} onChange={async (e) => { await api.patch(`/api/providers/${p.id}`, { default_model: e.target.value }); load(); }}>
                  {[...new Set([p.default_model, ...p.models])].filter(Boolean).map((m) => <option key={m}>{m}</option>)}
                </select>
                <label className="flex items-center gap-1 text-xxs text-dim"><input type="checkbox" checked={p.enabled} onChange={async (e) => { await api.patch(`/api/providers/${p.id}`, { enabled: e.target.checked }); load(); }} />aktywny</label>
                <button className="btn !h-6" onClick={() => test(p.id)}>{t === "busy" ? <Loader2 size={12} className="animate-spin" /> : <Plug size={12} />} Testuj</button>
                <button className="icon-btn" title="Usuń" onClick={async () => { if (confirm(`Usunąć dostawcę ${p.name}?`)) { await api.del(`/api/providers/${p.id}`); load(); } }}><Trash2 size={13} /></button>
              </div>
              {usage.filter((u) => u.provider_id === p.id).map((u) => (
                <div key={u.model} className="mt-1.5 flex flex-wrap gap-x-3 text-xxs font-mono text-mute">
                  <span className="text-dim">{u.model}</span><span>{u.calls} wywołań</span>
                  <span>{u.tokens_in.toLocaleString("pl-PL")} tok we / {u.tokens_out.toLocaleString("pl-PL")} wy</span>
                  {u.last_latency_ms != null && <span>{u.last_latency_ms} ms</span>}
                  {u.retries > 0 && <span className="text-warn">{u.retries} ponowień</span>}
                  {u.errors > 0 && <span className="text-err" title={u.last_error}>{u.errors} błędów: {u.last_error.slice(0, 80)}</span>}
                </div>
              ))}
              {t && t !== "busy" && (
                <div className={`mt-2 text-xxs flex items-start gap-1.5 ${t.ok ? "text-ok" : "text-err"}`}>
                  {t.ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                  <span className="break-all">{t.ok ? `Połączono. ${t.model ? `Model ${t.model} odpowiedział: „${t.reply}”. ` : ""}Modeli: ${p.models.length}.` : t.error}</span>
                </div>
              )}
            </div>
          );
        })}
        {!list.length && <div className="text-mute text-center py-6">Brak dostawców — aplikacja działa w trybie planera offline (szablony bez LLM).</div>}
      </div>
    </div>
  );
}

function Tasks() {
  const [list, setList] = useState<ProviderInfo[]>([]);
  const [tasks, setTasks] = useState<Record<string, string>>({});
  const [asg, setAsg] = useState<Record<string, { provider_id?: string | null; model?: string | null }>>({});
  useEffect(() => {
    api.get<{ providers: ProviderInfo[]; assignments: typeof asg }>("/api/providers").then((r) => { setList(r.providers); setAsg(r.assignments || {}); });
    api.get<{ tasks: Record<string, string> }>("/api/capabilities").then((c) => setTasks(c.tasks));
  }, []);
  const save = async (next: typeof asg) => { setAsg(next); await api.put("/api/ai/assignments", next); useStore.getState().notify("Zapisano przypisania", "ok"); };
  return (
    <div className="space-y-3">
      <div className="text-dim">Przypisz różne modele do różnych zadań. Przykład: Claude do modelowania, tańszy model wizyjny do analizy zdjęć. Pusty wybór = domyślny dostawca.</div>
      {Object.entries(tasks).map(([k, label]) => {
        const p = list.find((x) => x.id === asg[k]?.provider_id);
        return (
          <div key={k} className="grid grid-cols-[180px_1fr_1fr] gap-2 items-center">
            <span>{label}</span>
            <select className="input" value={asg[k]?.provider_id || ""} onChange={(e) => save({ ...asg, [k]: { provider_id: e.target.value || null, model: null } })}>
              <option value="">(domyślny)</option>{list.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
            <select className="input font-mono text-xxs" disabled={!p} value={asg[k]?.model || ""} onChange={(e) => save({ ...asg, [k]: { ...asg[k], model: e.target.value } })}>
              <option value="">{p ? `(${p.default_model})` : "—"}</option>{p?.models.map((m) => <option key={m}>{m}</option>)}
            </select>
          </div>
        );
      })}
    </div>
  );
}

function Mcp() {
  const [list, setList] = useState<McpServer[]>([]);
  const [busy, setBusy] = useState<string>("");
  const [form, setForm] = useState({ name: "", url: "", transport: "streamable_http", header: "", permission: "ask" });
  const [msg, setMsg] = useState<Record<string, string>>({});
  const load = () => api.get<McpServer[]>("/api/mcp/servers").then(setList);
  useEffect(() => { load(); }, []);
  const add = async () => {
    const headers: Record<string, string> = {};
    if (form.header.includes(":")) { const [k, ...v] = form.header.split(":"); headers[k.trim()] = v.join(":").trim(); }
    try { await api.post("/api/mcp/servers", { name: form.name, url: form.url, transport: form.transport, headers, permission: form.permission }); setForm({ ...form, name: "", url: "", header: "" }); load(); }
    catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  const test = async (id: string) => {
    setBusy(id);
    const r = await api.post<{ status: string; error?: string }>(`/api/mcp/servers/${id}/test`).catch((e) => ({ status: "error", error: e.message }));
    setMsg((m) => ({ ...m, [id]: r.error || "" })); setBusy(""); load();
  };
  const patch = async (id: string, body: object) => { await api.patch(`/api/mcp/servers/${id}`, body); load(); };
  const own = absUrl("/mcp/") || `${window.location.origin}/mcp/`;
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-accent/40 bg-accent/5 p-3">
        <div className="font-medium flex items-center gap-1.5"><Server size={14} /> Ta aplikacja jako serwer MCP</div>
        <div className="text-dim mt-1">Podłącz Claude Desktop / Claude Code / inne agenty do silników CAD i GAME (Streamable HTTP):</div>
        <code className="block mt-1.5 px-2 py-1 rounded bg-bg border border-line font-mono text-xxs select-all">{own.startsWith("http") ? own : window.location.origin + own}</code>
        <div className="text-xxs text-mute mt-1">Narzędzia: list_projects, create_project, get_scene, run_operation, set_parameters, undo, export_model, delete_project (wymaga potwierdzenia w UI). Gdy ustawiono API_TOKEN, wymagany nagłówek Authorization: Bearer.</div>
      </div>
      <div className="text-dim">Zewnętrzne serwery MCP (tylko zdalne HTTP/SSE — uruchamianie procesów stdio jest wyłączone ze względów bezpieczeństwa). Ich włączone narzędzia są udostępniane agentowi AI.</div>
      <div className="rounded-lg border border-line2 p-3 grid grid-cols-2 gap-2">
        <input className="input" placeholder="Nazwa" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input className="input font-mono" placeholder="https://serwer/mcp" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
        <select className="input" value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select>
        <select className="input" value={form.permission} onChange={(e) => setForm({ ...form, permission: e.target.value })}>
          <option value="ask">Pytaj przed każdym wywołaniem</option><option value="allow">Zezwalaj (destrukcyjne nadal pytają)</option><option value="deny">Blokuj</option></select>
        <input className="input font-mono col-span-2" type="password" placeholder="Nagłówek autoryzacji, np. Authorization: Bearer xxx (szyfrowany)" value={form.header} onChange={(e) => setForm({ ...form, header: e.target.value })} />
        <div className="col-span-2 flex justify-end"><button className="btn-primary" disabled={!form.name || !form.url} onClick={add}>Dodaj serwer</button></div>
      </div>
      {list.map((s) => (
        <div key={s.id} className="rounded-lg border border-line2 p-3">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${s.status === "online" ? "bg-ok" : s.status === "error" ? "bg-err" : "bg-mute"}`} title={s.status} />
            <div className="flex-1 min-w-0"><div className="font-medium">{s.name}</div><div className="text-xxs text-mute font-mono truncate">{s.transport} · {s.url}{s.header_names.length ? ` · nagłówki: ${s.header_names.join(", ")}` : ""}</div></div>
            <select className="input !w-36 text-xxs" value={s.permission} onChange={(e) => patch(s.id, { permission: e.target.value })}><option value="ask">pytaj</option><option value="allow">zezwalaj</option><option value="deny">blokuj</option></select>
            <label className="flex items-center gap-1 text-xxs text-dim"><input type="checkbox" checked={s.enabled} onChange={(e) => patch(s.id, { enabled: e.target.checked })} />aktywny</label>
            <button className="btn !h-6" onClick={() => test(s.id)}>{busy === s.id ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Testuj</button>
            <button className="icon-btn" onClick={async () => { if (confirm(`Usunąć serwer ${s.name}?`)) { await api.del(`/api/mcp/servers/${s.id}`); load(); } }}><Trash2 size={13} /></button>
          </div>
          {msg[s.id] && <div className="text-err text-xxs mt-1.5 break-all">{msg[s.id]}</div>}
          {s.tools.length > 0 && (
            <div className="mt-2 grid grid-cols-2 gap-1">
              {s.tools.map((t) => (
                <label key={t.name} className="flex items-start gap-1.5 text-xxs" title={t.description}>
                  <input type="checkbox" checked={!s.disabled_tools.includes(t.name)}
                    onChange={(e) => patch(s.id, { disabled_tools: e.target.checked ? s.disabled_tools.filter((x) => x !== t.name) : [...s.disabled_tools, t.name] })} />
                  <span className="font-mono">{t.name}</span>{t.destructive && <span className="text-warn">destrukcyjne</span>}
                </label>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Connection() {
  const [url, setUrl] = useState(apiBase());
  const [token, setToken] = useState(apiToken());
  const [st, setSt] = useState("");
  const check = async () => {
    setConnection(url.trim(), token.trim());
    try { await api.get("/api/projects"); setSt("ok"); } catch (e) { setSt((e as Error).message); }
  };
  return (
    <div className="space-y-3 max-w-lg">
      <div className="text-dim">Adres backendu (FastAPI + silnik CAD). Pusty = ten sam host (proxy). Token wymagany, gdy na serwerze ustawiono API_TOKEN.</div>
      <div><div className="label mb-1">Backend URL</div><input className="input font-mono" placeholder="https://api.twoja-domena.pl" value={url} onChange={(e) => setUrl(e.target.value)} /></div>
      <div><div className="label mb-1">API token</div><input className="input font-mono" type="password" value={token} onChange={(e) => setToken(e.target.value)} /></div>
      <div className="flex items-center gap-2"><button className="btn-primary" onClick={check}>Zapisz i sprawdź</button>
        {st && (st === "ok" ? <span className="text-ok flex items-center gap-1"><CheckCircle2 size={13} /> Połączono</span> : <span className="text-err">{st}</span>)}</div>
      <div className="text-xxs text-mute">Te dwa pola są zapisywane w localStorage przeglądarki. Klucze dostawców AI nigdy nie trafiają do przeglądarki.</div>
    </div>
  );
}
