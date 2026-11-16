import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_BASE lets the same build be served from GitHub Pages (/<repo>/) or a domain root.
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", ws: true },
      "/mcp": { target: "http://localhost:8000" },
    },
  },
  build: { chunkSizeWarningLimit: 2000 },
});
