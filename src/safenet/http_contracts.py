"""Public upload models contain raw transient data; never persist their JSON."""

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from safenet.contracts import Contract, DecisionAction, IncomingRecord, SyntheticNetwork


@dataclass(frozen=True)
class Work:
    record: IncomingRecord
    image: bytes | None = field(default=None, repr=False)

    @cached_property
    def size(self) -> int:
        return len(self.record.model_dump_json().encode()) + len(self.image or b"")

    @cached_property
    def fingerprint(self) -> str:
        data = self.record.model_dump(mode="json", exclude={"case_id", "artifact_id", "created_at"})
        if data.get("network") is None:
            data.pop("network", None)  # Preserve fingerprints of pre-network pending jobs.
        data["image_sha256"] = hashlib.sha256(self.image).hexdigest() if self.image else None
        return fingerprint(data)


def fingerprint(data) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class UploadRecord(Contract):
    provenance: Literal["synthetic", "deidentified"]
    synthetic: bool = Field(strict=True)
    text: str = Field(default="", repr=False)
    source_kind: Literal["text", "transcript", "screenshot"] = "text"
    created_at: AwareDatetime | None = None
    synthetic_account_id: UUID | None = None
    network: SyntheticNetwork | None = None
    redirect_chain: tuple[str, ...] = Field(default=(), max_length=16, repr=False)
    image_base64: str | None = Field(default=None, repr=False)
    image_format: Literal["png", "jpeg", "webp"] | None = None

    @model_validator(mode="after")
    def check_record(self) -> Self:
        if self.synthetic != (self.provenance == "synthetic"):
            raise ValueError("provenance mismatch")
        if self.synthetic_account_id is not None and not self.synthetic:
            raise ValueError("account metadata requires synthetic provenance")
        if self.network is not None and not self.synthetic:
            raise ValueError("network identities require synthetic provenance")
        if self.source_kind == "screenshot":
            if self.text or not self.image_base64 or not self.image_format:
                raise ValueError("screenshot requires image data and format")
        elif (
            not self.text.strip() or self.image_base64 is not None or self.image_format is not None
        ):
            raise ValueError("text requires text only")
        return self

    def work(self) -> Work:
        image = None
        if self.image_base64 is not None:
            image = base64.b64decode(self.image_base64, validate=True)
        screenshot = self.source_kind == "screenshot"
        record = IncomingRecord(
            provenance=self.provenance,
            synthetic=self.synthetic,
            text=self.text,
            source_kind=self.source_kind,
            created_at=self.created_at or datetime.now(UTC),
            synthetic_account_id=self.synthetic_account_id,
            network=self.network,
            redirect_chain=self.redirect_chain,
            artifact_id=uuid4() if screenshot else None,
            image_path=f"upload.{self.image_format}" if screenshot else None,
        )
        return Work(record, image)


class IngestionRequest(Contract):
    request_id: UUID
    records: tuple[UploadRecord, ...] = Field(min_length=1, max_length=10000)


class CommandRequest(Contract):
    request_id: UUID


class RetryRequest(CommandRequest):
    record: UploadRecord | None = None


class DecisionRequest(CommandRequest):
    result_id: UUID
    action: DecisionAction
