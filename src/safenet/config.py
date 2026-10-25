import hashlib
import json
import tomllib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictFloat, model_validator

from safenet.contracts import Contract, FeatureId, PositiveInt, Score, Track
from safenet.features.catalog import CATALOG


class Thresholds(Contract):
    medium: StrictFloat = Field(gt=0, lt=100)
    high: StrictFloat = Field(gt=0, le=100)

    @model_validator(mode="after")
    def check_order(self) -> Self:
        if self.medium >= self.high:
            raise ValueError("medium threshold must be less than high")
        return self


class FeatureWeight(Contract):
    feature_id: FeatureId
    track: Track
    weight: StrictFloat


class GraphSettings(Contract):
    max_adjustment: Score
    max_nodes: PositiveInt = 100_000
    max_edges: PositiveInt = 200_000
    display_max_nodes: PositiveInt = 100
    display_max_edges: PositiveInt = 200

    @model_validator(mode="after")
    def check_display_limits(self) -> Self:
        if self.display_max_nodes > self.max_nodes or self.display_max_edges > self.max_edges:
            raise ValueError("graph display limit exceeds processing capacity")
        return self


class Limits(Contract):
    max_upload_bytes: PositiveInt
    max_records: PositiveInt
    batch_size: PositiveInt
    max_pending_jobs: PositiveInt
    max_image_pixels: PositiveInt
    max_image_dimension: PositiveInt
    max_text_characters: PositiveInt
    max_ocr_words: PositiveInt

    @model_validator(mode="after")
    def check_batch(self) -> Self:
        if self.batch_size > min(self.max_records, self.max_pending_jobs):
            raise ValueError("batch size exceeds record or pending-work limit")
        return self


class OCRSettings(Contract):
    languages: Literal["rus+kaz+eng"]
    concurrency: PositiveInt
    timeout_seconds: PositiveInt


class ProcessingSettings(Contract):
    max_attempts: PositiveInt = Field(default=3, le=10)
    max_pending_bytes: PositiveInt = 32 * 1024 * 1024


class DetectorConfig(Contract):
    schema_version: Literal[1]
    thresholds: Thresholds
    graph: GraphSettings
    limits: Limits
    ocr: OCRSettings
    features: tuple[FeatureWeight, ...]
    processing: ProcessingSettings = ProcessingSettings()

    @model_validator(mode="after")
    def check_features(self) -> Self:
        ids = [feature.feature_id for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate feature IDs in configuration")
        if set(ids) != set(CATALOG):
            raise ValueError("configuration must contain exactly the known feature IDs")
        for feature in self.features:
            track, _, direction = CATALOG[feature.feature_id]
            if feature.track != track or feature.weight * direction <= 0:
                raise ValueError("feature track or contribution direction is invalid")
        return self

    @property
    def fingerprint(self) -> str:
        # Separate scoring/graph histories without invalidating prepared OCR artifacts.
        payload = json.dumps(
            {**self.model_dump(mode="json"), "rule_version": "3"},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_config(path: Path) -> DetectorConfig:
    with path.open("rb") as source:
        return DetectorConfig.model_validate(tomllib.load(source))
