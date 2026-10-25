"""Калибровка на validation → фиксация → test с абляциями. Без чтения OCR-эталонов."""

import argparse
import hashlib
import json
import platform
import re
import statistics
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

from safenet.config import DetectorConfig, load_config
from safenet.evaluation.metrics import calibrate, report
from safenet.graph import GraphIndex
from safenet.pipeline import analyze
from safenet.prepare import prepare_file
from safenet.preprocessing import load_prepared

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_digest():
    value = hashlib.sha256()
    for path in sorted((ROOT / "src/safenet").rglob("*.py")):
        value.update(path.relative_to(ROOT).as_posix().encode())
        value.update(path.read_bytes())
    return value.hexdigest()


def prepared_split(dataset, split, cache, config):
    target = cache / split
    started = time.perf_counter()
    input_hash = digest(dataset / "inputs" / f"{split}.jsonl")
    if not target.exists():
        prepare_file(
            dataset / "inputs" / f"{split}.jsonl",
            target,
            config,
            image_root=dataset,
            tessdata=ROOT / ".tools/tessdata",
        )
        (target / "input.sha256").write_text(input_hash, encoding="ascii")
    elif (target / "input.sha256").read_text(encoding="ascii") != input_hash:
        raise ValueError("cached input changed; choose a fresh cache")
    summary = json.loads((target / "summary.json").read_text(encoding="utf-8"))
    if summary["failed"] or summary["incomplete"] or summary["file_error"]:
        raise ValueError("preparation failed; cannot silently exclude cases")
    rows = [
        json.loads(line)
        for line in (target / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    prepared = [load_prepared(target / row["case_id"], config) for row in rows]
    return prepared, time.perf_counter() - started


def scored(prepared, config, split, *, graph=True, ocr=True):
    index = GraphIndex(config, split) if graph else None
    rows, latencies = [], []
    for item in sorted(prepared, key=lambda p: (p.content.created_at, str(p.content.case_id))):
        start = time.perf_counter()
        if not ocr and item.content.source_kind == "screenshot":
            scores = {track: 0.0 for track in ("lottery", "casino", "investment")}
        else:
            result = analyze(item, config, graph=index, scope=split if graph else None)
            if result.result is None:
                raise ValueError("scoring failure; evaluation aborted")
            scores = {track.track.value: track.score for track in result.result.tracks}
        latencies.append((time.perf_counter() - start) * 1000)
        rows.append(
            {
                "case_id": str(item.content.case_id),
                "split": split,
                "scores": scores,
                "source_kind": item.content.source_kind,
                "detected_language": item.content.language.value,
            }
        )
    return rows, latencies


def join_truth(rows, path):
    truth = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["case_id"] in truth:
            raise ValueError("duplicate ground truth")
        # OCR reference never enters the detection path.
        truth[row["case_id"]] = {
            key: row[key] for key in ("labels", "language", "scenario", "style")
        }
    if set(truth) != {row["case_id"] for row in rows}:
        raise ValueError("prediction/truth identity mismatch")
    return [{**row, **truth[row["case_id"]]} for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/synthetic-v1")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/evaluation")
    parser.add_argument("--cache", type=Path, default=ROOT / "data/evaluation-prepared-v1")
    parser.add_argument(
        "--evaluation-kind", choices=("initial", "regression"), default="regression"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config_path = ROOT / "config/detector.toml"
    base = load_config(config_path)
    validation, prep_validation = prepared_split(args.dataset, "validation", args.cache, base)
    rows, _ = scored(validation, base, "validation", graph=False)
    rows = join_truth(rows, args.dataset / "ground_truth/validation.jsonl")
    thresholds, candidates = calibrate(rows)
    config = DetectorConfig.model_validate({**base.model_dump(), "thresholds": thresholds})
    frozen = re.sub(
        r"(?m)^medium = .*$",
        f"medium = {thresholds['medium']}",
        config_path.read_text(encoding="utf-8"),
    )
    frozen = re.sub(r"(?m)^high = .*$", f"high = {thresholds['high']}", frozen)
    frozen = frozen.replace(
        "# Initial hand-assigned weights and thresholds, not validation-calibrated probabilities.",
        "# Weights are hand-assigned; thresholds selected on validation only. Not probabilities.",
    )
    (args.output / "detector.toml").write_text(frozen, encoding="utf-8")
    # Persist the freeze BEFORE opening any test input or label file.
    write(
        args.output / "calibration.json",
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "split": "validation",
            "evaluation_kind": args.evaluation_kind,
            "cases": len(rows),
            "policy": "max macro F1 for medium; high: maximum recall at queue precision >= 0.90; "
            "ties prefer higher threshold",
            "thresholds": thresholds,
            "config_fingerprint": config.fingerprint,
            "code_sha256": code_digest(),
            "validation_input_sha256": digest(args.dataset / "inputs/validation.jsonl"),
            "validation_truth_sha256": digest(args.dataset / "ground_truth/validation.jsonl"),
            "candidates": candidates,
        },
    )
    test, prep_test = prepared_split(args.dataset, "test", args.cache, config)
    metrics = {
        "created_at": datetime.now(UTC).isoformat(),
        "synthetic": True,
        "evaluation_kind": args.evaluation_kind,
        "rule_version": "3",
        "cases": len(test),
        "thresholds": thresholds,
        "config_fingerprint": config.fingerprint,
        "code_sha256": code_digest(),
        "test_input_sha256": digest(args.dataset / "inputs/test.jsonl"),
        "test_truth_sha256": digest(args.dataset / "ground_truth/test.jsonl"),
        "limitations": [
            "Синтетика не доказывает реальную точность.",
            *(
                ["Повторная регрессионная проверка ранее открытого test; не независимая оценка."]
                if args.evaluation_kind == "regression"
                else []
            ),
            "Пороги выбраны по базовым баллам validation; веса не обучались.",
            "Без OCR скриншоты имеют нулевые баллы и не добавляются в граф; знаменатель неизменен.",
            "Порядок графа: created_at, case_id; каждый split и вариант используют новый граф.",
            "Высокий уровень — приоритет проверки, не вероятность.",
        ],
    }
    for name, graph, ocr in (
        ("full", True, True),
        ("without_graph", False, True),
        ("without_ocr", True, False),
        ("without_graph_and_ocr", False, False),
    ):
        predictions, latencies = scored(test, config, "test", graph=graph, ocr=ocr)
        labelled = join_truth(predictions, args.dataset / "ground_truth/test.jsonl")
        metrics[name] = report(labelled, thresholds["medium"])
        if name == "full":
            metrics["high_priority"] = report(labelled, thresholds["high"])
            write(args.output / "predictions.json", predictions)
            ordered = sorted(latencies)
            tracemalloc.start()
            scored(test, config, "test")
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            write(
                args.output / "benchmark.json",
                {
                    "scope": "local sequential cached preprocessing → rules and graph; "
                    "not HTTP load capacity",
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "cases": len(test),
                    "scoring_ms_p50": statistics.median(ordered),
                    "scoring_ms_p95": ordered[int(0.95 * (len(ordered) - 1))],
                    "scoring_cases_per_second": 1000 * len(ordered) / sum(ordered),
                    "preparation_validation_seconds": prep_validation,
                    "preparation_test_seconds": prep_test,
                    "python_peak_scoring_bytes": peak,
                    "memory_scope": "tracemalloc, excludes native OCR and preloaded inputs",
                    "preparation_scope": "cache load and validation if artifacts already exist; "
                    "otherwise fresh OCR preparation",
                },
            )
    write(args.output / "metrics.json", metrics)
    write(ROOT / "metrics.json", metrics)
    table = [
        "# Метрики для слайдов",
        "",
        (
            "Повторная регрессионная проверка; не независимая оценка."
            if args.evaluation_kind == "regression"
            else "Первичная оценка нового test."
        ),
        "",
        f"Синтетический test: {len(test)} материалов. Положительный прогноз — балл ≥ "
        + str(thresholds["medium"])
        + ".",
        "",
        "| Трек | Precision | Recall | F1 | FPR | TP / FP / FN / TN |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name, values in metrics["full"]["overall"]["tracks"].items():
        values_text = [
            "—" if values[key] is None else f"{values[key]:.3f}"
            for key in ("precision", "recall", "f1", "fpr")
        ]
        table.append(
            "| "
            + " | ".join(
                [name, *values_text, " / ".join(str(values[k]) for k in ("tp", "fp", "fn", "tn"))]
            )
            + " |"
        )
    table.extend(
        [
            "",
            "Показатели синтетики не являются оценкой работы на реальных публикациях.",
        ]
    )
    (ROOT / "reports/METRICS.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"thresholds": thresholds, "test": metrics["full"]["overall"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
