"""Offline preprocessing CLI. Never reads evaluator labels or OCR answer files."""

import argparse
import json
from collections import Counter
from pathlib import Path

from safenet.config import DetectorConfig, load_config
from safenet.contracts import ErrorCode
from safenet.errors import ProcessingError
from safenet.ocr import OCRProcessor
from safenet.preprocessing import iter_records, preprocess_record, read_image, save_prepared


def prepare_file(
    source: Path, output: Path, config: DetectorConfig, *, image_root: Path, tessdata: Path
) -> dict:
    if output.exists():
        raise FileExistsError("output exists; choose a new directory")
    output.mkdir(parents=True)
    summary = {
        "prepared": 0,
        "incomplete": 0,
        "failed": 0,
        "file_error": None,
        "detection_ready": False,
        "config_fingerprint": config.fingerprint,
    }
    languages, entities, redactions, kinds = Counter(), Counter(), Counter(), Counter()
    ocr = None
    try:
        with (output / "outcomes.jsonl").open("x", encoding="utf-8") as outcomes:
            for record in iter_records(source, config):
                try:
                    image = None
                    if record.source_kind == "screenshot":
                        image = read_image(record, image_root, config)
                        if ocr is None:
                            ocr = OCRProcessor(config, tessdata)
                    prepared = preprocess_record(record, config, image_bytes=image, ocr=ocr)
                    save_prepared(prepared, output)
                    summary["prepared"] += 1
                    languages[prepared.content.language.value] += 1
                    kinds[prepared.content.source_kind] += 1
                    entities.update(entity.kind for entity in prepared.entities)
                    redactions.update(item.kind for item in prepared.redactions)
                    outcome = {"case_id": str(record.case_id), "status": "prepared"}
                except (ProcessingError, OSError) as error:
                    code = (
                        error.code
                        if isinstance(error, ProcessingError)
                        else ErrorCode.PROCESSING_FAILED
                    )
                    status = "incomplete" if code == ErrorCode.OCR_FAILED else "failed"
                    summary[status] += 1
                    outcome = {
                        "case_id": str(record.case_id),
                        "status": status,
                        "error_code": code.value,
                    }
                outcomes.write(json.dumps(outcome) + "\n")
                total = summary["prepared"] + summary["incomplete"] + summary["failed"]
                if total % config.limits.batch_size == 0:
                    print(
                        json.dumps({"processed": total, "prepared": summary["prepared"]}),
                        flush=True,
                    )
    except ProcessingError as error:
        summary["file_error"] = error.code.value
    summary.update(
        {
            "languages": dict(languages),
            "entities": dict(entities),
            "redactions": dict(redactions),
            "source_kinds": dict(kinds),
        }
    )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--tessdata", type=Path, default=Path(".tools/tessdata"))
    parser.add_argument("--config", type=Path, default=Path("config/detector.toml"))
    args = parser.parse_args()
    summary = prepare_file(
        args.input,
        args.output,
        load_config(args.config),
        image_root=args.image_root,
        tessdata=args.tessdata,
    )
    print(json.dumps(summary, indent=2))
    if summary["incomplete"] or summary["failed"] or summary["file_error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
