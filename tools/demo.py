"""python tools/demo.py: подготовить окружение, OCR и запустить сайт с API."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Установите uv: https://docs.astral.sh/uv/getting-started/installation/")
    subprocess.run([uv, "sync", "--frozen", "--extra", "ocr"], check=True)
    python = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    subprocess.run([str(python), "tools/fetch_ocr_models.py"], check=True)
    environment = {**os.environ, "SAFENET_DB_PATH": str(ROOT / "data/demo.sqlite3")}
    print("SafeNet KZ: http://127.0.0.1:" + os.environ.get("SAFENET_PORT", "8501"), flush=True)
    subprocess.run(
        [
            str(python),
            "-m",
            "uvicorn",
            "safenet.site:app",
            "--host",
            "127.0.0.1",
            "--port",
            os.environ.get("SAFENET_PORT", "8501"),
            "--workers",
            "1",
            "--no-access-log",
        ],
        env=environment,
        check=True,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
