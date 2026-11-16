import { useEffect, useState } from "react";
import AIPanel from "./components/AIPanel";
import ExportDialog from "./components/ExportDialog";
import LeftPanel from "./components/LeftPanel";
import LiveBuild from "./components/LiveBuild";
import SettingsDialog from "./components/SettingsDialog";
import { Confirms, Home, Toast, TopBar } from "./components/Shell";
import Viewport from "./components/Viewport";
import { api } from "./lib/api";
import { useStore } from "./lib/store";
import type { Snapshot } from "./lib/types";

export default function App() {
  const project = useStore((s) => s.project);
  const [exportOpen, setExportOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [bottomCollapsed, setBottomCollapsed] = useState(false);

  useEffect(() => {
    let last = "";
    try { last = localStorage.getItem("ai3d.lastProject") || ""; } catch { /* */ }
    if (last) useStore.getState().open(last).catch(() => { try { localStorage.removeItem("ai3d.lastProject"); } catch { /* */ } useStore.getState().close(); });
  }, []);

  useEffect(() => {
    const onKey = async (e: KeyboardEvent) => {
      const tgt = e.target as HTMLElement;
      if (tgt.tagName === "INPUT" || tgt.tagName === "TEXTAREA" || tgt.tagName === "SELECT") return;
      const st = useStore.getState();
      if (!st.project) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (st.job?.status === "running") return;
        const r = await api.post<{ snapshot: Snapshot }>(`/api/projects/${st.project.id}/${e.shiftKey ? "redo" : "undo"}`).catch(() => null);
        if (r) st.setSnap(r.snapshot);
      } else if (e.key === "Escape") st.select(null);
      else if (e.key === "q") st.set({ gizmo: "none" });
      else if (e.key === "w") st.set({ gizmo: "translate" });
      else if (e.key === "e" && st.project.mode === "game") st.set({ gizmo: "rotate" });
      else if (e.key === "r" && st.project.mode === "game") st.set({ gizmo: "scale" });
      else if (e.key === "f") st.setView("iso");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {project ? (
        <>
          <TopBar onExport={() => setExportOpen(true)} onSettings={() => setSettingsOpen(true)} />
          <div className="flex-1 min-h-0 grid grid-cols-[minmax(240px,280px)_minmax(0,1fr)_minmax(300px,360px)] max-lg:grid-cols-[1fr] max-lg:grid-rows-[auto]">
            <div className="min-h-0 max-lg:hidden"><LeftPanel /></div>
            <div className="min-h-0 min-w-0 flex flex-col max-lg:h-[55vh]">
              <div className="flex-1 min-h-0"><Viewport /></div>
              <div className={bottomCollapsed ? "h-8" : "h-56"}><LiveBuild collapsed={bottomCollapsed} toggle={() => setBottomCollapsed((c) => !c)} /></div>
            </div>
            <div className="min-h-0 max-lg:h-[60vh]"><AIPanel /></div>
          </div>
        </>
      ) : <Home onSettings={() => setSettingsOpen(true)} />}
      {exportOpen && <ExportDialog onClose={() => setExportOpen(false)} />}
      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
      <Confirms />
      <Toast />
    </div>
  );
}
