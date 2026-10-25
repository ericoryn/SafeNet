import io
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from safenet.config import DetectorConfig
from safenet.contracts import IncomingRecord
from safenet.errors import ProcessingError
from safenet.ocr import OCRProcessor
from safenet.pipeline import analyze
from safenet.preprocessing import load_prepared, preprocess_record, save_prepared

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def processor(config):
    return OCRProcessor(config, ROOT / ".tools/tessdata")


def screenshot(lines=None):
    image = Image.new("RGB", (1500, 420), "white")
    draw = ImageDraw.Draw(image)
    font_path = next(
        path
        for path in (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        )
        if path.is_file()
    )
    font = ImageFont.truetype(str(font_path), 36)
    for index, text in enumerate(
        lines
        or (
            "SYNTHETIC TEST DATA",
            "ФИО: ТЕСТОВ ТЕСТ ТЕСТОВИЧ",
            "+7 000 111 22 33",
            "0000 1111 2222 3333",
            "Қатысу тегін. 5000 теңге.",
            "Код SYN-abcdef12",
        )
    ):
        draw.text((40, 20 + 60 * index), text, font=font, fill="black")
    # Non-text pixels deliberately absent from OCR must not be preserved either.
    draw.rectangle((1400, 350, 1499, 419), fill="red")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Author", "SYNTHETIC-PRIVATE-METADATA")
    data = io.BytesIO()
    image.save(data, format="PNG", pnginfo=metadata)
    return data.getvalue()


@pytest.mark.ocr
@pytest.mark.parametrize(
    "track,lines",
    [
        ("casino", ("Казино. Переходи по ссылке.", "Пополни депозит. Мой выигрыш.")),
        ("lottery", ("Жарна 5000 теңге.", "Жеңімпаз кездейсоқ таңдалады.")),
    ],
)
def test_real_ocr_to_scoring_retains_image_evidence(config, processor, track, lines):
    prepared = preprocess_record(record(), config, image_bytes=screenshot(lines), ocr=processor)
    outcome = analyze(prepared, config)
    assert outcome.status == "completed"
    result = next(item for item in outcome.result.tracks if item.track == track)
    assert result.level == "high"
    for feature in result.features:
        for evidence in feature.evidence:
            assert evidence.span is None and evidence.region is not None
            assert evidence.source_id == prepared.content.artifact_id
            assert evidence.quote in prepared.content.text
            assert evidence.region.x + evidence.region.width <= prepared.image_width
            assert evidence.region.y + evidence.region.height <= prepared.image_height


def record():
    return IncomingRecord(
        provenance="synthetic",
        synthetic=True,
        source_kind="screenshot",
        artifact_id=uuid4(),
        image_path="private.png",
    )


@pytest.mark.ocr
def test_real_ocr_masks_text_discards_original_pixels_and_strips_metadata(
    config, processor, tmp_path
):
    original = screenshot()
    prepared = preprocess_record(record(), config, image_bytes=original, ocr=processor)
    assert prepared.ocr_words
    assert any(item.kind == "card" for item in prepared.redactions)
    assert any(item.kind == "phone" for item in prepared.redactions)
    assert "ТЕСТОВ" not in prepared.model_dump_json()
    assert "+7 000 111 22 33" not in prepared.model_dump_json()
    assert "0000 1111 2222 3333" not in prepared.model_dump_json()
    with Image.open(io.BytesIO(prepared.sanitized_image)) as image:
        pixel = image.getpixel((1450, 380))
        assert pixel[0] == pixel[1] == pixel[2]
        assert "Author" not in image.info
    directory = save_prepared(prepared, tmp_path)
    for path in directory.iterdir():
        assert b"SYNTHETIC-PRIVATE-METADATA" not in path.read_bytes()
    reloaded = load_prepared(directory, config)
    assert reloaded.sanitized_image == prepared.sanitized_image
    assert reloaded.content.text == prepared.content.text
    match = next(entity for entity in prepared.entities if entity.kind == "amount")
    assert match.evidence.region is not None
    assert match.evidence.source_id == prepared.content.artifact_id
    assert match.evidence.quote in prepared.content.text
    # Reuse is an explicit load of an already-sanitized artifact, not raw-input memoization.
    (directory / "evidence.png").write_bytes(b"corrupt")
    with pytest.raises(ProcessingError):
        load_prepared(directory, config)


@pytest.mark.parametrize(
    "data,suffix,expected",
    [
        (b"not an image PRIVATE-SENTINEL", ".png", "invalid_input"),
        (b"not an image", ".gif", "unsupported_input"),
        (b"", ".png", "invalid_input"),
    ],
)
def test_ocr_failure_has_no_raw_error_or_zero_score(processor, data, suffix, expected):
    with pytest.raises(ProcessingError) as error:
        processor.recognize(data, suffix)
    assert str(error.value) == expected


def test_pixel_and_dimension_limits_checked_before_decoding(config):
    data = config.model_dump()
    data["limits"]["max_image_pixels"] = 10
    processor = OCRProcessor(DetectorConfig.model_validate(data), ROOT / ".tools/tessdata")
    with pytest.raises(ProcessingError, match="invalid_input"):
        processor.recognize(screenshot(), ".png")


def test_native_timeout_kills_worker_and_releases_capacity(processor, monkeypatch):
    class Worker:
        killed = False

        def communicate(self, *args, **kwargs):
            if not self.killed:
                raise subprocess.TimeoutExpired("native-ocr", 20)
            return b"", b""

        def kill(self):
            self.killed = True

        def poll(self):
            return 0 if self.killed else None

    worker = Worker()
    monkeypatch.setattr("safenet.ocr.subprocess.Popen", lambda *args, **kwargs: worker)
    with pytest.raises(ProcessingError, match="ocr_failed"):
        processor.recognize(b"synthetic", ".png")
    assert worker.killed
    assert processor._slots.acquire(blocking=False)
    processor._slots.release()


def test_cancellation_stops_native_worker(processor, monkeypatch):
    class Worker:
        killed = False

        def communicate(self, *args, **kwargs):
            if not self.killed:
                raise KeyboardInterrupt()
            return b"", b""

        def poll(self):
            return 0 if self.killed else None

        def kill(self):
            self.killed = True

    worker = Worker()
    monkeypatch.setattr("safenet.ocr.subprocess.Popen", lambda *args, **kwargs: worker)
    with pytest.raises(KeyboardInterrupt):
        processor.recognize(b"synthetic", ".png")
    assert worker.killed
    assert processor._slots.acquire(blocking=False)
    processor._slots.release()


def test_missing_or_modified_models_fail_closed(config, tmp_path):
    with pytest.raises(ProcessingError, match="ocr_failed"):
        OCRProcessor(config, tmp_path)


def test_ocr_capacity_wait_has_a_deadline(config, monkeypatch):
    data = config.model_dump()
    data["ocr"]["timeout_seconds"] = 1
    processor = OCRProcessor(DetectorConfig.model_validate(data), ROOT / ".tools/tessdata")
    processor._slots.acquire()
    try:
        with pytest.raises(ProcessingError, match="ocr_failed"):
            processor.recognize(b"synthetic", ".png")
    finally:
        processor._slots.release()


def test_decoder_rejects_extension_mismatch(processor):
    data = io.BytesIO()
    Image.new("RGB", (300, 150), "white").save(data, format="JPEG")
    with pytest.raises(ProcessingError, match="unsupported_input"):
        processor.recognize(data.getvalue(), ".png")


def test_blank_image_is_incomplete_not_success(processor):
    image = Image.new("RGB", (300, 150), "white")
    data = io.BytesIO()
    image.save(data, format="PNG")
    with pytest.raises(ProcessingError, match="ocr_failed"):
        processor.recognize(data.getvalue(), ".png")


def test_fully_transparent_hidden_text_is_not_exposed(processor):
    image = Image.new("RGBA", (600, 180), (255, 255, 255, 0))
    ImageDraw.Draw(image).text((20, 20), "HIDDEN PRIVATE SENTINEL", fill=(0, 0, 0, 0))
    data = io.BytesIO()
    image.save(data, format="PNG")
    with pytest.raises(ProcessingError, match="ocr_failed"):
        processor.recognize(data.getvalue(), ".png")
