import math

from safenet.config import DetectorConfig
from safenet.contracts import FeatureContribution, RiskLevel, Track, TrackResult
from safenet.features.catalog import CATALOG
from safenet.features.common import FeatureMatch


def risk_level(score: float, config: DetectorConfig) -> RiskLevel:
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("score must be finite and between zero and 100")
    if score >= config.thresholds.high:
        return RiskLevel.HIGH
    if score >= config.thresholds.medium:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def score_track(
    track: Track, matches: tuple[FeatureMatch, ...], config: DetectorConfig
) -> TrackResult:
    weights = {feature.feature_id: feature.weight for feature in config.features}
    ids = [match.feature_id for match in matches]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate feature contributions")
    contributions = []
    for match in matches:
        expected_track, description, _ = CATALOG[match.feature_id]
        if expected_track != track or not match.evidence:
            raise ValueError("invalid feature track or missing evidence")
        contributions.append(
            FeatureContribution(
                feature_id=match.feature_id,
                track=track,
                contribution=weights[match.feature_id],
                evidence=match.evidence,
                description=description,
            )
        )
    total = math.fsum(item.contribution for item in contributions)
    if not math.isfinite(total):
        raise ValueError("non-finite contribution sum")
    score = max(0.0, min(100.0, total))
    return TrackResult(
        track=track,
        features=tuple(contributions),
        graph_adjustment=0,
        cap_adjustment=score - total,
        score=score,
        level=risk_level(score, config),
    )
