import { CheckCircle2, Download, Loader2, X, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, download } from "../lib/api";
import { useStore } from "../lib/store";

interface Fmt { label: string; ext: string; units: string; materials: boolean; textures: boolean; note: string; cad_only?: boolean }
interface Result { filename: string; size: number; url: string; format: string; validation: Record<string, unknown> }

const TARGETS: [string, string][] = [["bambu", "Bambu Lab"], ["prusa", "PrusaSlicer"], ["blender", "Blender"], ["roblox", "Roblox Studio"],
  ["unity", "Unity"], ["unreal", "Unreal Engine"], ["web", "Web 3D"], ["cad", "CAD (Fusion/SolidWorks)"], ["ar", "AR (iOS/Android)"]];

export default function ExportDialog({ onClose }: { onClose: () => void }) {
  const project = useStore((s) => s.project);
  const selected = useStore((s) => s.selected);
  const [formats, setFormats] = useState<Record<string, Fmt>>({});
  const [sugg, setSugg] = useState<Record<string, { formats: string[]; why: string }>>({});
  const [target, setTarget] = useState<string>(project?.mode === "cad" ? "bambu" : "blender");
  const [fmt, setFmt] = useState<string>("");
  const [o, setO] = useState({ quality: "normal", texture_res: 1024, materials: true, textures: true, animation: false, scope: "all", units: "", scale: 1, compression: true, include_lods: true });
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<{ formats: Record<string, Fmt>; suggestions: typeof sugg }>("/api/export/formats").then((r) => { setFormats(r.formats); setSugg(r.suggestions); }); }, []);
  useEffect(() => { const f = sugg[target]?.formats.find((x) => !(formats[x]?.cad_only && project?.mode !== "cad")); if (f) setFmt(f); }, [target, sugg, formats, project?.mode]);
  const f = formats[fmt];
  const units = o.units || f?.units || "";
  const disabledReason = (k: string) => (formats[k]?.cad_only && project?.mode !== "cad" ? "STEP wymaga geometrii B-Rep (tylko projekty CAD)" : "");
  const zipFmt = useMemo(() => ["obj", "fbx", "gltf"].includes(fmt), [fmt]);

  const run = async () => {
    if (!project || !fmt) return;
    setBusy(true); setErr(""); setRes(null);
    try {
      const r = await api.post<Result>(`/api/projects/${project.id}/export`, {
        format: fmt, quality: o.quality, texture_res: o.texture_res, materials: o.materials, textures: o.textures, animation: o.animation,
        selection: o.scope === "selected" && selected ? [selected] : null, units: o.units || null, scale: o.scale, compression: o.compression, include_lods: o.include_lods,
      });
      setRes(r);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const Toggle = ({ k, label, disabled, hint }: { k: "materials" | "textures" | "animation" | "compression" | "include_lods"; label: string; disabled?: boolean; hint?: string }) => (
    <label className={`flex items-center gap-2 ${disabled ? "opacity-40" : ""}`} title={hint}>
      <input type="checkbox" disabled={disabled} checked={o[k] && !disabled} onChange={(e) => setO({ ...o, [k]: e.target.checked })} />{label}
    </label>
  );
  return (
    <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px] flex items-center justify-center p-4" onClick={onClose}>
      <div className="w-full max-w-3xl max-h-[92vh] overflow-y-auto bg-panel border border-line2 rounded-xl shadow-2xl slide-in" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 h-11 border-b border-line">
          <div className="font-medium">Eksport modelu</div><button className="icon-btn" onClick={onClose}><X size={15} /></button>
        </div>
        <div className="p-4 grid grid-cols-1 md:grid-cols-[1fr_260px] gap-5">
          <div>
            <div className="label mb-1.5">Przeznaczenie</div>
            <div className="flex flex-wrap gap-1 mb-2">
              {TARGETS.map(([k, l]) => <button key={k} onClick={() => setTarget(k)} className={`btn !h-6 text-xxs ${target === k ? "!border-accent !text-fg bg-accent/15" : "text-dim"}`}>{l}</button>)}
            </div>
            {sugg[target] && <div className="text-xxs text-dim mb-3">Sugestia: <b className="text-fg">{sugg[target].formats.map((x) => x.toUpperCase()).join(" lub ")}</b> — {sugg[target].why}</div>}
            <div className="label mb-1.5">Format</div>
            <div className="grid grid-cols-3 gap-1.5">
              {Object.entries(formats).map(([k, v]) => {
                const dis = disabledReason(k);
                const rec = sugg[target]?.formats.includes(k);
                return (
                  <button key={k} disabled={!!dis} title={dis || v.note} onClick={() => setFmt(k)}
                    className={`text-left rounded-lg border px-2.5 py-2 transition-colors disabled:opacity-35 ${fmt === k ? "border-accent bg-accent/10" : "border-line2 hover:border-mute"}`}>
                    <div className="flex items-center justify-between"><span className="font-medium">{v.label}</span>{rec && <span className="text-xxs text-accent">poleć.</span>}</div>
                    <div className="text-xxs text-mute">{v.materials ? (v.textures ? "geometria + materiały + tekstury" : "geometria + kolory") : "tylko geometria"}</div>
                  </button>
                );
              })}
            </div>
            {f && <div className="mt-3 text-xxs text-dim border-l-2 border-accent/60 pl-2">{f.note}</div>}
          </div>
          <div className="space-y-3">
            <div>
              <div className="label mb-1">Zakres</div>
              <select className="input" value={o.scope} onChange={(e) => setO({ ...o, scope: e.target.value })}>
                <option value="all">Cały projekt (widoczne obiekty)</option>
                <option value="selected" disabled={!selected}>Zaznaczony obiekt{selected ? `: ${selected}` : ""}</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div><div className="label mb-1">Jednostki</div>
                <select className="input" value={o.units} onChange={(e) => setO({ ...o, units: e.target.value })}>
                  <option value="">{f ? `domyślne (${f.units})` : "domyślne"}</option>{["mm", "cm", "m", "in"].map((u) => <option key={u}>{u}</option>)}
                </select></div>
              <div><div className="label mb-1">Skala</div><input className="input" type="number" step="0.1" min="0.001" value={o.scale} onChange={(e) => setO({ ...o, scale: Number(e.target.value) || 1 })} /></div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div><div className="label mb-1" title="Tolerancja teselacji B-Rep (CAD)">Jakość geometrii</div>
                <select className="input" value={o.quality} onChange={(e) => setO({ ...o, quality: e.target.value })} disabled={project?.mode !== "cad" || fmt === "step"}>
                  <option value="draft">Robocza</option><option value="normal">Normalna</option><option value="fine">Wysoka</option>
                </select></div>
              <div><div className="label mb-1">Tekstury</div>
                <select className="input" value={o.texture_res} onChange={(e) => setO({ ...o, texture_res: Number(e.target.value) })} disabled={project?.mode !== "game" || !f?.textures}>
                  {[512, 1024, 2048, 4096].map((r) => <option key={r} value={r}>{r} px</option>)}
                </select></div>
            </div>
            <div className="space-y-1.5 pt-1">
              <Toggle k="materials" label="Eksportuj materiały / kolory" disabled={!f?.materials} />
              <Toggle k="textures" label="Eksportuj tekstury" disabled={!f?.textures || project?.mode !== "game"} />
              <Toggle k="include_lods" label="Dołącz LOD (*_LOD0/1/2)" disabled={project?.mode !== "game" || fmt === "step"} />
              <Toggle k="compression" label="Kompresja ZIP" disabled={!zipFmt} hint="Dotyczy pakietów OBJ/FBX/glTF" />
              <Toggle k="animation" label="Eksportuj animacje" disabled hint="Scena nie zawiera animacji (moduł AI Animation planowany)" />
            </div>
            <div className="text-xxs text-mute">Wyjście: {units} · {fmt.toUpperCase()}</div>
          </div>
        </div>
        <div className="px-4 pb-4">
          {err && <div className="mb-2 text-err flex items-center gap-1.5"><XCircle size={14} />{err}</div>}
          {res && (
            <div className="mb-3 rounded-lg border border-line2 bg-bg p-3">
              <div className="flex items-center gap-2">
                {res.validation.ok ? <CheckCircle2 size={15} className="text-ok" /> : <XCircle size={15} className="text-err" />}
                <span className="font-medium">{res.filename}</span><span className="text-mute">{(res.size / 1024).toFixed(1)} kB</span>
                <button className="btn-primary ml-auto" onClick={() => download(res.url, res.filename)}><Download size={13} /> Pobierz</button>
              </div>
              <div className="mt-2 text-xxs text-dim font-mono break-all">
                Walidacja (ponowny import): {Object.entries(res.validation).filter(([k]) => k !== "note").map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("×") : String(v)}`).join(" · ")}
              </div>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button className="btn" onClick={onClose}>Zamknij</button>
            <button className="btn-primary" disabled={!fmt || busy || !!disabledReason(fmt)} onClick={run}>{busy ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />} Eksportuj {fmt.toUpperCase()}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
