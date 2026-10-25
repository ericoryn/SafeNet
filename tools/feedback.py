"""Экспортировать проверенную обратную связь для последующей разметки и калибровки."""

import argparse
import json
from pathlib import Path


def feedback_rows(records):
    if not records or records[0].get("record_type") != "begin":
        raise ValueError("missing export header")
    end = records[-1]
    if end.get("record_type") != "end" or end.get("complete") is not True:
        raise ValueError("incomplete export")
    cases, results, decisions, counts, versions = {}, {}, {}, {}, {}
    for row in records[1:-1]:
        kind, data = row["record_type"], row["data"]
        if kind not in ("case", "result", "decision", "job"):
            raise ValueError("unexpected export row")
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "case":
            if data["case_id"] in cases:
                raise ValueError("duplicate case")
            cases[data["case_id"]] = data
        elif kind == "result":
            if data["result_id"] in results:
                raise ValueError("duplicate result")
            results[data["result_id"]] = data
            versions[data["case_id"]] = max(versions.get(data["case_id"], 0), data["version"])
        elif kind == "decision":
            old = decisions.get(data["case_id"])
            if old is None or (data["created_at"], data["decision_id"]) > (
                old["created_at"],
                old["decision_id"],
            ):
                decisions[data["case_id"]] = data
    if any(counts.get(k, 0) != v for k, v in end["counts"].items()) or any(
        k not in end["counts"] for k in counts
    ):
        raise ValueError("export count mismatch")
    rows = []
    for identity, decision in sorted(decisions.items()):
        if decision["action"] == "request_review":
            continue
        case, result = cases.get(identity), results.get(decision["result_id"])
        if case is None or result is None or result["case_id"] != identity:
            raise ValueError("feedback references missing or foreign evidence")
        if result["version"] != versions[identity]:
            continue
        positive = decision["action"] == "confirm_signs"
        if not positive and decision["action"] != "false_positive":
            raise ValueError("unknown decision")
        rows.append(
            {
                "case_id": identity,
                "result_id": result["result_id"],
                "decision_id": decision["decision_id"],
                "text": case["sanitized_text"],
                "queue_label": "suspicious" if positive else "benign",
                "labels": None
                if positive
                else {t: "benign" for t in ("lottery", "casino", "investment")},
                "requires_track_annotation": positive,
                "config_fingerprint": result["run"]["config_fingerprint"],
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("export exceeds 100 MiB")
    rows = feedback_rows(
        [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()]
    )
    with args.output.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "exported": len(rows),
                "track_annotation_required": sum(r["requires_track_annotation"] for r in rows),
            }
        )
    )


if __name__ == "__main__":
    main()
