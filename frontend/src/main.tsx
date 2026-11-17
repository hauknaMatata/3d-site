import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { apiToken, setConnection } from "./lib/api";

// ?engine=https://<codespace>-8000.app.github.dev  → remember the engine URL (link printed by the Codespace)
const q = new URLSearchParams(window.location.search);
const engine = q.get("engine");
if (engine && /^https?:\/\/[^\s]+$/.test(engine)) {
  setConnection(engine.replace(/\/$/, ""), apiToken());
  q.delete("engine");
  window.history.replaceState(null, "", window.location.pathname + (q.toString() ? `?${q}` : "") + window.location.hash);
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
