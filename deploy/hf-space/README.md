---
title: AI 3D Studio Engine
emoji: 🧊
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
short_description: CAD/GAME 3D engine, AI team and MCP server for AI 3D Studio
---

# AI 3D Studio — engine

Backend of [AI 3D Studio](https://github.com/hauknaMatata/3d-site): FastAPI + OpenCascade (CadQuery) CAD kernel,
PBR mesh engine, exporters, AI TEAM orchestrator and MCP server (`/mcp/`).

The web UI is hosted on GitHub Pages and talks to this Space. This repository is deployed automatically from GitHub
(`.github/workflows/deploy-engine.yml`) — do not edit it here.

Optional Space secrets (Settings → Variables and secrets): `SECRET_KEY` (encrypts stored provider keys),
`API_TOKEN` (protects the API, the web UI and MCP clients must then send it).
