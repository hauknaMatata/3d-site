import { AlertTriangle, Box, Car, ChevronDown, Copy, Download, FileUp, FolderOpen, Hexagon, Import, Plus, Settings, Trash2, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, apiBase, apiToken, download, setConnection } from "../lib/api";
import { answerConfirm, useStore } from "../lib/store";
import type { Mode, Project, Snapshot } from "../lib/types";

export function Logo() {
  return <span className="flex items-center gap-1.5 font-semibold tracking-tight"><Hexagon size={16} className="text-accent" fill="currentColor" fillOpacity={0.2} />AI 3D Studio</span>;
}

export function TopBar({ onExport, onSettings }: { onExport: () => void; onSettings: () => void }) {
  const project = useStore((s) => s.project);
  const [menu, setMenu] = useState(false);
  const [name, setName] = useState(project?.name || "");
  useEffect(() => setName(project?.name || ""), [project?.name]);
  const importRef = useRef<HTMLInputElement>(null);
  if (!project) return null;
  const rename = async () => {
    if (name.trim() && name !== project.name) { await api.patch(`/api/projects/${project.id}`, { name }); useStore.setState({ project: { ...project, name } }); }
  };
  const doImport = async (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api.upload<{ snapshot: Snapshot }>(`/api/projects/${project.id}/import`, fd); useStore.getState().setSnap(r.snapshot); useStore.getState().notify(`Zaimportowano ${f.name}`, "ok"); }
    catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  return (
    <div className="h-11 flex items-center gap-3 px-3 bg-panel border-b border-line">
      <button className="btn-ghost !px-1" onClick={() => { useStore.getState().close(); try { localStorage.removeItem("ai3d.lastProject"); } catch { /* */ } }} title="Projekty"><Logo /></button>
      <div className="w-px h-5 bg-line" />
      <div className="relative">
        <button className="btn-ghost" onClick={() => setMenu(!menu)}>Projekt <ChevronDown size={12} /></button>
        {menu && (
          <div className="absolute z-30 top-8 left-0 w-60 rounded-lg border border-line2 bg-panel2 shadow-xl py-1" onMouseLeave={() => setMenu(false)}>
            <MenuItem icon={<FolderOpen size={13} />} label="Wszystkie projekty" onClick={() => useStore.getState().close()} />
            <MenuItem icon={<Import size={13} />} label={project.mode === "cad" ? "Importuj STEP…" : "Importuj model (GLB/OBJ/STL/PLY/FBX)…"} onClick={() => importRef.current?.click()} />
            <MenuItem icon={<Copy size={13} />} label="Duplikuj projekt" onClick={async () => { const p = await api.post<Project>(`/api/projects/${project.id}/duplicate`); useStore.getState().open(p.id); }} />
            <MenuItem icon={<Download size={13} />} label="Eksportuj cały projekt (.ai3d.zip)" onClick={() => download(`/api/projects/${project.id}/bundle`, `${project.name}.ai3d.zip`)} />
            <MenuItem icon={<Trash2 size={13} />} label="Usuń projekt…" danger onClick={() => useStore.setState({ confirms: [...useStore.getState().confirms, { id: "local-delete", title: `Usunąć projekt „${project.name}”?`, detail: "Operacja jest nieodwracalna — historia, wersje i załączniki zostaną usunięte." }] })} />
          </div>
        )}
        <input ref={importRef} type="file" className="hidden" accept={project.mode === "cad" ? ".step,.stp" : ".glb,.gltf,.obj,.stl,.ply,.fbx,.3mf"} onChange={(e) => { const f = e.target.files?.[0]; if (f) doImport(f); e.target.value = ""; }} />
      </div>
      <input className="bg-transparent outline-none border border-transparent hover:border-line2 focus:border-accent rounded px-1.5 h-7 w-56 font-medium" value={name} onChange={(e) => setName(e.target.value)} onBlur={rename} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
      <span className={`chip ${project.mode === "cad" ? "!text-[#9fc6ff] !border-[#2f4a74]" : "!text-[#f0b46a] !border-[#6b4a24]"}`}>{project.mode === "cad" ? <><Wrench size={10} /> CAD</> : <><Car size={10} /> GAME</>}</span>
      <div className="flex-1" />
      <button className="btn-ghost" onClick={onSettings}><Settings size={14} /> Ustawienia</button>
      <button className="btn-primary" onClick={onExport}><Download size={13} /> EXPORT MODEL</button>
    </div>
  );
}

function MenuItem({ icon, label, onClick, danger }: { icon: JSX.Element; label: string; onClick: () => void; danger?: boolean }) {
  return <button onClick={onClick} className={`w-full flex items-center gap-2 px-3 h-8 text-left hover:bg-panel ${danger ? "text-err" : "text-fg"}`}>{icon}{label}</button>;
}

export function Home({ onSettings }: { onSettings: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("cad");
  const [err, setErr] = useState("");
  const bundleRef = useRef<HTMLInputElement>(null);
  const [offline, setOffline] = useState(false);
  const load = () => api.get<Project[]>("/api/projects").then((p) => { setProjects(p); setErr(""); setOffline(false); })
    .catch((e) => { setErr(e.message); setOffline(!(e instanceof ApiError)); });
  useEffect(() => { load(); }, []);
  useEffect(() => { if (!offline) return; const t = window.setInterval(load, 4000); return () => window.clearInterval(t); }, [offline]);
  if (offline) return <EngineOffline onSettings={onSettings} retry={load} />;
  const create = async () => {
    try { const p = await api.post<Project>("/api/projects", { name: name.trim() || (mode === "cad" ? "Nowa część CAD" : "Nowy asset"), mode }); useStore.getState().open(p.id); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="h-full overflow-y-auto">
      <div className="h-11 flex items-center px-4 border-b border-line bg-panel"><Logo /><div className="flex-1" /><button className="btn-ghost" onClick={onSettings}><Settings size={14} /> Ustawienia</button></div>
      <div className="max-w-5xl mx-auto px-6 py-10 grid grid-cols-1 lg:grid-cols-[1fr_1fr] gap-10">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Nowy projekt</h1>
          <p className="text-dim mt-1 mb-5">Wybierz pipeline modelowania. Każdy ma osobny, prawdziwy silnik 3D.</p>
          <div className="grid grid-cols-2 gap-3">
            {([["cad", <Wrench key="w" size={18} />, "CAD Mode", "Precyzyjne bryły B-Rep (OpenCascade): wymiary w mm, szkice, extrude, revolve, boolean, fillet, shell, otwory. Eksport STEP/3MF/STL do druku 3D."],
               ["game", <Box key="b" size={18} />, "Game Mode", "Kolorowe assety PBR: pojazdy, postacie, budynki, rekwizyty. Materiały, UV, normal/roughness/metallic/AO, LOD. Eksport GLB/FBX/OBJ."]] as [Mode, JSX.Element, string, string][]).map(([m, icon, t, d]) => (
              <button key={m} onClick={() => setMode(m)} className={`text-left rounded-xl border p-4 transition-colors ${mode === m ? "border-accent bg-accent/[0.07]" : "border-line2 hover:border-mute bg-panel"}`}>
                <div className={`mb-2 ${mode === m ? "text-accent" : "text-dim"}`}>{icon}</div>
                <div className="font-medium">{t}</div>
                <div className="text-dim text-xxs leading-relaxed mt-1">{d}</div>
              </button>
            ))}
          </div>
          <div className="flex gap-2 mt-4">
            <input className="input !h-9" placeholder="Nazwa projektu" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
            <button className="btn-primary !h-9 !px-4" onClick={create}><Plus size={14} /> Utwórz</button>
          </div>
          {err && <div className="mt-3 text-err flex items-center gap-1.5"><AlertTriangle size={13} /> Backend: {err}. Sprawdź Ustawienia → Połączenie.</div>}
        </div>
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold">Projekty</h2>
            <button className="btn-ghost text-xxs" onClick={() => bundleRef.current?.click()}><FileUp size={13} /> Importuj projekt</button>
            <input ref={bundleRef} type="file" accept=".zip" className="hidden" onChange={async (e) => {
              const f = e.target.files?.[0]; if (!f) return; const fd = new FormData(); fd.append("file", f);
              try { const p = await api.upload<Project>("/api/projects/import-bundle", fd); load(); useStore.getState().open(p.id); } catch (er) { setErr((er as Error).message); } e.target.value = ""; }} />
          </div>
          <div className="rounded-xl border border-line overflow-hidden">
            {projects.map((p) => (
              <button key={p.id} onClick={() => useStore.getState().open(p.id)} className="w-full flex items-center gap-3 px-3 h-11 border-b border-line last:border-0 hover:bg-panel2 text-left">
                <span className={p.mode === "cad" ? "text-[#9fc6ff]" : "text-[#f0b46a]"}>{p.mode === "cad" ? <Wrench size={14} /> : <Box size={14} />}</span>
                <span className="flex-1 truncate">{p.name}</span>
                <span className="text-xxs text-mute">{p.ops} op.</span>
                <span className="text-xxs text-mute w-32 text-right">{new Date(p.updated * 1000).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" })}</span>
              </button>
            ))}
            {!projects.length && <div className="text-mute text-center py-10">Brak projektów</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

function EngineOffline({ onSettings, retry }: { onSettings: () => void; retry: () => void }) {
  const [url, setUrl] = useState(apiBase() || "http://localhost:8000");
  const pages = window.location.hostname.endsWith("github.io");
  const hosted = !!apiBase() && !/localhost|127\.0\.0\.1/.test(apiBase());
  const branch = (import.meta.env.VITE_ENGINE_BRANCH as string) || "";
  const steps = `git clone ${branch ? `-b ${branch} ` : ""}https://github.com/hauknaMatata/3d-site.git
cd 3d-site/backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000`;
  const localPart = (
    <>
        <p className="text-dim mt-2 leading-relaxed">
          {pages ? "Ta strona (GitHub Pages) to interfejs. " : ""}Modelowanie CAD (OpenCascade), siatki, eksport i serwer MCP dla agentów AI działają w lokalnym
          silniku w Pythonie — dzięki temu pracujesz na swoim sprzęcie i bez kluczy API. Wymagany Python 3.11–3.13.
        </p>
        <pre className="mt-4 text-xxs font-mono bg-bg border border-line rounded-lg p-3 whitespace-pre-wrap select-all">{steps}</pre>
        <p className="text-dim mt-3">Gdy w terminalu pojawi się <span className="font-mono">Uvicorn running on http://127.0.0.1:8000</span>, ta strona połączy się sama (sprawdzam co 4 s).</p>
        <div className="mt-5 rounded-lg border border-line2 p-3">
          <div className="label mb-1">Adres silnika</div>
          <div className="flex gap-2">
            <input className="input font-mono" value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="btn-primary" onClick={() => { setConnection(url.trim(), apiToken()); retry(); }}>Połącz</button>
          </div>
          <div className="text-xxs text-mute mt-1.5">Chrome/Edge mogą zapytać o zgodę na dostęp do sieci lokalnej — zezwól. Safari blokuje połączenia https → http://localhost; użyj Chrome, Edge lub Firefoksa.</div>
        </div>
    </>
  );
  return (
    <div className="h-full overflow-y-auto">
      <div className="h-11 flex items-center px-4 border-b border-line bg-panel"><Logo /><div className="flex-1" /><button className="btn-ghost" onClick={onSettings}><Settings size={14} /> Ustawienia</button></div>
      <div className="max-w-2xl mx-auto px-6 py-12">
        <div className="flex items-center gap-2 text-warn"><span className="w-2 h-2 rounded-full bg-warn pulse-dot" /> Łączę z silnikiem 3D: {apiBase() || "ten sam adres"}…</div>
        {hosted && (
          <>
            <h1 className="text-xl font-semibold tracking-tight mt-3">Silnik online się uruchamia</h1>
            <p className="text-dim mt-2 leading-relaxed">Silnik 3D działa na serwerze w chmurze. Darmowy hosting usypia go po dłuższej bezczynności —
              pierwsze wejście może potrwać 1–3 minuty. Nic nie musisz instalować; strona połączy się sama (sprawdzam co 4 s).</p>
            <details className="mt-6 text-dim"><summary className="cursor-pointer hover:text-fg">Zaawansowane: użyj własnego silnika (lokalnie)</summary>
              <div className="mt-3">{localPart}</div></details>
          </>
        )}
        {!hosted && (
          <>
            <h1 className="text-xl font-semibold tracking-tight mt-3">Uruchom silnik AI 3D Studio w chmurze (za darmo)</h1>
            <p className="text-dim mt-2 leading-relaxed">Silnik działa w <b>GitHub Codespaces</b> — darmowej maszynie w chmurze GitHuba (nie na Twoim komputerze).</p>
            <ol className="mt-3 space-y-1.5 text-dim list-decimal pl-5">
              <li><a className="text-accent hover:underline" target="_blank" rel="noreferrer"
                href={`https://codespaces.new/hauknaMatata/3d-site${branch ? `/tree/${branch}` : ""}?quickstart=1`}>Kliknij tutaj, aby uruchomić silnik w Codespaces</a> → <b>Create codespace</b>.</li>
              <li>Poczekaj na komunikat w terminalu <span className="font-mono">„Silnik AI 3D Studio działa”</span> (pierwszy raz 3–5 min, kolejne starty ~30 s).</li>
              <li>Kliknij link <span className="font-mono">Strona: https://hauknamatata.github.io/3d-site/?engine=…</span> z terminala — strona zapamięta adres silnika.</li>
            </ol>
            <p className="text-xxs text-mute mt-3">Codespace wyłącza się po bezczynności (domyślnie 30 min, max 4 h w ustawieniach GitHub → Codespaces). Darmowy limit: ok. 60 h pracy miesięcznie. Jeśli przeglądarka pokaże stronę ostrzegawczą GitHuba dla adresu silnika, otwórz go raz i kliknij „Continue”.</p>
            <details className="mt-6 text-dim"><summary className="cursor-pointer hover:text-fg">Zaawansowane: silnik lokalny lub własny adres</summary>
              <div className="mt-3">{localPart}</div></details>
          </>
        )}
      </div>
    </div>
  );
}

export function Confirms() {
  const confirms = useStore((s) => s.confirms);
  const project = useStore((s) => s.project);
  const c = confirms[0];
  if (!c) return null;
  const respond = async (ok: boolean) => {
    if (c.id === "local-delete") {
      useStore.setState({ confirms: confirms.slice(1) });
      if (ok && project) { await api.del(`/api/projects/${project.id}?confirm=true`); useStore.getState().close(); }
      return;
    }
    answerConfirm(c.id, ok);
  };
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-panel border border-warn/50 rounded-xl p-4 slide-in">
        <div className="flex items-center gap-2 font-medium"><AlertTriangle size={16} className="text-warn" />{c.title}</div>
        <pre className="mt-2 text-xxs text-dim whitespace-pre-wrap font-mono max-h-60 overflow-auto bg-bg rounded p-2 border border-line">{c.detail}</pre>
        <div className="flex justify-end gap-2 mt-3"><button className="btn" onClick={() => respond(false)}>Odrzuć</button><button className="btn-primary !bg-warn !border-warn !text-black" onClick={() => respond(true)}>Zatwierdź</button></div>
      </div>
    </div>
  );
}

export function Toast() {
  const t = useStore((s) => s.toast);
  if (!t) return null;
  return (
    <div className={`fixed z-50 bottom-5 left-1/2 -translate-x-1/2 px-3.5 py-2 rounded-lg border shadow-xl slide-in max-w-xl ${t.kind === "err" ? "bg-[#2a1416] border-err/60 text-[#ffb4ae]" : t.kind === "ok" ? "bg-[#12241b] border-ok/50 text-[#a6e3c0]" : "bg-panel2 border-line2"}`}>{t.msg}</div>
  );
}
