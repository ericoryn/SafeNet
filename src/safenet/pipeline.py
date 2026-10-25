from safenet import __version__
from safenet.config import DetectorConfig
from safenet.contracts import (
    AnalysisOutcome,
    AnalysisResult,
    ErrorCode,
    GraphScope,
    JobStatus,
    PreparedContent,
    RunVersion,
    Track,
)
from safenet.features import casino, investment, lottery
from safenet.graph import GraphIndex
from safenet.preprocessing import preprocessing_fingerprint
from safenet.scoring import score_track

ALLOWED_TRANSITIONS = {
    JobStatus.QUEUED: frozenset({JobStatus.PROCESSING, JobStatus.FAILED}),
    JobStatus.PROCESSING: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.INCOMPLETE,
            JobStatus.FAILED,
        }
    ),
    JobStatus.INCOMPLETE: frozenset({JobStatus.QUEUED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.COMPLETED: frozenset(),
}


def validate_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError("invalid job-state transition")


def analyze(
    content: PreparedContent,
    config: DetectorConfig,
    *,
    version: int = 1,
    graph: GraphIndex | None = None,
    scope: GraphScope | None = None,
) -> AnalysisOutcome:
    """Shared deterministic scoring; fresh result metadata identifies each analysis run."""
    if not isinstance(content, PreparedContent):
        return AnalysisOutcome(status=JobStatus.INCOMPLETE, error_code=ErrorCode.INVALID_INPUT)
    if (
        content.preprocessing_fingerprint != preprocessing_fingerprint(config)
        or content.run.code_version != __version__
    ):
        return AnalysisOutcome(status=JobStatus.INCOMPLETE, error_code=ErrorCode.PROCESSING_FAILED)
    tracks = tuple(
        score_track(track, module.extract(content), config)
        for track, module in (
            (Track.LOTTERY, lottery),
            (Track.CASINO, casino),
            (Track.INVESTMENT, investment),
        )
    )
    result = AnalysisResult(
        case_id=content.content.case_id,
        version=version,
        tracks=tracks,
        preprocessing_fingerprint=content.preprocessing_fingerprint,
        run=RunVersion(
            code_version=__version__,
            config_fingerprint=config.fingerprint,
            ocr_version=content.run.ocr_version,
            model_versions=content.run.model_versions,
        ),
    )
    if graph is not None:
        try:
            result = graph.process(content, result, scope=scope)
        except ValueError:
            return AnalysisOutcome(
                status=JobStatus.INCOMPLETE, error_code=ErrorCode.PROCESSING_FAILED
            )
    elif scope is not None:
        return AnalysisOutcome(status=JobStatus.INCOMPLETE, error_code=ErrorCode.INVALID_INPUT)
    return AnalysisOutcome(status=JobStatus.COMPLETED, result=result)
