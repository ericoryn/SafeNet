import pytest

from tools.feedback import feedback_rows


def test_feedback_uses_latest_decision_and_rejects_partial_export():
    rows = [
        {"record_type": "begin"},
        {"record_type": "case", "data": {"case_id": "c", "sanitized_text": "Тест"}},
        {
            "record_type": "result",
            "data": {
                "case_id": "c",
                "result_id": "r",
                "version": 1,
                "run": {"config_fingerprint": "f"},
            },
        },
        {
            "record_type": "decision",
            "data": {
                "case_id": "c",
                "result_id": "r",
                "decision_id": "a",
                "created_at": "2026-01-01",
                "action": "false_positive",
            },
        },
        {
            "record_type": "decision",
            "data": {
                "case_id": "c",
                "result_id": "r",
                "decision_id": "b",
                "created_at": "2026-01-02",
                "action": "confirm_signs",
            },
        },
        {
            "record_type": "end",
            "complete": True,
            "counts": {"case": 1, "result": 1, "decision": 2, "job": 0},
        },
    ]
    result = feedback_rows(rows)
    assert len(result) == 1
    assert result[0]["decision_id"] == "b"
    assert result[0]["queue_label"] == "suspicious"
    assert result[0]["requires_track_annotation"] and result[0]["labels"] is None
    with pytest.raises(ValueError):
        feedback_rows(rows[:-1])
    rows[-2]["data"]["action"] = "request_review"
    assert feedback_rows(rows) == []
