import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from safenet import DISCLAIMER
from safenet.api import create_app

CONFIG = Path(__file__).resolve().parents[2] / "config/detector.toml"


def test_health_and_openapi_expose_processing_routes(tmp_path, config):
    with TestClient(create_app(CONFIG, tmp_path / "database.sqlite3")) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "phase": 6,
            "detection_ready": True,
            "schema_version": 2,
            "config_fingerprint": config.fingerprint,
            "code_version": "0.1.0",
            "disclaimer": DISCLAIMER,
        }
        assert {"/health", "/ingestions", "/cases", "/summary", "/exports"} <= set(
            client.get("/openapi.json").json()["paths"]
        )
        assert client.post("/analyze", json={"text": "SYNTHETIC-SENTINEL"}).status_code == 404


def test_database_failure_is_sanitized_and_not_reported_healthy(tmp_path, monkeypatch):
    with TestClient(create_app(CONFIG, tmp_path / "database.sqlite3")) as client:

        def broken_connection(*args, **kwargs):
            raise sqlite3.OperationalError("SYNTHETIC-SECRET-SENTINEL")

        monkeypatch.setattr("safenet.api.connect_database", broken_connection)
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "unavailable"
        assert "SYNTHETIC-SECRET-SENTINEL" not in response.text


def test_invalid_configuration_aborts_startup_before_database_creation(tmp_path):
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("schema_version = 99", encoding="utf-8")
    database = tmp_path / "database.sqlite3"
    with pytest.raises(ValidationError), TestClient(create_app(invalid, database)):
        pass
    assert not database.exists()
