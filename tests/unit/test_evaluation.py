import pytest

from safenet.evaluation.metrics import binary_metrics, calibrate, report


def test_confusion_and_undefined_denominators():
    values = binary_metrics([True, True, False, False], [True, False, True, False])
    assert values["confusion_matrix"] == [[1, 1], [1, 1]]
    assert all(values[k] == 0.5 for k in ("precision", "recall", "f1", "fpr"))
    empty_positive = binary_metrics([False], [False])
    assert empty_positive["precision"] is None
    assert empty_positive["recall"] is None
    assert empty_positive["fpr"] == 0
    with pytest.raises(ValueError):
        binary_metrics([True], [])


def test_calibration_rejects_test_and_metrics_keep_every_case():
    rows = [
        {
            "case_id": str(i),
            "split": "validation",
            "language": "kk",
            "source_kind": "text",
            "scores": {t: score for t in ("lottery", "casino", "investment")},
            "labels": {t: label for t in ("lottery", "casino", "investment")},
        }
        for i, (score, label) in enumerate([(80, "suspicious"), (25, "benign"), (50, "suspicious")])
    ]
    thresholds, _ = calibrate(rows)
    assert thresholds == {"medium": 50.0, "high": 80.0}
    values = report(rows, 50)
    assert values["overall"]["queue"]["tp"] == 2
    assert values["by_language"]["kk"]["cases"] == 3
    assert values["precision_at_k"]["10"] == {"k": 3, "precision": 2 / 3}
    rows[0]["split"] = "test"
    with pytest.raises(ValueError, match="validation"):
        calibrate(rows)
    with pytest.raises(ValueError):
        report(rows + [rows[0]], 50)
