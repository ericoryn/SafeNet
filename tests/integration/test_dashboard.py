import io
import json
from pathlib import Path
from urllib.error import URLError

import pytest
from streamlit.testing.v1 import AppTest

from safenet import DISCLAIMER

APP = Path(__file__).resolve().parents[2] / "dashboard/app.py"


@pytest.mark.parametrize("available", [True, False])
def test_dashboard_shell_and_connectivity_message(monkeypatch, config, available):
    def health_response(*args, **kwargs):
        if not available:
            raise URLError("SYNTHETIC-PRIVATE-SENTINEL")
        return io.BytesIO(
            json.dumps(
                {
                    "status": "ok",
                    "schema_version": 1,
                    "config_fingerprint": config.fingerprint,
                    "code_version": "0.1.0",
                }
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", health_response)
    app = AppTest.from_file(str(APP)).run(timeout=10)
    assert not app.exception
    assert app.info[0].value == DISCLAIMER
    assert "этапе 7" in app.warning[0].value
    assert len(app.success) == int(available)
    assert len(app.error) == int(not available)
    assert all("SYNTHETIC-PRIVATE-SENTINEL" not in element.value for element in app.error)
