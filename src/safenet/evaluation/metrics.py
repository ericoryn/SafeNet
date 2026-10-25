"""Pure metrics and validation-only threshold selection; no detector imports or I/O."""

from statistics import mean


def binary_metrics(truth, predicted):
    if len(truth) != len(predicted) or not truth:
        raise ValueError("nonempty aligned labels required")
    tp = sum(a and b for a, b in zip(truth, predicted, strict=True))
    fp = sum(not a and b for a, b in zip(truth, predicted, strict=True))
    fn = sum(a and not b for a, b in zip(truth, predicted, strict=True))
    tn = len(truth) - tp - fp - fn
    return {
        "n": len(truth),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        "fpr": fp / (fp + tn) if fp + tn else None,
        "confusion_matrix": [[tn, fp], [fn, tp]],
    }


def report(rows, threshold):
    if not rows or len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("unique nonempty evaluation rows required")
    tracks = ("lottery", "casino", "investment")

    def group(items):
        by_track = {
            track: binary_metrics(
                [r["labels"][track] == "suspicious" for r in items],
                [r["scores"][track] >= threshold for r in items],
            )
            for track in tracks
        }
        return {
            "cases": len(items),
            "tracks": by_track,
            "macro_f1": mean(m["f1"] or 0 for m in by_track.values()),
            "queue": binary_metrics(
                [any(v == "suspicious" for v in r["labels"].values()) for r in items],
                [max(r["scores"].values()) >= threshold for r in items],
            ),
        }

    ranked = sorted(rows, key=lambda r: (-max(r["scores"].values()), r["case_id"]))
    return {
        "threshold": threshold,
        "overall": group(rows),
        "by_language": {
            lang: group([r for r in rows if r["language"] == lang])
            for lang in sorted({r["language"] for r in rows})
        },
        "by_source_kind": {
            kind: group([r for r in rows if r["source_kind"] == kind])
            for kind in sorted({r["source_kind"] for r in rows})
        },
        "precision_at_k": {
            str(k): {
                "k": min(k, len(rows)),
                "precision": sum(
                    any(v == "suspicious" for v in r["labels"].values()) for r in ranked[:k]
                )
                / min(k, len(rows)),
            }
            for k in (10, 20, 50)
        },
    }


def calibrate(rows):
    if {r["split"] for r in rows} != {"validation"}:
        raise ValueError("only validation may select thresholds")
    candidates = []
    for value in range(5, 100, 5):
        stats = report(rows, value)["overall"]
        candidates.append((value, stats))
    # Prefer fewer false alerts, then the higher threshold when F1 ties.
    medium, _ = max(candidates, key=lambda x: (x[1]["macro_f1"], -x[1]["queue"]["fp"], x[0]))
    eligible = [
        (v, s)
        for v, s in candidates
        if v > medium and s["queue"]["precision"] is not None and s["queue"]["precision"] >= 0.9
    ]
    high = max(eligible, key=lambda x: (x[1]["queue"]["recall"] or 0, x[0]))[0] if eligible else 100
    if medium >= high:
        raise ValueError("threshold search has no ordered result")
    return {"medium": float(medium), "high": float(high)}, [
        {"threshold": v, "macro_f1": s["macro_f1"], "queue": s["queue"]} for v, s in candidates
    ]
