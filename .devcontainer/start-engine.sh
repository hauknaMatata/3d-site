#!/usr/bin/env bash
# Starts the engine in the background and makes port 8000 public so the GitHub Pages site can reach it.
cd "$(dirname "$0")/../backend"
export DATA_DIR="${DATA_DIR:-$HOME/ai3d-data}"
export CORS_ORIGINS="${CORS_ORIGINS:-https://hauknamatata.github.io,http://localhost:5173}"
if ! curl -s localhost:8000/api/health > /dev/null 2>&1; then
  nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' \
    > /tmp/ai3d-engine.log 2>&1 &
fi
for _ in $(seq 1 60); do curl -s localhost:8000/api/health > /dev/null 2>&1 && break; sleep 1; done
if [ -n "${CODESPACE_NAME:-}" ]; then
  gh codespace ports visibility 8000:public -c "$CODESPACE_NAME" > /dev/null 2>&1 || \
    echo "!! Ustaw ręcznie: zakładka PORTS → port 8000 → prawy klik → Port Visibility → Public"
  URL="https://${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
  echo "$URL" > /tmp/ai3d-engine-url
  echo ""
  echo "=============================================================================="
  echo " Silnik AI 3D Studio działa:  $URL"
  echo " Strona:  https://hauknamatata.github.io/3d-site/?engine=$URL"
  echo " MCP dla agentów AI:  $URL/mcp/"
  echo "=============================================================================="
fi
