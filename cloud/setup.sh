#!/usr/bin/env bash
set -euo pipefail
cd /vercel/sandbox
mkdir -p app
if [[ ! -f app/.ready ]]; then
  tar -xzf backend.tgz -C app
  cd app
  uv sync --frozen --no-dev --extra ocr
  .venv/bin/python tools/fetch_ocr_models.py
  if [[ ! -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf && ! -f /usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf ]]; then
    if command -v apt-get >/dev/null; then
      sudo apt-get update -qq
      sudo apt-get install -y fonts-dejavu-core >/dev/null
    else
      sudo dnf install -y dejavu-sans-fonts >/dev/null
    fi
  fi
  touch .ready
else
  cd app
fi
.venv/bin/python cloud/boot.py
