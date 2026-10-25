import hashlib
import io
import subprocess

import pytest

from tools import fetch_ocr_models
from tools.ocr_smoke import character_error_rate, run_check


@pytest.mark.parametrize(
    "expected,actual,value",
    [
        ("abc", "abc", 0),
        ("abc", "axc", 1 / 3),
        ("abc", "ab", 1 / 3),
        ("abc", "abcd", 1 / 3),
        ("ҚАТЫСУ\nтегін", "қатысу тегін", 0),
        ("abc", "", 1),
    ],
)
def test_character_error_rate(expected, actual, value):
    assert character_error_rate(expected, actual) == pytest.approx(value)


def test_empty_reference_rejected():
    with pytest.raises(ValueError):
        character_error_rate("", "anything")


def test_downloader_checks_hash_and_reuses_verified_files(tmp_path, monkeypatch):
    payload = b"synthetic model bytes"
    monkeypatch.setattr(
        fetch_ocr_models,
        "MODEL_HASHES",
        {
            "rus": hashlib.sha256(payload).hexdigest(),
        },
    )
    calls = []

    def response(url, **kwargs):
        calls.append(url)
        return io.BytesIO(payload)

    monkeypatch.setattr(fetch_ocr_models, "urlopen", response)
    fetch_ocr_models.fetch_models(tmp_path)
    fetch_ocr_models.fetch_models(tmp_path)
    assert len(calls) == 1
    assert (tmp_path / "rus.traineddata").read_bytes() == payload


@pytest.mark.parametrize("oversized", [False, True])
def test_untrusted_model_download_is_not_persisted(tmp_path, monkeypatch, oversized):
    monkeypatch.setattr(fetch_ocr_models, "MAX_MODEL_BYTES", 5 if oversized else 100)
    monkeypatch.setattr(fetch_ocr_models, "urlopen", lambda *a, **kw: io.BytesIO(b"bad model"))
    with pytest.raises(ValueError):
        fetch_ocr_models.fetch_models(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_native_ocr_timeout_propagates(tmp_path, monkeypatch):
    pytest.importorskip("tesserocr")
    for language in fetch_ocr_models.MODEL_HASHES:
        (tmp_path / f"{language}.traineddata").write_bytes(b"synthetic")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic-worker", 20)

    monkeypatch.setattr("tools.ocr_smoke.subprocess.run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        run_check(tmp_path, tmp_path / "report.json")
