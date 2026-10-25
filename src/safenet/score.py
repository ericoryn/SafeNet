"""Offline scoring of internal prepared artifacts; never reads evaluator labels."""

import argparse
import json
from collections import Counter
from pathlib import Path
from uuid import UUID

from safenet import DISCLAIMER
from safenet.config import DetectorConfig, load_config
from safenet.contracts import AnalysisOutcome, ErrorCode, GraphScope, JobStatus, Track
from safenet.errors import ProcessingError
from safenet.graph import GraphIndex
from safenet.pipeline import analyze
from safenet.preprocessing import load_prepared
from safenet.scoring import risk_level


def score_prepared(
    source: Path, output: Path, config: DetectorConfig, *, graph_scope: GraphScope | None = None
) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        "completed": 0,
        "incomplete": 0,
        "failed": 0,
        "file_error": None,
        "rule_version": "3",
        "config_fingerprint": config.fingerprint,
        "graph_enabled": graph_scope is not None,
        "disclaimer": DISCLAIMER,
    }
    levels = {track: Counter() for track in Track}
    features, languages, kinds = Counter(), Counter(), Counter()
    queue, seen = [], set()
    overlap = 0
    graph = GraphIndex(config, graph_scope) if graph_scope is not None else None
    adjusted_cases, adjusted_tracks, level_changes = 0, 0, 0
    try:
        with (source / "summary.json").open("rb") as report:
            encoded = report.read(config.limits.max_upload_bytes + 1)
        if len(encoded) > config.limits.max_upload_bytes:
            raise ValueError("preparation summary exceeds bounds")
        upstream = json.loads(encoded)
        expected = sum(upstream[key] for key in ("prepared", "incomplete", "failed"))
        if not 0 < expected <= config.limits.max_records:
            raise ValueError("invalid preparation count")
        if upstream["file_error"] is not None:
            summary["file_error"] = ErrorCode(upstream["file_error"]).value
        with (source / "outcomes.jsonl").open("rb") as manifest:
            data = manifest.read(config.limits.max_upload_bytes + 1)
        if len(data) > config.limits.max_upload_bytes or not data.strip():
            raise ValueError("invalid preparation manifest")
        with (output / "outcomes.jsonl").open("x", encoding="utf-8") as outcomes:
            for index, line in enumerate(data.splitlines()):
                if index >= config.limits.max_records:
                    raise ValueError("record limit exceeded")
                row = json.loads(line)
                case_id = UUID(row["case_id"])
                if case_id in seen:
                    raise ValueError("duplicate case in manifest")
                seen.add(case_id)
                if row["status"] == "prepared":
                    try:
                        directory = (source / str(case_id)).resolve()
                        if not directory.is_relative_to(source.resolve()):
                            raise ProcessingError(ErrorCode.PROCESSING_FAILED)
                        prepared = load_prepared(directory, config)
                        if prepared.content.case_id != case_id:
                            raise ProcessingError(ErrorCode.PROCESSING_FAILED)
                        outcome = analyze(prepared, config, graph=graph, scope=graph_scope)
                    except ProcessingError as error:
                        outcome = AnalysisOutcome(status=JobStatus.FAILED, error_code=error.code)
                else:
                    outcome = AnalysisOutcome(status=row["status"], error_code=row["error_code"])
                summary[outcome.status.value] += 1
                if outcome.result is not None:
                    result = outcome.result
                    high_tracks = 0
                    adjusted_cases += any(track.graph_adjustment > 0 for track in result.tracks)
                    for track in result.tracks:
                        levels[track.track][track.level.value] += 1
                        features.update(feature.feature_id for feature in track.features)
                        high_tracks += track.level.value == "high"
                        adjusted_tracks += track.graph_adjustment > 0
                        if graph is not None:
                            level_changes += track.level != risk_level(
                                graph.base_scores[result.case_id][track.track], config
                            )
                    overlap += high_tracks > 1
                    languages[prepared.content.language.value] += 1
                    kinds[prepared.content.source_kind] += 1
                    queue.append(
                        {
                            "case_id": str(case_id),
                            "result_id": str(result.result_id),
                            "queue_score": result.queue_score,
                            "queue_level": result.queue_level.value,
                            "scores": {track.track.value: track.score for track in result.tracks},
                        }
                    )
                outcomes.write(
                    json.dumps(
                        {"case_id": str(case_id), **outcome.model_dump(mode="json")},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                if (index + 1) % config.limits.batch_size == 0:
                    print(
                        json.dumps({"processed": index + 1, "completed": summary["completed"]}),
                        flush=True,
                    )
        if len(seen) != expected:
            raise ValueError("incomplete preparation manifest")
    except OSError:
        summary["file_error"] = ErrorCode.PROCESSING_FAILED.value
    except (ValueError, TypeError, KeyError):
        summary["file_error"] = ErrorCode.INVALID_INPUT.value
    queue.sort(key=lambda row: (-row["queue_score"], row["case_id"]))
    if graph is not None:
        summary["graph_snapshot"] = graph.snapshot.model_dump(mode="json")
        (output / "graph.json").write_text(
            json.dumps(graph.export(), ensure_ascii=False) + "\n", encoding="utf-8"
        )
    with (output / "queue.jsonl").open("x", encoding="utf-8") as target:
        for row in queue:
            target.write(json.dumps(row) + "\n")
    summary.update(
        {
            "levels": {
                track.value: {level: levels[track][level] for level in ("low", "medium", "high")}
                for track in Track
            },
            "feature_counts": dict(sorted(features.items())),
            "high_track_overlap": overlap,
            "languages": dict(languages),
            "source_kinds": dict(kinds),
            "graph_adjusted_cases": adjusted_cases,
            "graph_adjusted_tracks": adjusted_tracks,
            "graph_level_changes": level_changes,
        }
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/detector.toml"))
    parser.add_argument(
        "--graph-scope",
        choices=("train", "validation", "test", "demo"),
        help="enable a fresh graph isolated to this input partition",
    )
    args = parser.parse_args()
    summary = score_prepared(
        args.input, args.output, load_config(args.config), graph_scope=args.graph_scope
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary["incomplete"] or summary["failed"] or summary["file_error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
