import hashlib
import json
import re
import shutil
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from safenet.evaluation.generate import (
    SPLITS,
    STYLES,
    GroundTruth,
    SyntheticInput,
    audit_dataset,
    canonical_text,
    family_assignments,
    generate_dataset,
    read_jsonl,
    surface_variant,
)
from safenet.evaluation.templates import POSITIVE_TRACKS, TEMPLATES


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic") / "dataset"
    generate_dataset(root, count=600, seed=42)
    return root


def write_rows(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def json_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_grouped_split_counts_coverage_and_manifest_hashes(dataset):
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    audit = audit_dataset(dataset)
    assert audit["total_records"] == 600
    assert [audit["splits"][split]["records"] for split in SPLITS] == [360, 120, 120]
    assert audit["cross_split_overlap"] == dict.fromkeys(
        ("family", "campaign", "account", "entity", "content", "image"),
        0,
    )
    assert audit["detector_evaluated"] is False
    for split in SPLITS:
        summary = audit["splits"][split]
        assert set(summary["languages"]) == {"ru", "kk", "mixed"}
        assert set(summary["styles"]) == set(STYLES)
        assert set(summary["source_kinds"]) == {"text", "transcript", "screenshot"}
        assert set(summary["scenarios"]) == set(TEMPLATES)
        assert summary["overlapping_track_records"] > 0
        for counts in summary["track_labels"].values():
            assert counts["suspicious"] > 0 and counts["benign"] > 0
    assert len(list((dataset / "images").glob("*.png"))) == 200
    for name, expected_hash in manifest["file_sha256"].items():
        assert hashlib.sha256((dataset / name).read_bytes()).hexdigest() == expected_hash


def test_repeated_generation_is_byte_identical(dataset, tmp_path):
    second = tmp_path / "repeat"
    generate_dataset(second, count=600, seed=42)
    left = {
        path.relative_to(dataset).as_posix(): path.read_bytes()
        for path in dataset.rglob("*")
        if path.is_file()
    }
    right = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert left == right


def test_configurable_count_and_different_seed(tmp_path):
    root = tmp_path / "different-seed"
    manifest = generate_dataset(root, count=601, seed=73)
    assert manifest["total_records"] == 601
    assert sum(split["records"] for split in manifest["splits"].values()) == 601
    assert family_assignments(73) != family_assignments(42)
    assert audit_dataset(root)["cross_split_overlap"]["family"] == 0


@pytest.mark.parametrize("count", [0, 599, 10001, True, 600.5])
def test_invalid_counts_do_not_create_output(tmp_path, count):
    path = tmp_path / "invalid"
    with pytest.raises(ValueError):
        generate_dataset(path, count=count)
    assert not path.exists()


def test_generator_never_overwrites_existing_data(dataset):
    before = (dataset / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        generate_dataset(dataset, count=600)
    assert (dataset / "manifest.json").read_bytes() == before


def test_missing_font_fails_before_output_creation(tmp_path):
    output = tmp_path / "invalid-font"
    with pytest.raises(OSError):
        generate_dataset(output, font_path=tmp_path / "missing.ttf")
    assert not output.exists()


def test_detector_inputs_exclude_evaluator_metadata_and_ocr_answers(dataset):
    forbidden = {
        "labels",
        "label",
        "track",
        "scenario",
        "family_id",
        "campaign_id",
        "split",
        "style",
        "language",
        "ocr_reference",
    }
    for split in SPLITS:
        inputs = read_jsonl(dataset / "inputs" / f"{split}.jsonl", SyntheticInput)
        targets = {
            row.case_id: row
            for row in read_jsonl(
                dataset / "ground_truth" / f"{split}.jsonl",
                GroundTruth,
            )
        }
        for row in inputs:
            assert not forbidden.intersection(row.model_dump())
            assert row.synthetic is True
            target = targets[row.case_id]
            assert {
                track for track, label in target.labels.items() if label == "suspicious"
            } == set(
                POSITIVE_TRACKS.get(target.scenario, ()),
            )
            if row.image_path:
                assert row.text == ""
                assert target.ocr_reference
                with Image.open(dataset / row.image_path) as image:
                    assert image.format == "PNG"
                    assert image.width == 1600
                    image.verify()


@pytest.mark.parametrize("field", ["family_id", "campaign_id"])
def test_auditor_detects_group_leakage(dataset, tmp_path, field):
    root = tmp_path / "corrupt"
    shutil.copytree(dataset, root)
    train = json_rows(root / "ground_truth/train.jsonl")
    validation_path = root / "ground_truth/validation.jsonl"
    validation = json_rows(validation_path)
    validation[0][field] = train[0][field]
    write_rows(validation_path, validation)
    with pytest.raises(ValueError, match="cross-split"):
        audit_dataset(root)


def test_auditor_detects_shared_accounts(dataset, tmp_path):
    root = tmp_path / "corrupt"
    shutil.copytree(dataset, root)
    train = json_rows(root / "inputs/train.jsonl")
    validation_path = root / "inputs/validation.jsonl"
    validation = json_rows(validation_path)
    validation[0]["synthetic_account_id"] = train[0]["synthetic_account_id"]
    write_rows(validation_path, validation)
    with pytest.raises(ValueError, match="cross-split account"):
        audit_dataset(root)


def test_auditor_detects_shared_domains(dataset, tmp_path):
    root = tmp_path / "corrupt"
    shutil.copytree(dataset, root)
    train = next(row for row in json_rows(root / "inputs/train.jsonl") if row["text"])
    path = root / "inputs/validation.jsonl"
    validation = json_rows(path)
    row = next(row for row in validation if row["text"])
    row["text"] += " " + re.search(r"https://\S+", train["text"]).group()
    write_rows(path, validation)
    with pytest.raises(ValueError, match="cross-split entity"):
        audit_dataset(root)


def test_auditor_detects_content_copied_with_a_different_url(dataset, tmp_path):
    root = tmp_path / "corrupt"
    shutil.copytree(dataset, root)
    train = next(
        row
        for row in json_rows(root / "inputs/train.jsonl")
        if row["text"] and "card-" not in row["text"] and "SYN-" not in row["text"]
    )
    path = root / "inputs/validation.jsonl"
    validation = json_rows(path)
    row = next(row for row in validation if row["text"])
    row["text"] = re.sub(r"https://\S+", "https://isolated.test/ref", train["text"])
    write_rows(path, validation)
    with pytest.raises(ValueError, match="cross-split content"):
        audit_dataset(root)


def test_auditor_rejects_missing_screenshot_reference(dataset, tmp_path):
    root = tmp_path / "corrupt"
    shutil.copytree(dataset, root)
    path = root / "ground_truth/train.jsonl"
    rows = json_rows(path)
    next(row for row in rows if row["ocr_reference"])["ocr_reference"] = None
    write_rows(path, rows)
    with pytest.raises(ValueError, match="missing screenshot or OCR reference"):
        audit_dataset(root)


def test_timestamps_do_not_separate_positive_and_negative_scenarios(dataset):
    for split in SPLITS:
        inputs = read_jsonl(dataset / "inputs" / f"{split}.jsonl", SyntheticInput)
        truth = {
            row.case_id: row
            for row in read_jsonl(
                dataset / "ground_truth" / f"{split}.jsonl",
                GroundTruth,
            )
        }
        positive, negative = [], []
        for row in inputs:
            bucket = positive if "suspicious" in truth[row.case_id].labels.values() else negative
            bucket.append(row.created_at)
        assert min(positive) < max(negative)
        assert min(negative) < max(positive)


def test_auditor_detects_reused_screenshot_bytes(dataset, tmp_path):
    root = tmp_path / "corrupt"
    shutil.copytree(dataset, root)
    train = next(row for row in json_rows(root / "inputs/train.jsonl") if row["image_path"])
    validation = next(
        row for row in json_rows(root / "inputs/validation.jsonl") if row["image_path"]
    )
    (root / validation["image_path"]).write_bytes((root / train["image_path"]).read_bytes())
    with pytest.raises(ValueError, match="cross-split image"):
        audit_dataset(root)


def test_canonical_duplicates_ignore_surrogate_ids_amounts_and_urls():
    first = f"Взнос 5000 тг, card-{uuid4()}, SYN-abcdef12 https://one.test/r"
    second = f"ВЗНОС 9000 тг, card-{uuid4()}, SYN-1234abcd https://two.test/offer"
    assert canonical_text(first) == canonical_text(second)


@pytest.mark.parametrize(
    "language,text",
    [
        ("ru", "Номер для розыгрыша стоит 1000 тг."),
        ("kk", "Тегін байқауға қатысу үшін төлем керек емес."),
    ],
)
def test_image_obfuscation_changes_text_without_emoji(language, text):
    output = surface_variant(text, language, "obfuscated", "screenshot")
    assert output != "SYNTHETIC TEST DATA\n" + text
    assert "🎲" not in output


def test_input_contract_rejects_external_urls_truth_and_path_traversal(dataset):
    row = json_rows(dataset / "inputs/train.jsonl")[0]
    for changes in (
        {"label": "benign"},
        {"synthetic": False},
        {"redirect_chain": ["https://example.com/offer"]},
        {
            "source_kind": "screenshot",
            "text": "",
            "artifact_id": str(uuid4()),
            "image_path": "../outside.png",
        },
    ):
        with pytest.raises(ValidationError):
            SyntheticInput.model_validate({**row, **changes})
