import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from safenet.config import DetectorConfig
from safenet.contracts import ErrorCode
from safenet.errors import ProcessingError

MODEL_RELEASE = "4.1.0"
MODEL_HASHES = {
    "rus": "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
    "kaz": "fcc01eed3815a42b9c6321c4c9d3606f39b166cbf95ade98b7d8d12063eae53d",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}


def runtime_version() -> str:
    import tesserocr

    return f"tesserocr {tesserocr.__version__}; {tesserocr.tesseract_version().splitlines()[0]}"


class OCRProcessor:
    def __init__(self, config: DetectorConfig, tessdata: Path):
        self.config = config
        self.tessdata = tessdata.resolve()
        self._slots = threading.BoundedSemaphore(config.ocr.concurrency)
        try:
            for name, expected in MODEL_HASHES.items():
                path = self.tessdata / f"{name}.traineddata"
                if path.stat().st_size > 10 * 1024 * 1024:
                    raise ValueError("model size")
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError("model checksum")
        except (OSError, ValueError):
            raise ProcessingError(ErrorCode.OCR_FAILED) from None

    def recognize(self, image: bytes, suffix: str) -> dict:
        if not image or len(image) > self.config.limits.max_upload_bytes:
            raise ProcessingError(ErrorCode.INVALID_INPUT)
        if suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise ProcessingError(ErrorCode.UNSUPPORTED_INPUT)
        deadline = time.monotonic() + self.config.ocr.timeout_seconds
        if not self._slots.acquire(timeout=self.config.ocr.timeout_seconds):
            raise ProcessingError(ErrorCode.OCR_FAILED)
        process = None
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "safenet.ocr_worker",
                    "--tessdata",
                    str(self.tessdata),
                    "--limits",
                    self.config.limits.model_dump_json(),
                    "--suffix",
                    suffix.lower(),
                    "--languages",
                    self.config.ocr.languages,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
            stdout, _ = process.communicate(image, timeout=max(0.001, deadline - time.monotonic()))
            if process.returncode != 0:
                raise ProcessingError(ErrorCode.OCR_FAILED)
            result = json.loads(stdout)
            if "error_code" in result:
                raise ProcessingError(ErrorCode(result["error_code"]))
            return result
        except subprocess.TimeoutExpired:
            raise ProcessingError(ErrorCode.OCR_FAILED) from None
        except (OSError, ValueError, TypeError):
            raise ProcessingError(ErrorCode.OCR_FAILED) from None
        finally:
            try:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()
            finally:
                self._slots.release()
