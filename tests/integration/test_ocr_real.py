from pathlib import Path

import pytest

from tools.fetch_ocr_models import MODEL_HASHES
from tools.ocr_smoke import run_check

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.ocr
def test_real_russian_and_kazakh_screenshot_extraction(tmp_path):
    pytest.importorskip("tesserocr")
    models = ROOT / ".tools/tessdata"
    if not all((models / f"{language}.traineddata").exists() for language in MODEL_HASHES):
        pytest.skip("run tools/fetch_ocr_models.py before the real OCR check")
    report = run_check(models, tmp_path / "ocr.json")
    assert report["model_sha256"] == MODEL_HASHES
    assert {sample["language"] for sample in report["samples"]} == {"ru", "kk"}
    assert all(sample["nonempty"] for sample in report["samples"])
    assert all(sample["synthetic"] for sample in report["samples"])
