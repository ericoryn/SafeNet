import time
from pathlib import Path

from fastapi.testclient import TestClient

from safenet.site import create_site


def test_one_server_has_ui_api_languages_seed_and_restart(tmp_path):
    database = tmp_path / "demo.sqlite3"
    for _restart in range(2):
        with TestClient(create_site(database_path=database)) as client:
            assert client.get("/").status_code == 200
            assert 'data-locale="kk"' in client.get("/").text
            assert client.get("/locales/kk.json").json()["Материалы"] == "Материалдар"
            assert client.get("/backend/health").json()["detection_ready"]
            assert client.get("/../config/detector.toml").status_code == 404
            until = time.monotonic() + 5
            while time.monotonic() < until:
                summary = client.get("/backend/summary").json()
                if summary["statuses"].get("completed") == 6:
                    break
                time.sleep(0.02)
            assert summary["cases"] == 6
            assert summary["statuses"]["completed"] == 6
            assert Path(database).is_file()
