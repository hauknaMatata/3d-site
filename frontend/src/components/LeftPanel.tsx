import { ChevronDown, ChevronRight, Eye, EyeOff, History, Image as ImageIcon, Layers, Lock, Palette, Pencil, RotateCcw, Save, SlidersHorizontal, Trash2, Unlock, Upload, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { absUrl, api } from "../lib/api";
import { runOp, useStore } from "../lib/store";
import type { ObjectInfo, OpInfo, Snapshot } from "../lib/types";

type Tab = "scene" | "history" | "params" | "files" | "materials" | "versions";

export default function LeftPanel() {
  const [tab, setTab] = useState<Tab>("scene");
  const tabs: { id: Tab; icon: JSX.Element; label: string }[] = [
    { id: "scene", icon: <Layers size={13} />, label: "Scena" },
    { id: "history", icon: <History size={13} />, label: "Historia" },
    { id: "params", icon: <SlidersHorizontal size={13} />, label: "Parametry" },
    { id: "files", icon: <ImageIcon size={13} />, label: "Załączniki" },
    { id: "materials", icon: <Palette size={13} />, label: "Materiały" },
    { id: "versions", icon: <Save size={13} />, label: "Wersje" },
  ];
  return (
    <div className="h-full flex flex-col bg-panel border-r border-line">
      <div className="flex border-b border-line px-1">
        {tabs.map((t) => (
          <button key={t.id} title={t.label} onClick={() => setTab(t.id)} className={`tab flex-1 flex items-center justify-center ${tab === t.id ? "tab-on" : ""}`}>{t.icon}</button>
        ))}
      </div>
      <div className="panel-title">{tabs.find((t) => t.id === tab)?.label}</div>
      <div className="flex-1 overflow-y-auto">
        {tab === "scene" && <SceneTree />}
        {tab === "history" && <HistoryList />}
        {tab === "params" && <Params />}
        {tab === "files" && <Files />}
        {tab === "materials" && <Materials />}
        {tab === "versions" && <Versions />}
      </div>
      <Inspector />
    </div>
  );
}

function setFlag(name: string, flags: { hidden?: boolean; locked?: boolean }) {
  const p = useStore.getState().project;
  if (!p) return;
  api.post<{ snapshot: Snapshot }>(`/api/projects/${p.id}/objects/${encodeURIComponent(name)}/flags`, flags)
    .then((r) => useStore.getState().setSnap(r.snapshot)).catch((e) => useStore.getState().notify(e.message, "err"));
}

function SceneTree() {
  const objects = useStore((s) => s.snap?.objects || []);
  const selected = useStore((s) => s.selected);
  const select = useStore((s) => s.select);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const children = useMemo(() => {
    const m = new Map<string | null, ObjectInfo[]>();
    objects.forEach((o) => { const k = o.parent ?? null; m.set(k, [...(m.get(k) || []), o]); });
    return m;
  }, [objects]);
  const toggle = (n: string) => setCollapsed((c) => { const x = new Set(c); x.has(n) ? x.delete(n) : x.add(n); return x; });
  useEffect(() => { // collapse composites by default
    setCollapsed(new Set(objects.filter((o) => (children.get(o.name)?.length || 0) > 3).map((o) => o.name)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objects.length]);
  const row = (o: ObjectInfo, depth: number): JSX.Element => {
    const kids = children.get(o.name) || [];
    const open = !collapsed.has(o.name);
    return (
      <div key={o.name}>
        <div onClick={() => select(o.name)}
          className={`group flex items-center h-7 pr-1 cursor-pointer border-l-2 ${selected === o.name ? "bg-accent/15 border-accent" : "border-transparent hover:bg-panel2"}`}
          style={{ paddingLeft: 6 + depth * 12 }}>
          <button className="w-4 h-4 flex items-center justify-center text-mute" onClick={(e) => { e.stopPropagation(); toggle(o.name); }}>
            {kids.length ? (open ? <ChevronDown size={12} /> : <ChevronRight size={12} />) : null}
          </button>
          <span className="w-2.5 h-2.5 rounded-sm mr-2 shrink-0 border border-white/10" style={{ background: o.color || (o.kind === "group" ? "transparent" : "#888") }} />
          <span className={`truncate flex-1 ${o.hidden ? "text-mute line-through" : ""}`}>{o.name}</span>
          <span className="text-xxs text-mute mr-1 group-hover:hidden">{o.kind === "group" ? `${kids.length}` : o.material || o.kind}</span>
          <button title={o.hidden ? "Pokaż" : "Ukryj"} className="icon-btn !w-6 !h-6 hidden group-hover:inline-flex" onClick={(e) => { e.stopPropagation(); setFlag(o.name, { hidden: !o.hidden }); }}>
            {o.hidden ? <EyeOff size={12} /> : <Eye size={12} />}
          </button>
          <button title={o.locked ? "Odblokuj" : "Zablokuj"} className={`icon-btn !w-6 !h-6 ${o.locked ? "!text-warn" : "hidden group-hover:inline-flex"}`} onClick={(e) => { e.stopPropagation(); setFlag(o.name, { locked: !o.locked }); }}>
            {o.locked ? <Lock size={12} /> : <Unlock size={12} />}
          </button>
        </div>
        {open && kids.map((k) => row(k, depth + 1))}
      </div>
    );
  };
  if (!objects.length) return <Empty text="Brak obiektów. Wygeneruj model w panelu AI." />;
  return <div className="py-1">{(children.get(null) || []).map((o) => row(o, 0))}</div>;
}

function HistoryList() {
  const ops = useStore((s) => s.snap?.ops || []);
  const project = useStore((s) => s.project);
  const [edit, setEdit] = useState<OpInfo | null>(null);
  const [text, setText] = useState("");
  const del = async (op: OpInfo) => {
    if (!project) return;
    try { const r = await api.del<{ snapshot: Snapshot }>(`/api/projects/${project.id}/ops/${op.id}`); useStore.getState().setSnap(r.snapshot); }
    catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  const save = async () => {
    if (!project || !edit) return;
    try {
      const args = JSON.parse(text);
      const r = await api.patch<{ snapshot: Snapshot }>(`/api/projects/${project.id}/ops/${edit.id}`, { args });
      useStore.getState().setSnap(r.snapshot); setEdit(null); useStore.getState().notify("Operacja zaktualizowana — model przebudowany", "ok");
    } catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  if (!ops.length) return <Empty text="Historia operacji jest pusta." />;
  return (
    <div className="py-1">
      {ops.map((o, i) => (
        <div key={o.id} className="group px-2 py-1.5 border-b border-line/60 hover:bg-panel2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xxs text-mute w-6 text-right">{i + 1}</span>
            <span className={`flex-1 truncate ${o.error ? "text-err" : ""}`} title={o.label}>{o.label}</span>
            <span className="chip !h-4">{o.source === "ai" ? "AI" : o.source === "planner" ? "plan" : o.source === "mcp" ? "MCP" : "ręcznie"}</span>
            <button className="icon-btn !w-5 !h-5 hidden group-hover:inline-flex" title="Edytuj parametry operacji" onClick={() => { setEdit(o); setText(JSON.stringify(o.args, null, 1)); }}><Pencil size={11} /></button>
            <button className="icon-btn !w-5 !h-5 hidden group-hover:inline-flex" title="Usuń operację z historii" onClick={() => del(o)}><Trash2 size={11} /></button>
          </div>
          <div className="pl-8 font-mono text-xxs text-mute truncate">{o.tool}</div>
          {o.error && <div className="pl-8 text-xxs text-err">{o.error}</div>}
          {edit?.id === o.id && (
            <div className="pl-8 pt-1.5">
              <textarea className="input !h-40 font-mono text-xxs py-1" value={text} onChange={(e) => setText(e.target.value)} />
              <div className="flex gap-1 mt-1"><button className="btn-primary !h-6" onClick={save}>Zastosuj</button><button className="btn !h-6" onClick={() => setEdit(null)}>Anuluj</button></div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Params() {
  const params = useStore((s) => s.snap?.params || {});
  const units = useStore((s) => s.snap?.units);
  const project = useStore((s) => s.project);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [nk, setNk] = useState(""); const [nv, setNv] = useState("");
  useEffect(() => { setVals(Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))); }, [params]);
  const apply = async (updates: Record<string, unknown>) => {
    if (!project) return;
    try { const r = await api.post<{ snapshot: Snapshot }>(`/api/projects/${project.id}/params`, { updates }); useStore.getState().setSnap(r.snapshot); }
    catch (e) { useStore.getState().notify((e as Error).message, "err"); setVals(Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))); }
  };
  const parse = (v: string) => (v.trim() !== "" && !isNaN(Number(v)) ? Number(v) : v);
  return (
    <div className="p-2 space-y-1">
      <p className="text-xxs text-mute pb-1">Parametry użytkownika — operacje zapisane jako „=wyrażenie” przeliczają się po zmianie (jak User Parameters w Fusion 360).</p>
      {Object.keys(params).length === 0 && <div className="text-mute py-3 text-center">Brak parametrów</div>}
      {Object.entries(vals).map(([k, v]) => {
        const isColor = /^#[0-9a-f]{6}$/i.test(v);
        return (
          <div key={k} className="flex items-center gap-1.5">
            <span className="font-mono text-xxs text-dim w-20 truncate" title={k}>{k}</span>
            {isColor && <input type="color" value={v} className="w-7 h-7 bg-transparent border border-line2 rounded cursor-pointer" onChange={(e) => { setVals({ ...vals, [k]: e.target.value }); apply({ [k]: e.target.value }); }} />}
            <input className="input font-mono" value={v} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") apply({ [k]: parse(v) }); }} onBlur={() => { if (String(params[k]) !== v) apply({ [k]: parse(v) }); }} />
            {!isColor && typeof params[k] === "number" && <span className="text-xxs text-mute w-5">{units}</span>}
          </div>
        );
      })}
      <div className="flex gap-1 pt-2 border-t border-line mt-2">
        <input className="input font-mono" placeholder="nazwa" value={nk} onChange={(e) => setNk(e.target.value.replace(/[^A-Za-z0-9_]/g, ""))} />
        <input className="input font-mono" placeholder="wartość" value={nv} onChange={(e) => setNv(e.target.value)} />
        <button className="btn" disabled={!nk || !nv} onClick={() => { apply({ [nk]: parse(nv) }); setNk(""); setNv(""); }}>+</button>
      </div>
    </div>
  );
}

export function useUpload() {
  const project = useStore((s) => s.project);
  return async (files: FileList | File[]) => {
    if (!project || !files.length) return [];
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    try {
      const r = await api.upload<{ id: string }[]>(`/api/projects/${project.id}/attachments`, fd);
      await useStore.getState().refreshAttachments();
      useStore.getState().notify(`Dodano ${r.length} plik(ów)`, "ok");
      return r.map((x) => x.id);
    } catch (e) { useStore.getState().notify((e as Error).message, "err"); return []; }
  };
}

export function AttachmentThumb({ id, url, mime, className }: { id: string; url: string; mime: string; className?: string }) {
  const [src, setSrc] = useState<string>("");
  useEffect(() => {
    let u = "";
    if (!mime.startsWith("image/")) return;
    api.blob(url).then((b) => { u = URL.createObjectURL(b); setSrc(u); }).catch(() => undefined);
    return () => { if (u) URL.revokeObjectURL(u); };
  }, [id, url, mime]);
  return src ? <img src={src} alt="" className={className} /> : <div className={`${className} bg-panel2`} />;
}

function Files() {
  const attachments = useStore((s) => s.attachments);
  const pending = useStore((s) => s.pendingAttach);
  const project = useStore((s) => s.project);
  const upload = useUpload();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const toggle = (id: string) => useStore.setState({ pendingAttach: pending.includes(id) ? pending.filter((x) => x !== id) : [...pending, id] });
  const remove = async (id: string) => {
    if (!project) return;
    try { await api.del(`/api/projects/${project.id}/attachments/${id}`); await useStore.getState().refreshAttachments(); useStore.setState({ pendingAttach: pending.filter((x) => x !== id) }); }
    catch (e) { useStore.getState().notify((e as Error).message, "err"); }
  };
  return (
    <div className="p-2">
      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={async (e) => { e.preventDefault(); setDrag(false); const ids = await upload(e.dataTransfer.files); useStore.setState({ pendingAttach: [...useStore.getState().pendingAttach, ...ids] }); }}
        onClick={() => input.current?.click()}
        className={`flex flex-col items-center justify-center gap-1 h-20 rounded-lg border border-dashed cursor-pointer transition-colors ${drag ? "border-accent bg-accent/10" : "border-line2 hover:border-mute"}`}>
        <Upload size={16} className="text-dim" />
        <span className="text-dim">Przeciągnij lub kliknij</span>
        <span className="text-xxs text-mute">PNG · JPG · WEBP · SVG — wiele plików</span>
      </div>
      <input ref={input} type="file" multiple accept=".png,.jpg,.jpeg,.webp,.svg" className="hidden"
        onChange={async (e) => { if (e.target.files) { const ids = await upload(e.target.files); useStore.setState({ pendingAttach: [...useStore.getState().pendingAttach, ...ids] }); e.target.value = ""; } }} />
      <div className="grid grid-cols-2 gap-2 mt-2">
        {attachments.map((a) => (
          <div key={a.id} className={`group relative rounded-md overflow-hidden border ${pending.includes(a.id) ? "border-accent" : "border-line2"}`}>
            <button className="block w-full" onClick={() => setPreview(a.id)}>
              <AttachmentThumb id={a.id} url={a.url} mime={a.mime} className="w-full h-20 object-contain bg-[repeating-conic-gradient(#1f2329_0%_25%,#191c21_0%_50%)] bg-[length:12px_12px]" />
            </button>
            <div className="px-1.5 py-1 text-xxs truncate text-dim">{a.filename}</div>
            <div className="absolute top-1 right-1 hidden group-hover:flex gap-0.5">
              <button className="icon-btn !w-5 !h-5 bg-black/60" title="Usuń" onClick={() => remove(a.id)}><X size={11} /></button>
            </div>
            <label className="absolute top-1 left-1 flex items-center gap-1 px-1 rounded bg-black/60 text-xxs cursor-pointer">
              <input type="checkbox" checked={pending.includes(a.id)} onChange={() => toggle(a.id)} /> do AI
            </label>
          </div>
        ))}
      </div>
      {preview && (() => {
        const a = attachments.find((x) => x.id === preview);
        return a ? (
          <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center" onClick={() => setPreview(null)}>
            <div className="bg-panel border border-line rounded-xl p-3 max-w-[70vw]" onClick={(e) => e.stopPropagation()}>
              <AttachmentThumb id={a.id} url={a.url} mime={a.mime} className="max-h-[60vh] max-w-full object-contain" />
              <div className="flex justify-between items-center mt-2 text-dim"><span>{a.filename} · {a.width}×{a.height}px · {(a.size / 1024).toFixed(0)} kB</span>
                <span className="font-mono text-xxs text-mute">attachment_id={a.id}</span></div>
            </div>
          </div>
        ) : null;
      })()}
      <p className="text-xxs text-mute mt-3">Zaznaczone „do AI” załączniki zostaną dołączone do następnego polecenia. Absolutne adresy plików: <span className="font-mono">{absUrl("/api/…")}</span></p>
    </div>
  );
}

const PRESETS = ["car_paint", "chrome", "metal_brushed", "metal_painted", "gold", "rubber", "tire", "carbon_fiber", "glass", "leather", "fabric",
  "wood", "wood_planks", "brick", "concrete", "stone", "asphalt", "tiles", "grass", "bark", "foliage", "skin", "emissive", "rust", "plastic"];

function Materials() {
  const mode = useStore((s) => s.project?.mode);
  const selected = useStore((s) => s.selected);
  const obj = useStore((s) => s.snap?.objects.find((o) => o.name === s.selected));
  const [color, setColor] = useState(obj?.color || "#b3121b");
  const attachments = useStore((s) => s.attachments);
  useEffect(() => { if (obj?.color) setColor(obj.color); }, [obj?.color]);
  if (!selected) return <Empty text="Zaznacz obiekt w viewporcie lub na liście sceny." />;
  if (mode === "cad") {
    return (
      <div className="p-3 space-y-2">
        <div className="text-dim">Kolor bryły <b className="text-fg">{selected}</b> (eksportowany do 3MF/STEP/GLB):</div>
        <div className="flex gap-2"><input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="w-10 h-7 bg-transparent" />
          <button className="btn-primary" onClick={() => runOp("set_color", { target: selected, color }, `Kolor ${selected}`)}>Zastosuj</button></div>
      </div>
    );
  }
  return (
    <div className="p-2">
      <div className="text-dim mb-2">Materiał dla <b className="text-fg">{selected}</b> (i podelementów)</div>
      <div className="flex items-center gap-2 mb-2"><span className="label">Kolor</span><input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="w-10 h-6 bg-transparent" />
        <button className="btn !h-6" onClick={() => runOp("set_material", { target: selected, color }, `Kolor ${selected}`)}>Tylko kolor</button></div>
      <div className="grid grid-cols-2 gap-1">
        {PRESETS.map((p) => (
          <button key={p} className={`btn !h-7 justify-start ${obj?.material === p ? "!border-accent" : ""}`}
            onClick={() => runOp("set_material", { target: selected, material: { preset: p, color: ["chrome", "tire", "carbon_fiber", "gold", "brick", "grass", "bark"].includes(p) ? undefined : color } }, `Materiał ${p} → ${selected}`)}>
            <span className="w-2 h-2 rounded-full" style={{ background: p === "chrome" ? "#dfe4ea" : p === "gold" ? "#d4a93a" : p === "glass" ? "#9fb7c9" : p === "emissive" ? color : "#777" }} />{p}
          </button>
        ))}
      </div>
      {attachments.some((a) => a.mime.startsWith("image/")) && (
        <>
          <div className="label mt-3 mb-1">Tekstura z załącznika (albedo)</div>
          <div className="grid grid-cols-3 gap-1">
            {attachments.filter((a) => a.mime.startsWith("image/")).map((a) => (
              <button key={a.id} title={a.filename} className="border border-line2 rounded overflow-hidden hover:border-accent" onClick={() => runOp("set_material", { target: selected, image: a.id, include_children: false }, `Tekstura ${a.filename}`)}>
                <AttachmentThumb id={a.id} url={a.url} mime={a.mime} className="w-full h-12 object-cover" />
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Versions() {
  const project = useStore((s) => s.project);
  const [rows, setRows] = useState<{ id: string; created: number; label: string; ops: number }[]>([]);
  const [label, setLabel] = useState("");
  const load = () => project && api.get<typeof rows>(`/api/projects/${project.id}/versions`).then(setRows);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [project?.id]);
  if (!project) return null;
  return (
    <div className="p-2">
      <div className="flex gap-1 mb-2"><input className="input" placeholder="Nazwa wersji" value={label} onChange={(e) => setLabel(e.target.value)} />
        <button className="btn-primary" onClick={async () => { await api.post(`/api/projects/${project.id}/versions`, { label: label || "Wersja" }); setLabel(""); load(); useStore.getState().notify("Zapisano wersję", "ok"); }}><Save size={13} /></button></div>
      <p className="text-xxs text-mute mb-2">Każda operacja jest zapisywana automatycznie (autosave). Wersje to nazwane migawki do przywrócenia; po każdym zadaniu AI tworzona jest wersja automatyczna.</p>
      {rows.map((v) => (
        <div key={v.id} className="flex items-center gap-2 py-1.5 border-b border-line/60">
          <div className="flex-1 min-w-0"><div className="truncate">{v.label}</div><div className="text-xxs text-mute">{new Date(v.created * 1000).toLocaleString("pl-PL")} · {v.ops} op.</div></div>
          <button className="btn !h-6" title="Przywróć" onClick={async () => { const r = await api.post<{ snapshot: Snapshot }>(`/api/projects/${project.id}/versions/${v.id}/restore`); useStore.getState().setSnap(r.snapshot); load(); }}><RotateCcw size={12} /></button>
        </div>
      ))}
      {!rows.length && <div className="text-mute text-center py-4">Brak wersji</div>}
    </div>
  );
}

function Inspector() {
  const obj = useStore((s) => s.snap?.objects.find((o) => o.name === s.selected));
  const units = useStore((s) => s.snap?.units);
  const mode = useStore((s) => s.project?.mode);
  if (!obj) return null;
  return (
    <div className="border-t border-line p-2.5 bg-panel2/40">
      <div className="flex items-center justify-between"><span className="label">Zaznaczony</span>
        <button className="icon-btn !w-5 !h-5" title="Usuń obiekt" onClick={() => runOp(mode === "cad" ? "delete_body" : "delete", { target: obj.name }, `Usunięcie ${obj.name}`)}><Trash2 size={12} /></button></div>
      <div className="font-medium truncate mt-0.5">{obj.name}</div>
      {obj.dims && (
        <div className="grid grid-cols-3 gap-1 mt-1.5 font-mono text-xxs">
          {["X", "Y", "Z"].map((a, i) => <div key={a} className="bg-bg rounded px-1.5 py-1 border border-line"><span className="text-mute">{a} </span>{obj.dims![i].toFixed(units === "mm" ? 2 : 3)}</div>)}
        </div>
      )}
      <div className="text-xxs text-mute mt-1.5 flex gap-2 flex-wrap">
        <span>{obj.kind}</span>{obj.material && <span>{obj.material}</span>}{obj.triangles != null && <span>{obj.triangles} tri</span>}
        {obj.volume != null && <span>V={obj.volume.toLocaleString("pl-PL")} mm³</span>}
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) { return <div className="text-mute text-center px-6 py-8">{text}</div>; }
