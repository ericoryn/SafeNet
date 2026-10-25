import json
from collections import Counter
from uuid import uuid4

import pytest

from safenet.contracts import AnalysisOutcome, IncomingRecord
from safenet.preprocessing import preprocess_record, save_prepared
from safenet.score import score_prepared


def manifest(source, rows):
    (source / "outcomes.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    counts = Counter(row["status"] for row in rows)
    (source / "summary.json").write_text(
        json.dumps(
            {
                **{key: counts[key] for key in ("prepared", "incomplete", "failed")},
                "file_error": None,
            }
        )
    )


def test_scoring_reuses_sanitized_preparation_preserves_failures_and_ranks_stably(
    config, tmp_path, monkeypatch, capsys
):
    source = tmp_path / "prepared"
    source.mkdir()
    ids = []
    for text in ("Тегін байқау.", "Взнос. Случайный выбор.", "Взнос. Случайный выбор."):
        prepared = preprocess_record(
            IncomingRecord(
                provenance="synthetic",
                synthetic=True,
                text=text + " ФИО: Тестов Тест Тестович; +7 000 111 22 33",
            ),
            config,
        )
        save_prepared(prepared, source)
        ids.append(str(prepared.content.case_id))
    rows = [{"case_id": case_id, "status": "prepared"} for case_id in ids]
    rows += [
        {"case_id": str(uuid4()), "status": "incomplete", "error_code": "ocr_failed"},
        {"case_id": str(uuid4()), "status": "failed", "error_code": "invalid_input"},
        {"case_id": str(uuid4()), "status": "prepared"},
    ]
    manifest(source, rows)

    def forbidden(*args, **kwargs):
        pytest.fail("scoring must never rerun OCR")

    monkeypatch.setattr("safenet.ocr.OCRProcessor.recognize", forbidden)
    output = tmp_path / "scored"
    summary = score_prepared(source, output, config)
    assert summary["rule_version"] == "3"
    assert (summary["completed"], summary["incomplete"], summary["failed"]) == (3, 1, 2)
    assert summary["file_error"] is None
    assert summary["levels"]["lottery"] == {"low": 1, "medium": 0, "high": 2}
    outcomes = [
        json.loads(line)
        for line in (output / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for row in outcomes:
        row.pop("case_id")
        parsed = AnalysisOutcome.model_validate(row)
        if parsed.status != "completed":
            assert parsed.result is None
    queue = [json.loads(line) for line in (output / "queue.jsonl").read_text().splitlines()]
    assert [row["case_id"] for row in queue] == sorted(ids[1:]) + ids[:1]
    assert [row["queue_score"] for row in queue] == [85, 85, 0]
    for path in output.iterdir():
        saved = path.read_text(encoding="utf-8")
        assert "Тестов" not in saved and "+7 000 111 22 33" not in saved
    assert "Тестов" not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        score_prepared(source, output, config)


@pytest.mark.parametrize("bad", ["", '{"case_id":"PRIVATE-SENTINEL"}', "[]", "broken"])
def test_invalid_internal_manifest_has_safe_file_error(config, tmp_path, bad):
    source = tmp_path / "prepared"
    source.mkdir()
    manifest(source, [{"case_id": str(uuid4()), "status": "prepared"}])
    (source / "outcomes.jsonl").write_text(bad)
    summary = score_prepared(source, tmp_path / "scored", config)
    assert summary["file_error"] == "invalid_input" and summary["completed"] == 0
    assert "PRIVATE-SENTINEL" not in json.dumps(summary)


def test_duplicate_manifest_is_not_silently_counted_twice(config, tmp_path):
    source = tmp_path / "prepared"
    source.mkdir()
    row = {"case_id": str(uuid4()), "status": "incomplete", "error_code": "ocr_failed"}
    manifest(source, [row, row])
    summary = score_prepared(source, tmp_path / "scored", config)
    assert summary["incomplete"] == 1 and summary["file_error"] == "invalid_input"


def test_upstream_file_errors_and_interrupted_manifests_remain_visible(config, tmp_path):
    source = tmp_path / "prepared"
    source.mkdir()
    row = {"case_id": str(uuid4()), "status": "incomplete", "error_code": "ocr_failed"}
    manifest(source, [row])
    upstream = json.loads((source / "summary.json").read_text())
    upstream["file_error"] = "invalid_input"
    (source / "summary.json").write_text(json.dumps(upstream))
    summary = score_prepared(source, tmp_path / "scored", config)
    assert summary["file_error"] == "invalid_input" and summary["incomplete"] == 1
    upstream.update(file_error=None, incomplete=2)
    (source / "summary.json").write_text(json.dumps(upstream))
    assert score_prepared(source, tmp_path / "partial", config)["file_error"] == "invalid_input"


def test_graph_batch_isolated_snapshot_export_and_base_only_ablation(config, tmp_path):
    source = tmp_path / "prepared"
    source.mkdir()
    rows = []
    for text in (
        "Гарантированный доход. Пригласи друзей.",
        "Гарантированный доход. Успей сегодня.",
    ):
        record = IncomingRecord(
            provenance="synthetic", synthetic=True, text=text + " https://shared.test/"
        )
        content = preprocess_record(record, config)
        save_prepared(content, source)
        rows.append({"case_id": str(record.case_id), "status": "prepared"})
    manifest(source, rows)
    output = tmp_path / "graph"
    summary = score_prepared(source, output, config, graph_scope="demo")
    assert summary["completed"] == 2 and summary["file_error"] is None
    assert summary["graph_enabled"] is True
    assert summary["graph_adjusted_cases"] == summary["graph_adjusted_tracks"] == 1
    assert summary["graph_level_changes"] == 1
    graph = json.loads((output / "graph.json").read_text(encoding="utf-8"))
    assert graph["snapshot"]["edge_count"] == 2
    assert graph["snapshot"]["node_count"] == 3
    assert graph["snapshot"]["scope"] == "demo"
    outcomes = [
        json.loads(line)
        for line in (output / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for row in outcomes:
        row.pop("case_id")
        assert AnalysisOutcome.model_validate(row).result.graph_snapshot is not None
    ablation = score_prepared(source, tmp_path / "base", config)
    assert ablation["levels"]["investment"] == {"low": 0, "medium": 1, "high": 1}
    assert summary["levels"]["investment"] == {"low": 0, "medium": 0, "high": 2}
    assert not (tmp_path / "base/graph.json").exists()
