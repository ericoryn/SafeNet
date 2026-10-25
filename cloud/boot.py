"""Запуск единственного API в среде Vercel с постоянным диском."""

import fcntl
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path("/vercel/sandbox/app")
DATA = Path("/data")


def start():
    DATA.mkdir(exist_ok=True)
    secret = DATA / ".access-token"
    if not secret.exists():
        descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as target:
            target.write(secrets.token_urlsafe(48))
    token = secret.read_text().strip()

    def ready():
        try:
            request = Request("http://127.0.0.1:8000/health", headers={"X-SafeNet-Access": token})
            with urlopen(request, timeout=2) as response:
                return json.load(response).get("detection_ready", False)
        except (URLError, TimeoutError):
            return False

    if ready():
        return
    environment = {
        **os.environ,
        "SAFENET_ACCESS_TOKEN": token,
        "SAFENET_DB_PATH": str(DATA / "safenet.sqlite3"),
        "SAFENET_CONFIG": str(ROOT / "config/calibrated.toml"),
        "SAFENET_TESSDATA": str(ROOT / ".tools/tessdata"),
    }
    with (DATA / "api.log").open("ab") as output:
        process = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "safenet.api:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--root-path",
                "/backend",
                "--workers",
                "1",
                "--limit-concurrency",
                "32",
                "--no-access-log",
            ],
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
    for _ in range(100):
        if ready():
            return
        if process.poll() is not None:
            raise RuntimeError("API не удалось запустить; журнал находится на постоянном диске")
        time.sleep(0.1)
    raise TimeoutError("Истекло время запуска API")


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    with (DATA / ".startup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start()
