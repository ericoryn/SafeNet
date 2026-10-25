import io
import json
from uuid import uuid4

from PIL import Image

from safenet.contracts import IncomingRecord
from safenet.prepare import prepare_file


def test_batch_keeps_failures_explicit_and_persists_only_safe_cases(config, tmp_path, capsys):
    from pathlib import Path

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "PRIVATE-FILENAME-SENTINEL.png").write_bytes(b"PRIVATE-PIXEL-SENTINEL")
    blank = io.BytesIO()
    Image.new("RGB", (300, 150), "white").save(blank, format="PNG")
    (assets / "blank.png").write_bytes(blank.getvalue())
    records = [
        IncomingRecord(
            provenance="synthetic", synthetic=True, text="ФИО: Тестов Тест Тестович; взнос 5000 тг."
        )
    ]
    for filename in ("PRIVATE-FILENAME-SENTINEL.png", "blank.png"):
        records.append(
            IncomingRecord(
                provenance="synthetic",
                synthetic=True,
                source_kind="screenshot",
                image_path=filename,
                artifact_id=uuid4(),
            )
        )
    source = tmp_path / "input.jsonl"
    source.write_text("".join(row.model_dump_json() + "\n" for row in records), encoding="utf-8")
    output = tmp_path / "prepared"
    summary = prepare_file(
        source,
        output,
        config,
        image_root=assets,
        tessdata=Path(__file__).resolve().parents[2] / ".tools/tessdata",
    )
    assert summary["prepared"] == 1
    assert summary["failed"] == 1
    assert summary["incomplete"] == 1
    assert summary["file_error"] is None
    assert summary["detection_ready"] is False
    outcomes = [json.loads(line) for line in (output / "outcomes.jsonl").read_text().splitlines()]
    assert [item["status"] for item in outcomes] == ["prepared", "failed", "incomplete"]
    assert all("score" not in row for row in outcomes)
    for path in output.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            assert "PRIVATE" not in content
            assert "Тестов" not in content
    assert "PRIVATE" not in capsys.readouterr().out


def test_malformed_batch_reports_file_error_without_input_contents(config, tmp_path):
    source = tmp_path / "bad.jsonl"
    source.write_text('{"text":"PRIVATE-SENTINEL"', encoding="utf-8")
    summary = prepare_file(source, tmp_path / "out", config, image_root=tmp_path, tessdata=tmp_path)
    assert summary["file_error"] == "invalid_input"
    assert summary["prepared"] == 0
    assert "PRIVATE-SENTINEL" not in json.dumps(summary)
