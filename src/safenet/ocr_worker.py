"""One-shot native OCR subprocess. Raw pixels/text exist only in memory/pipes."""

import argparse
import io
import json
import sys
import warnings
from pathlib import Path

from safenet.config import Limits
from safenet.contracts import ErrorCode
from safenet.errors import ProcessingError
from safenet.ocr import runtime_version
from safenet.privacy import canonical_source


def recognize_image(
    data: bytes, tessdata: Path, limits: Limits, suffix: str, languages: str
) -> dict:
    from PIL import Image, ImageOps, UnidentifiedImageError
    from tesserocr import OEM, PSM, RIL, PyTessBaseAPI

    if len(data) > limits.max_upload_bytes:
        raise ProcessingError(ErrorCode.INVALID_INPUT)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as header:
                expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
                if header.format != expected.get(suffix) or getattr(header, "n_frames", 1) != 1:
                    raise ProcessingError(ErrorCode.UNSUPPORTED_INPUT)
                width, height = header.size
                if (
                    max(width, height) > limits.max_image_dimension
                    or width * height > limits.max_image_pixels
                ):
                    raise ProcessingError(ErrorCode.INVALID_INPUT)
                header.verify()
            with Image.open(io.BytesIO(data)) as original:
                oriented = ImageOps.exif_transpose(original).convert("RGBA")
                background = Image.new("RGBA", oriented.size, "white")
                background.alpha_composite(oriented)
                image = background.convert("RGB")
                oriented.close()
                background.close()
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ProcessingError(ErrorCode.INVALID_INPUT) from None
    text, words = "", []
    with (
        image,
        PyTessBaseAPI(
            path=str(tessdata), lang=languages, oem=OEM.LSTM_ONLY, psm=PSM.SINGLE_BLOCK
        ) as engine,
    ):
        engine.SetImage(image)
        engine.Recognize()
        iterator = engine.GetIterator()
        if iterator is None:
            raise ProcessingError(ErrorCode.OCR_FAILED)
        while True:
            value = canonical_source(iterator.GetUTF8Text(RIL.WORD) or "").strip()
            box = iterator.BoundingBox(RIL.WORD)
            if value and box:
                if len(words) >= limits.max_ocr_words:
                    raise ProcessingError(ErrorCode.OCR_FAILED)
                separator = "\n" if iterator.IsAtBeginningOf(RIL.TEXTLINE) else " "
                if text:
                    text += separator
                start = len(text)
                text += value
                if len(text) > limits.max_text_characters:
                    raise ProcessingError(ErrorCode.OCR_FAILED)
                left, top, right, bottom = box
                words.append(
                    {
                        "span": {"start": start, "end": len(text)},
                        "region": {
                            "x": left,
                            "y": top,
                            "width": right - left,
                            "height": bottom - top,
                        },
                        "confidence": max(0, min(100, float(iterator.Confidence(RIL.WORD)))),
                    }
                )
            if not iterator.Next(RIL.WORD):
                break
        if not text.strip():
            raise ProcessingError(ErrorCode.OCR_FAILED)
        return {
            "text": text,
            "words": words,
            "width": image.width,
            "height": image.height,
            "engine_version": runtime_version(),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tessdata", type=Path, required=True)
    parser.add_argument("--limits", required=True)
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--languages", required=True)
    args = parser.parse_args()
    try:
        limits = Limits.model_validate_json(args.limits)
        data = sys.stdin.buffer.read(limits.max_upload_bytes + 1)
        result = recognize_image(data, args.tessdata, limits, args.suffix, args.languages)
    except ProcessingError as error:
        result = {"error_code": error.code.value}
    except Exception:
        # Native/Pillow failures must not print recognized text or exception payloads.
        result = {"error_code": ErrorCode.OCR_FAILED.value}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
