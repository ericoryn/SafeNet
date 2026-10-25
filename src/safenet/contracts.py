"""Internal sanitized contracts. Model validation is not a redaction mechanism."""

from datetime import UTC, datetime
from enum import StrEnum
from math import isclose
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictFloat, model_validator

from safenet import DISCLAIMER

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
Score = Annotated[StrictFloat, Field(ge=0, le=100)]
Fingerprint = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
FeatureId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,79}$")]
GraphScope = Literal["train", "validation", "test", "demo"]
GraphKeyKind = Literal[
    "account", "synthetic_card", "domain", "promo_code", "template", "group", "channel", "audience"
]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
        validate_default=True,
    )


class Track(StrEnum):
    LOTTERY = "lottery"
    CASINO = "casino"
    INVESTMENT = "investment"


class Language(StrEnum):
    RU = "ru"
    KK = "kk"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_INPUT = "unsupported_input"
    OCR_FAILED = "ocr_failed"
    PROCESSING_FAILED = "processing_failed"
    INTERRUPTED = "interrupted"
    NOT_IMPLEMENTED = "not_implemented"


class DecisionAction(StrEnum):
    CONFIRM_SIGNS = "confirm_signs"
    FALSE_POSITIVE = "false_positive"
    REQUEST_REVIEW = "request_review"


class ProcessingJob(Contract):
    job_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    case_id: UUID
    status: JobStatus = JobStatus.QUEUED
    attempts: NonnegativeInt = 0
    error_code: ErrorCode | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def check_error(self) -> Self:
        requires_error = self.status in (JobStatus.INCOMPLETE, JobStatus.FAILED)
        if requires_error != (self.error_code is not None):
            raise ValueError("job error code does not match processing status")
        if self.updated_at < self.created_at:
            raise ValueError("job update precedes creation")
        return self


class SanitizedContent(Contract):
    # Only the preprocessing boundary may create this for persistence.
    case_id: UUID = Field(default_factory=uuid4)
    provenance: Literal["synthetic", "deidentified"]
    synthetic: bool = Field(strict=True)
    text: str = Field(repr=False)
    language: Language = Language.UNKNOWN
    source_kind: Literal["text", "screenshot", "transcript"] = "text"
    artifact_id: UUID | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def check_provenance(self) -> Self:
        if self.synthetic != (self.provenance == "synthetic"):
            raise ValueError("synthetic flag must match provenance")
        if self.source_kind == "screenshot" and self.artifact_id is None:
            raise ValueError("screenshot requires a sanitized artifact reference")
        return self


class TextSpan(Contract):
    start: NonnegativeInt
    end: PositiveInt

    @model_validator(mode="after")
    def check_order(self) -> Self:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        return self


class ImageRegion(Contract):
    x: NonnegativeInt
    y: NonnegativeInt
    width: PositiveInt
    height: PositiveInt


class Evidence(Contract):
    source_id: UUID
    quote: str = Field(min_length=1, repr=False)
    span: TextSpan | None = None
    region: ImageRegion | None = None

    @model_validator(mode="after")
    def check_location(self) -> Self:
        if (self.span is None) == (self.region is None):
            raise ValueError("evidence requires exactly one text span or image region")
        return self


class ExtractedEntity(Contract):
    kind: Literal[
        "domain", "account", "synthetic_card", "payment_reference", "promo_code", "amount"
    ]
    value: str = Field(min_length=1, repr=False)
    evidence: Evidence


class FeatureContribution(Contract):
    feature_id: FeatureId
    track: Track
    contribution: StrictFloat
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    description: str = ""


class GraphSnapshot(Contract):
    snapshot_id: Fingerprint
    scope: GraphScope
    sequence: NonnegativeInt
    node_count: NonnegativeInt
    edge_count: NonnegativeInt
    component_count: NonnegativeInt
    largest_component_cases: NonnegativeInt

    @model_validator(mode="after")
    def check_statistics(self) -> Self:
        if (
            self.sequence > self.node_count
            or self.component_count > self.node_count
            or self.largest_component_cases > self.sequence
        ):
            raise ValueError("inconsistent graph snapshot statistics")
        return self


class GraphNode(Contract):
    node_id: str = Field(min_length=1, max_length=90)
    kind: Literal[
        "case",
        "account",
        "synthetic_card",
        "domain",
        "promo_code",
        "template",
        "group",
        "channel",
        "audience",
    ]
    value: str = Field(min_length=1, repr=False)
    first_seen: PositiveInt


class GraphEdge(Contract):
    source: str
    target: str
    origin: Literal[
        "text",
        "account_metadata",
        "redirect_metadata",
        "template",
        "group_metadata",
        "channel_metadata",
        "audience_metadata",
        "organizer_metadata",
        "curator_metadata",
    ]
    observed_sequence: PositiveInt


class GraphInfluence(Contract):
    snapshot_id: Fingerprint
    node_id: str
    kind: GraphKeyKind
    value: str = Field(min_length=1, repr=False)
    previous_cases: PositiveInt
    high_base_cases: PositiveInt
    seed_case_id: UUID
    seed_base_score: Score
    seed_threshold: StrictFloat = Field(gt=0, le=100)
    max_adjustment: Score
    description: str = "Связь с ранее обработанными материалами с высокой базовой оценкой"

    @model_validator(mode="after")
    def check_counts(self) -> Self:
        if self.high_base_cases > self.previous_cases:
            raise ValueError("graph seed count exceeds relationship degree")
        if self.seed_base_score < self.seed_threshold:
            raise ValueError("graph seed lacks a high base score")
        return self


class GraphNeighborhood(Contract):
    snapshot: GraphSnapshot
    focus_case_id: UUID
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    total_edges: NonnegativeInt
    truncated: bool

    @model_validator(mode="after")
    def check_view(self) -> Self:
        ids = {node.node_id for node in self.nodes}
        if len(ids) != len(self.nodes) or any(
            edge.source not in ids or edge.target not in ids for edge in self.edges
        ):
            raise ValueError("graph neighborhood contains duplicate or missing nodes")
        if self.truncated != (len(self.edges) < self.total_edges):
            raise ValueError("graph neighborhood truncation flag is inconsistent")
        return self


class TrackResult(Contract):
    track: Track
    features: tuple[FeatureContribution, ...]
    graph_adjustment: Score = 0
    graph_evidence: GraphInfluence | None = None
    cap_adjustment: StrictFloat
    score: Score
    level: RiskLevel

    @model_validator(mode="after")
    def check_contributions(self) -> Self:
        ids = [feature.feature_id for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate feature IDs in track result")
        if any(feature.track != self.track for feature in self.features):
            raise ValueError("feature track does not match result track")
        if (self.graph_adjustment > 0) != (self.graph_evidence is not None):
            raise ValueError("graph adjustment requires its explanation")
        if self.graph_evidence is not None:
            info = self.graph_evidence
            expected = info.max_adjustment * info.high_base_cases / info.previous_cases
            if not isclose(self.graph_adjustment, expected, abs_tol=1e-6):
                raise ValueError("graph adjustment does not reconcile with its evidence")
        total = sum(feature.contribution for feature in self.features) + self.graph_adjustment
        # Both upper and lower clipping must reconcile with the displayed score.
        expected_score = max(0, min(100, total))
        if not isclose(self.score, expected_score, abs_tol=1e-6):
            raise ValueError("score does not match clipped contributions")
        if not isclose(self.cap_adjustment, self.score - total, abs_tol=1e-6):
            raise ValueError("cap adjustment does not reconcile with contributions")
        return self


class RunVersion(Contract):
    code_version: str = Field(min_length=1)
    config_fingerprint: Fingerprint
    ocr_version: str | None = None
    model_versions: tuple[str, ...] = ()


class AnalysisResult(Contract):
    result_id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    version: PositiveInt
    run: RunVersion
    tracks: tuple[TrackResult, ...]
    rule_version: Literal["1", "2", "3"] = "3"
    preprocessing_fingerprint: Fingerprint | None = None
    graph_snapshot: GraphSnapshot | None = None
    disclaimer: str = DISCLAIMER
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def check_tracks(self) -> Self:
        if len(self.tracks) != len(Track) or {item.track for item in self.tracks} != set(Track):
            raise ValueError("result requires exactly one score for each track")
        for track in self.tracks:
            info = track.graph_evidence
            if info is not None and (
                self.graph_snapshot is None
                or info.snapshot_id != self.graph_snapshot.snapshot_id
                or info.previous_cases > self.graph_snapshot.sequence
                or info.seed_case_id == self.case_id
            ):
                raise ValueError("graph explanation must reference prior snapshot and another case")
        return self

    @property
    def queue_score(self) -> float:
        return max(item.score for item in self.tracks)

    @property
    def queue_level(self) -> RiskLevel:
        return max(self.tracks, key=lambda item: item.score).level


class AnalysisOutcome(Contract):
    status: Literal[JobStatus.COMPLETED, JobStatus.INCOMPLETE, JobStatus.FAILED]
    result: AnalysisResult | None = None
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def check_completion(self) -> Self:
        if self.status == JobStatus.COMPLETED:
            if self.result is None or self.error_code is not None:
                raise ValueError("completed analysis requires a result and no error")
        elif self.result is not None or self.error_code is None:
            raise ValueError("unfinished analysis requires an error code and no final scores")
        return self


class AnalystDecision(Contract):
    decision_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    case_id: UUID
    result_id: UUID
    action: DecisionAction
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class HealthResponse(Contract):
    status: Literal["ok", "unavailable"]
    phase: Literal[6] = 6
    detection_ready: bool = False
    schema_version: int
    config_fingerprint: Fingerprint
    code_version: str
    disclaimer: str = DISCLAIMER


class SyntheticNetwork(Contract):
    group_id: UUID | None = None
    channel_ids: tuple[UUID, ...] = Field(default=(), max_length=8)
    audience_ids: tuple[UUID, ...] = Field(default=(), max_length=32)
    role: Literal["member", "organizer", "curator"] = "member"

    @model_validator(mode="after")
    def check_role(self) -> Self:
        if self.role != "member" and self.group_id is None:
            raise ValueError("group role requires a group")
        return self


class IncomingRecord(Contract):
    case_id: UUID = Field(default_factory=uuid4)
    provenance: Literal["synthetic", "deidentified"]
    synthetic: bool = Field(strict=True)
    text: str = Field(default="", repr=False)
    source_kind: Literal["text", "screenshot", "transcript"] = "text"
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    synthetic_account_id: UUID | None = None
    network: SyntheticNetwork | None = None
    artifact_id: UUID | None = None
    image_path: str | None = Field(default=None, repr=False)
    redirect_chain: tuple[str, ...] = Field(default=(), max_length=16, repr=False)

    @model_validator(mode="after")
    def check_input(self) -> Self:
        if self.synthetic != (self.provenance == "synthetic"):
            raise ValueError("synthetic flag must match provenance")
        if self.synthetic_account_id is not None and not self.synthetic:
            raise ValueError("synthetic account metadata requires synthetic provenance")
        if self.network is not None and not self.synthetic:
            raise ValueError("network identities require synthetic provenance")
        if self.source_kind == "screenshot":
            if self.text or self.image_path is None or self.artifact_id is None:
                raise ValueError("screenshot requires an asset reference and no reference answer")
            path = PurePosixPath(self.image_path)
            if (
                path.is_absolute()
                or PureWindowsPath(self.image_path).drive
                or ".." in path.parts
                or "\\" in self.image_path
                or ":" in self.image_path
            ):
                raise ValueError("image reference must stay inside the supplied asset root")
        elif not self.text.strip() or self.image_path is not None or self.artifact_id is not None:
            raise ValueError("text/transcript requires text and no image reference")
        return self


class Redaction(Contract):
    kind: Literal["phone", "card", "email", "handle", "name", "url_private"]
    span: TextSpan


class OCRWord(Contract):
    span: TextSpan
    region: ImageRegion
    confidence: Score


class NormalizedText(Contract):
    name: Literal["normalized", "translit_ru", "translit_kk"]
    text: str = Field(repr=False)
    source_spans: tuple[TextSpan, ...] = Field(repr=False)

    @model_validator(mode="after")
    def check_mapping(self) -> Self:
        if len(self.text) != len(self.source_spans):
            raise ValueError("every normalized character requires a source mapping")
        return self

    def source_span(self, start: int, end: int) -> TextSpan:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("normalized evidence span is out of bounds")
        return TextSpan(start=self.source_spans[start].start, end=self.source_spans[end - 1].end)


class PreparedContent(Contract):
    preprocessing_version: Literal["1"] = "1"
    preprocessing_fingerprint: Fingerprint
    content: SanitizedContent
    run: RunVersion
    variants: tuple[NormalizedText, ...] = Field(min_length=1)
    entities: tuple[ExtractedEntity, ...] = ()
    redactions: tuple[Redaction, ...] = ()
    ocr_words: tuple[OCRWord, ...] = ()
    synthetic_account_id: UUID | None = None
    network: SyntheticNetwork | None = None
    redirect_domains: tuple[str, ...] = ()
    image_width: PositiveInt | None = None
    image_height: PositiveInt | None = None
    artifact_sha256: Fingerprint | None = None
    sanitized_image: bytes | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def check_prepared(self) -> Self:
        size = len(self.content.text)
        for variant in self.variants:
            if any(span.end > size for span in variant.source_spans):
                raise ValueError("normalization mapping exceeds sanitized source")
        if any(item.span.end > size for item in self.redactions):
            raise ValueError("redaction span exceeds sanitized source")
        if self.content.source_kind == "screenshot":
            if not self.ocr_words or not self.image_width or not self.image_height:
                raise ValueError("screenshot requires OCR evidence and sanitized dimensions")
            if self.artifact_sha256 is None:
                raise ValueError("screenshot requires sanitized artifact checksum")
            for word in self.ocr_words:
                if (
                    word.span.end > size
                    or word.region.x + word.region.width > self.image_width
                    or word.region.y + word.region.height > self.image_height
                ):
                    raise ValueError("OCR evidence exceeds source bounds")
        elif self.ocr_words or self.artifact_sha256 or self.sanitized_image is not None:
            raise ValueError("text preparation cannot contain screenshot artifacts")
        return self

    def evidence(self, variant: int, start: int, end: int) -> Evidence:
        span = self.variants[variant].source_span(start, end)
        return self.source_evidence(span)

    def source_evidence(self, span: TextSpan) -> Evidence:
        if span.end > len(self.content.text):
            raise ValueError("evidence exceeds sanitized source")
        quote = self.content.text[span.start : span.end]
        if self.content.source_kind != "screenshot":
            return Evidence(source_id=self.content.case_id, quote=quote, span=span)
        words = [
            word
            for word in self.ocr_words
            if word.span.start < span.end and word.span.end > span.start
        ]
        if not words:
            raise ValueError("normalized match has no OCR region")
        left = min(word.region.x for word in words)
        top = min(word.region.y for word in words)
        right = max(word.region.x + word.region.width for word in words)
        bottom = max(word.region.y + word.region.height for word in words)
        return Evidence(
            source_id=self.content.artifact_id,
            quote=quote,
            region=ImageRegion(x=left, y=top, width=right - left, height=bottom - top),
        )
