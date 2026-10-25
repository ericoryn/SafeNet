"""Download only pinned official language models; never invoked by an API request."""

import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen

from safenet.ocr import MODEL_HASHES, MODEL_RELEASE

MAX_MODEL_BYTES = 10 * 1024 * 1024


def fetch_models(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for language, expected_hash in MODEL_HASHES.items():
        target = destination / f"{language}.traineddata"
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected_hash:
            continue
        url = (
            "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
            f"{MODEL_RELEASE}/{language}.traineddata"
        )
        with urlopen(url, timeout=30) as response:
            data = response.read(MAX_MODEL_BYTES + 1)
        if len(data) > MAX_MODEL_BYTES:
            raise ValueError("OCR model exceeds download limit")
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError("OCR model checksum mismatch")
        temporary = target.with_suffix(".tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path(".tools/tessdata"))
    args = parser.parse_args()
    fetch_models(args.destination)
    print("Verified Russian, Kazakh, and English OCR models (SHA-256).")
