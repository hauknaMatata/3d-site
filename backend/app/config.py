"""Runtime configuration (environment variables only — never hard-code secrets)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "studio.sqlite3"
FILES_DIR = DATA_DIR / "files"
RENDER_DIR = DATA_DIR / "renders"
EXPORT_DIR = DATA_DIR / "exports"
for _d in (DATA_DIR, FILES_DIR, RENDER_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Used to encrypt provider API keys + MCP headers at rest. MUST be set in production.
SECRET_KEY = os.environ.get("SECRET_KEY", "")
# The GitHub Pages frontend talks to an engine running on the user's own machine, so it is allowed by default.
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,https://hauknamatata.github.io").split(",") if o.strip()]
# Optional shared token protecting the whole API (recommended when the backend is public).
API_TOKEN = os.environ.get("API_TOKEN", "")
# Delay (seconds) between live-build steps so the viewport can animate each real operation.
LIVE_BUILD_STEP_DELAY = float(os.environ.get("LIVE_BUILD_STEP_DELAY", "0.35"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
ASSIMP_BIN = os.environ.get("ASSIMP_BIN", "assimp")
