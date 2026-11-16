import { Box, Grid3x3, Move3d, MousePointer2, Rotate3d, Ruler, Scale3d, Sparkles, SunMedium } from "lucide-react";
import { useStore, type Gizmo, type Shading } from "../lib/store";

const SHADING: { id: Shading; label: string; hint: string }[] = [
  { id: "wireframe", label: "Wireframe", hint: "Siatka krawędzi" },
  { id: "solid", label: "Solid", hint: "Jednolite cieniowanie" },
  { id: "material", label: "Material", hint: "Podgląd materiałów PBR + oświetlenie HDRI" },
  { id: "render", label: "Render", hint: "Cienie, odbicia, tło HDRI" },
];
const VIEWS = [["iso", "ISO"], ["front", "Przód"], ["back", "Tył"], ["left", "Lewo"], ["right", "Prawo"], ["top", "Góra"], ["bottom", "Dół"]];
const HDRIS = [["studio", "Studio (lokalne)"], ["city", "City (HDRI)"], ["sunset", "Sunset (HDRI)"], ["warehouse", "Warehouse (HDRI)"], ["forest", "Forest (HDRI)"], ["night", "Night (HDRI)"]];

export default function ViewportToolbar(p: { measuring: boolean; toggleMeasure: () => void; showGrid: boolean; toggleGrid: () => void }) {
  const shading = useStore((s) => s.shading);
  const gizmo = useStore((s) => s.gizmo);
  const hdri = useStore((s) => s.hdri);
  const mode = useStore((s) => s.project?.mode);
  const set = useStore((s) => s.set);
  const setView = useStore((s) => s.setView);
  const GZ: { id: Gizmo; icon: JSX.Element; label: string; cadOk: boolean }[] = [
    { id: "none", icon: <MousePointer2 size={14} />, label: "Zaznaczanie (Q)", cadOk: true },
    { id: "translate", icon: <Move3d size={14} />, label: "Przesuń (W)", cadOk: true },
    { id: "rotate", icon: <Rotate3d size={14} />, label: "Obróć (E)", cadOk: false },
    { id: "scale", icon: <Scale3d size={14} />, label: "Skaluj (R)", cadOk: false },
  ];
  return (
    <>
      <div className="absolute left-3 top-3 flex items-center gap-1 p-1 rounded-lg bg-panel/90 border border-line backdrop-blur">
        {GZ.map((g) => (
          <button key={g.id} title={g.label + (!g.cadOk && mode === "cad" ? " — w CAD użyj poleceń (obrót wokół środka bryły)" : "")}
            disabled={!g.cadOk && mode === "cad"} onClick={() => set({ gizmo: g.id })}
            className={`icon-btn ${gizmo === g.id ? "!bg-accent/20 !text-accent" : ""}`}>{g.icon}</button>
        ))}
        <div className="w-px h-5 bg-line mx-0.5" />
        <button title="Pomiar odległości" onClick={p.toggleMeasure} className={`icon-btn ${p.measuring ? "!bg-[#ffd166]/20 !text-[#ffd166]" : ""}`}><Ruler size={14} /></button>
        <button title="Siatka" onClick={p.toggleGrid} className={`icon-btn ${p.showGrid ? "!text-fg" : ""}`}><Grid3x3 size={14} /></button>
      </div>
      <div className="absolute right-3 top-3 flex items-center gap-1">
        <div className="flex p-1 rounded-lg bg-panel/90 border border-line backdrop-blur">
          {VIEWS.map(([id, l]) => (
            <button key={id} onClick={() => setView(id)} className="btn-ghost !h-6 !px-1.5 text-xxs" title={`Widok: ${l}`}>{l}</button>
          ))}
        </div>
        <div className="flex p-1 rounded-lg bg-panel/90 border border-line backdrop-blur">
          {SHADING.map((s) => (
            <button key={s.id} title={s.hint} onClick={() => set({ shading: s.id })}
              className={`h-6 px-2 rounded-md text-xxs transition-colors flex items-center gap-1 ${shading === s.id ? "bg-accent text-white" : "text-dim hover:text-fg hover:bg-panel2"}`}>
              {s.id === "wireframe" ? <Grid3x3 size={11} /> : s.id === "solid" ? <Box size={11} /> : s.id === "material" ? <SunMedium size={11} /> : <Sparkles size={11} />}
              {s.label}
            </button>
          ))}
        </div>
        {(shading === "material" || shading === "render") && (
          <select value={hdri} onChange={(e) => set({ hdri: e.target.value })} title="Oświetlenie HDRI (presety HDRI pobierane z CDN)"
            className="h-8 rounded-lg bg-panel border border-line text-xxs text-dim px-2 outline-none hover:text-fg">
            {HDRIS.map(([id, l]) => <option key={id} value={id}>{l}</option>)}
          </select>
        )}
      </div>
    </>
  );
}
