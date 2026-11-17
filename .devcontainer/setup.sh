#!/usr/bin/env bash
# One-time install inside the codespace (≈3–5 min on first creation).
set -euo pipefail
sudo apt-get update -qq
sudo apt-get install -y -qq assimp-utils libgl1 libglu1-mesa libxrender1 libxext6 libcairo2 fonts-dejavu-core > /dev/null
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt
echo "AI 3D Studio engine installed."
