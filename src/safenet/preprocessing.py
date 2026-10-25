"""Safe preprocessing entry points. Raw evidence is never written to disk."""

import csv
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from safenet import __version__
from safenet.config import DetectorConfig
from safenet.contracts import (
    ErrorCode,
    IncomingRecord,
    OCRWord,
    PreparedContent,
    RunVersion,
    SanitizedContent,
)
from safenet.entities import extract_entities
from safenet.errors import ProcessingError
from safenet.normalization import detect_language, normalize
from safenet.ocr import MODEL_HASHES, OCRProcessor, runtime_version
from safenet.privacy import canonical_source, redact


def preprocessing_fingerprint(config: DetectorConfig) -> str:
    data = {"version": "1", "limits": config.limits.model_dump(), "ocr": config.ocr.model_dump()}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def reconstruction(
    text: str, words: tuple[OCRWord, ...], width: int, height: int, font_path: Path | None = None
) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    if font_path is None:
        font_path = next(
            (
                path
                for path in (
                    Path("C:/Windows/Fonts/arial.ttf"),
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                    Path("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"),
                )
                if path.is_file()
            ),
            None,
        )
    if font_path is None:
        raise ProcessingError(ErrorCode.PROCESSING_FAILED)
    # Rebuild from sanitized text: no original pixels or metadata can survive.
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for word in words:
        value = text[word.span.start : word.span.end]
        font_size = max(1, min(word.region.height, 64))
        font = ImageFont.truetype(str(font_path), font_size)
        while font_size > 1 and font.getlength(value) > word.region.width:
            font_size -= 1
            font = ImageFont.truetype(str(font_path), font_size)
        draw.text((word.region.x, word.region.y), value, font=font, fill="black", anchor="lt")
    output = io.BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def preprocess_record(
    record: IncomingRecord,
    config: DetectorConfig,
    *,
    image_bytes: bytes | None = None,
    ocr: OCRProcessor | None = None,
) -> PreparedContent:
    if (
        len(record.text) > config.limits.max_text_characters
        or len(record.text.encode("utf-8")) > config.limits.max_upload_bytes
    ):
        raise ProcessingError(ErrorCode.INVALID_INPUT)
    words, width, height, engine_version = (), None, None, None
    if record.source_kind == "screenshot":
        if image_bytes is None or ocr is None:
            raise ProcessingError(ErrorCode.OCR_FAILED)
        result = ocr.recognize(image_bytes, Path(record.image_path).suffix)
        try:
            raw = result["text"]
            if not isinstance(raw, str) or canonical_source(raw) != raw:
                raise ValueError("OCR response is not canonical")
            if not raw.strip() or len(raw) > config.limits.max_text_characters:
                raise ValueError("OCR text limit")
            words = tuple(OCRWord.model_validate(word) for word in result["words"])
            width, height = result["width"], result["height"]
            if (
                not words
                or len(words) > config.limits.max_ocr_words
                or not isinstance(width, int)
                or not isinstance(height, int)
                or min(width, height) <= 0
                or max(width, height) > config.limits.max_image_dimension
                or width * height > config.limits.max_image_pixels
            ):
                raise ValueError("OCR bounds")
            engine_version = result["engine_version"]
            for word in words:
                if (
                    word.span.end > len(raw)
                    or word.region.x + word.region.width > width
                    or word.region.y + word.region.height > height
                ):
                    raise ValueError("OCR word bounds")
        except (KeyError, ValueError, TypeError):
            raise ProcessingError(ErrorCode.OCR_FAILED) from None
    else:
        if image_bytes is not None:
            raise ProcessingError(ErrorCode.INVALID_INPUT)
        raw = canonical_source(record.text)
    if not raw.strip() or len(raw) > config.limits.max_text_characters:
        raise ProcessingError(ErrorCode.INVALID_INPUT)
    sanitized, redactions = redact(raw)
    domains = []
    for value in record.redirect_chain:
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("redirect fixture")
            domain = parsed.hostname.encode("idna").decode("ascii").lower()
            if parsed.username or parsed.password:
                raise ValueError("redirect credentials")
            domains.append(domain)
        except (ValueError, UnicodeError):
            raise ProcessingError(ErrorCode.INVALID_INPUT) from None
    image = reconstruction(sanitized, words, width, height) if words else None
    prepared = PreparedContent(
        preprocessing_fingerprint=preprocessing_fingerprint(config),
        content=SanitizedContent(
            case_id=record.case_id,
            provenance=record.provenance,
            synthetic=record.synthetic,
            text=sanitized,
            language=detect_language(sanitized),
            source_kind=record.source_kind,
            artifact_id=record.artifact_id,
            created_at=record.created_at,
        ),
        run=RunVersion(
            code_version=__version__,
            config_fingerprint=config.fingerprint,
            ocr_version=engine_version,
            model_versions=tuple(f"{name}:{digest}" for name, digest in MODEL_HASHES.items())
            if words
            else (),
        ),
        variants=normalize(sanitized),
        redactions=redactions,
        ocr_words=words,
        synthetic_account_id=record.synthetic_account_id,
        network=record.network,
        redirect_domains=tuple(domains),
        image_width=width,
        image_height=height,
        artifact_sha256=hashlib.sha256(image).hexdigest() if image else None,
        sanitized_image=image,
    )
    return prepared.model_copy(update={"entities": extract_entities(prepared)})


def iter_records(path: Path, config: DetectorConfig, *, data: bytes | None = None):
    """Stream bounded JSONL/CSV; never echo invalid rows into errors."""
    if path.suffix.lower() not in (".jsonl", ".csv"):
        raise ProcessingError(ErrorCode.UNSUPPORTED_INPUT)
    try:
        if (
            len(data) if data is not None else path.stat().st_size
        ) > config.limits.max_upload_bytes:
            raise ProcessingError(ErrorCode.INVALID_INPUT)
        source = (
            io.StringIO(data.decode("utf-8-sig"), newline="")
            if data is not None
            else path.open(encoding="utf-8-sig", newline="")
        )
        with source:

            def bounded_lines():
                total = 0
                while line := source.readline(config.limits.max_upload_bytes + 1):
                    total += len(line.encode("utf-8"))
                    if total > config.limits.max_upload_bytes:
                        raise ProcessingError(ErrorCode.INVALID_INPUT)
                    yield line

            if path.suffix.lower() == ".jsonl":
                rows = (json.loads(line) for line in bounded_lines() if line.strip())
            else:
                reader = csv.DictReader(bounded_lines())
                if (
                    not reader.fieldnames
                    or len(set(reader.fieldnames)) != len(reader.fieldnames)
                    or set(reader.fieldnames) - set(IncomingRecord.model_fields)
                ):
                    raise ProcessingError(ErrorCode.INVALID_INPUT)

                def csv_rows():
                    for row in reader:
                        data = {key: value for key, value in row.items() if value != ""}
                        if data.get("synthetic") not in ("true", "false"):
                            raise ProcessingError(ErrorCode.INVALID_INPUT)
                        data["synthetic"] = data["synthetic"] == "true"
                        if "redirect_chain" in data:
                            data["redirect_chain"] = json.loads(data["redirect_chain"])
                        yield data

                rows = csv_rows()
            found = False
            for index, row in enumerate(rows):
                if index >= config.limits.max_records:
                    raise ProcessingError(ErrorCode.INVALID_INPUT)
                found = True
                yield IncomingRecord.model_validate(row)
            if not found:
                raise ProcessingError(ErrorCode.INVALID_INPUT)
    except (OSError, ValueError, TypeError, csv.Error, UnicodeError):
        raise ProcessingError(ErrorCode.INVALID_INPUT) from None


def read_image(record: IncomingRecord, root: Path, config: DetectorConfig) -> bytes:
    try:
        path = (root / record.image_path).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ProcessingError(ErrorCode.INVALID_INPUT)
        with path.open("rb") as source:
            data = source.read(config.limits.max_upload_bytes + 1)
        if len(data) > config.limits.max_upload_bytes:
            raise ProcessingError(ErrorCode.INVALID_INPUT)
        return data
    except (OSError, TypeError):
        raise ProcessingError(ErrorCode.INVALID_INPUT) from None


def save_prepared(prepared: PreparedContent, root: Path) -> Path:
    destination = root / str(prepared.content.case_id)
    destination.mkdir(parents=True, exist_ok=False)
    if prepared.sanitized_image is not None:
        (destination / "evidence.png").write_bytes(prepared.sanitized_image)
    (destination / "prepared.json").write_text(prepared.model_dump_json(), encoding="utf-8")
    return destination


def load_prepared(directory: Path, config: DetectorConfig) -> PreparedContent:
    try:
        source = directory / "prepared.json"
        if source.stat().st_size > config.limits.max_text_characters * 250:
            raise ValueError("prepared record exceeds bounds")
        encoded = source.read_bytes()
        image_path = directory / "evidence.png"
        image = None
        if image_path.exists():
            with image_path.open("rb") as source:
                image = source.read(config.limits.max_image_pixels * 4 + 1)
        return restore_prepared(encoded, image, config)
    except (OSError, ValueError):
        raise ProcessingError(ErrorCode.PROCESSING_FAILED) from None


def restore_prepared(
    encoded: bytes | str, image: bytes | None, config: DetectorConfig
) -> PreparedContent:
    """Shared bounded validation for filesystem and SQLite sanitized caches."""
    try:
        if len(encoded) > config.limits.max_text_characters * 250:
            raise ValueError("prepared record exceeds bounds")
        prepared = PreparedContent.model_validate_json(encoded)
        if prepared.preprocessing_fingerprint != preprocessing_fingerprint(config):
            raise ValueError("stale preprocessing configuration")
        if prepared.run.code_version != __version__:
            raise ValueError("stale code version")
        if prepared.artifact_sha256:
            expected_models = tuple(f"{name}:{digest}" for name, digest in MODEL_HASHES.items())
            if prepared.run.model_versions != expected_models:
                raise ValueError("stale OCR models")
            if prepared.run.ocr_version != runtime_version():
                raise ValueError("stale OCR engine")
            if (
                image is None
                or len(image) > config.limits.max_image_pixels * 4
                or hashlib.sha256(image).hexdigest() != prepared.artifact_sha256
            ):
                raise ValueError("artifact checksum mismatch")
            prepared = prepared.model_copy(update={"sanitized_image": image})
        elif image is not None:
            raise ValueError("unexpected image in text cache")
        return prepared
    except (OSError, ValueError, TypeError, ImportError, ValidationError):
        raise ProcessingError(ErrorCode.PROCESSING_FAILED) from None
