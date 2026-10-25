from itertools import product
from uuid import uuid4

import pytest
from pydantic import ValidationError

from safenet.contracts import (
    AnalysisOutcome,
    AnalysisResult,
    ErrorCode,
    Evidence,
    FeatureContribution,
    GraphInfluence,
    ImageRegion,
    JobStatus,
    ProcessingJob,
    RunVersion,
    SanitizedContent,
    TextSpan,
    Track,
    TrackResult,
)
from safenet.pipeline import analyze, validate_transition


def content(**changes):
    return SanitizedContent.model_validate(
        {
            "provenance": "synthetic",
            "synthetic": True,
            "text": "Тегін байқау",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"label": "suspicious"},
        {"track": "casino"},
        {"provenance": "public"},
        {"synthetic": False},
        {"source_kind": "screenshot"},
        {"created_at": "2026-10-24T12:00:00"},
    ],
)
def test_content_rejects_truth_fields_and_inconsistent_provenance(changes):
    with pytest.raises(ValidationError):
        content(**changes)


def test_sensitive_text_is_hidden_from_repr_and_validation_messages():
    sentinel = "SYNTHETIC-PRIVATE-SENTINEL"
    assert sentinel not in repr(content(text=sentinel))
    with pytest.raises(ValidationError) as error:
        content(label=sentinel)
    assert sentinel not in str(error.value)


def test_deidentified_and_screenshot_contracts():
    assert content(provenance="deidentified", synthetic=False).synthetic is False
    assert content(source_kind="screenshot", artifact_id=uuid4()).artifact_id is not None


@pytest.mark.parametrize("start,end", [(2, 2), (3, 2), (-1, 2), (True, 2)])
def test_invalid_spans_rejected(start, end):
    with pytest.raises(ValidationError):
        TextSpan(start=start, end=end)


def test_evidence_requires_one_location():
    data = {"source_id": uuid4(), "quote": "Тегін"}
    with pytest.raises(ValidationError):
        Evidence(**data)
    span = TextSpan(start=0, end=5)
    region = ImageRegion(x=0, y=0, width=30, height=20)
    with pytest.raises(ValidationError):
        Evidence(**data, span=span, region=region)
    assert Evidence(**data, span=span).span == span
    assert Evidence(**data, region=region).region == region


def feature(weight=120.0):
    return FeatureContribution(
        feature_id="lottery.payment",
        track=Track.LOTTERY,
        contribution=weight,
        evidence=(Evidence(source_id=uuid4(), quote="Тегін", span=TextSpan(start=0, end=5)),),
    )


def test_scores_reconcile_for_upper_and_lower_clipping():
    upper = TrackResult(
        track=Track.LOTTERY,
        features=(feature(),),
        graph_adjustment=10,
        graph_evidence=GraphInfluence(
            snapshot_id="0" * 64,
            node_id="domain:example",
            kind="domain",
            value="example.test",
            previous_cases=3,
            high_base_cases=2,
            seed_case_id=uuid4(),
            seed_base_score=85,
            seed_threshold=70,
            max_adjustment=15,
        ),
        cap_adjustment=-30,
        score=100,
        level="high",
    )
    assert upper.score == 100
    lower = TrackResult(
        track=Track.LOTTERY, features=(feature(-20.0),), cap_adjustment=20, score=0, level="low"
    )
    assert lower.score == 0
    with pytest.raises(ValidationError):
        TrackResult(
            track=Track.LOTTERY, features=(feature(),), cap_adjustment=0, score=10, level="low"
        )


def test_duplicate_features_and_cross_track_features_rejected():
    for track, features in [(Track.LOTTERY, (feature(), feature())), (Track.CASINO, (feature(),))]:
        with pytest.raises(ValidationError):
            TrackResult(track=track, features=features, cap_adjustment=0, score=0, level="low")


def test_analysis_result_requires_all_three_tracks(config):
    data = dict(
        case_id=uuid4(),
        version=1,
        run=RunVersion(code_version="test", config_fingerprint=config.fingerprint),
    )
    tracks = tuple(
        TrackResult(track=track, features=(), cap_adjustment=0, score=0, level="low")
        for track in Track
    )
    assert len(AnalysisResult(**data, tracks=tracks).tracks) == 3
    with pytest.raises(ValidationError):
        AnalysisResult(**data, tracks=tracks[:2])


def test_unprepared_content_never_reports_a_completed_or_zero_risk_analysis(config):
    outcome = analyze(content(), config)
    assert outcome.status == JobStatus.INCOMPLETE
    assert outcome.error_code == ErrorCode.INVALID_INPUT
    assert outcome.result is None
    with pytest.raises(ValidationError):
        AnalysisOutcome(status="completed")
    with pytest.raises(ValidationError):
        AnalysisOutcome(status="incomplete")


def test_job_status_and_error_code_consistent():
    ids = dict(request_id=uuid4(), case_id=uuid4())
    assert ProcessingJob(**ids).status == JobStatus.QUEUED
    with pytest.raises(ValidationError):
        ProcessingJob(**ids, status="failed")
    with pytest.raises(ValidationError):
        ProcessingJob(**ids, status="completed", error_code="ocr_failed")


@pytest.mark.parametrize("current,target", list(product(JobStatus, repeat=2)))
def test_all_job_state_transitions(current, target):
    allowed = {
        ("queued", "processing"),
        ("queued", "failed"),
        ("processing", "completed"),
        ("processing", "incomplete"),
        ("processing", "failed"),
        ("incomplete", "queued"),
        ("failed", "queued"),
    }
    if (current.value, target.value) in allowed:
        validate_transition(current, target)
    else:
        with pytest.raises(ValueError, match="invalid job-state"):
            validate_transition(current, target)
