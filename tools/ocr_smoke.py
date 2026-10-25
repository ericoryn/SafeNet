"""Real OCR feasibility check on two synthetic, clean screenshots, not an accuracy benchmark."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

from safenet.config import load_config
from safenet.ocr import MODEL_HASHES

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/ocr"


def character_error_rate(expected: str, actual: str) -> float:
    expected = " ".join(expected.casefold().split())
    actual = " ".join(actual.casefold().split())
    if not expected:
        raise ValueError("reference text must not be empty")
    previous = list(range(len(actual) + 1))
    for row, reference in enumerate(expected, start=1):
        current = [row]
        for col, recognized in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[col] + 1,
                    previous[col - 1] + (reference != recognized),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def generate_fixtures(font_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(font_path), 36)
    for language in ("ru", "kk"):
        text = (FIXTURES / f"{language}.txt").read_text(encoding="utf-8").strip()
        lines = text.splitlines()
        image = Image.new("RGB", (1500, 80 + 60 * len(lines)), "white")
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            draw.text((40, 30 + index * 60), line, font=font, fill="black")
        image.save(FIXTURES / f"{language}.png")


def recognize(image: Path, tessdata: Path) -> str:
    from safenet.ocr_worker import recognize_image

    config = load_config(ROOT / "config/detector.toml")
    return recognize_image(
        image.read_bytes(), tessdata, config.limits, image.suffix, config.ocr.languages
    )["text"]


def run_check(tessdata: Path, output: Path) -> dict:
    import tesserocr
    from PIL import __version__ as pillow_version

    model_hashes = {}
    for language in MODEL_HASHES:
        model_hashes[language] = hashlib.sha256(
            (tessdata / f"{language}.traineddata").read_bytes()
        ).hexdigest()
    samples = []
    for language in ("ru", "kk"):
        expected = (FIXTURES / f"{language}.txt").read_text(encoding="utf-8").strip()
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                str(FIXTURES / f"{language}.png"),
                "--tessdata",
                str(tessdata.resolve()),
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=20,
            check=True,
        )
        actual = json.loads(completed.stdout)
        samples.append(
            {
                "language": language,
                "synthetic": True,
                "expected": expected,
                "recognized": actual.strip(),
                "nonempty": bool(actual.strip()),
                "character_error_rate": character_error_rate(expected, actual),
            }
        )
    report = {
        "scope": "two clean synthetic screenshots; not real-world OCR accuracy",
        "platform": platform.system(),
        "python": platform.python_version(),
        "tesserocr": tesserocr.__version__,
        "pillow": pillow_version,
        "tesseract": tesserocr.tesseract_version(),
        "languages": load_config(ROOT / "config/detector.toml").ocr.languages,
        "model_sha256": model_hashes,
        "samples": samples,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tessdata", type=Path, default=ROOT / ".tools/tessdata")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/ocr_smoke.json")
    parser.add_argument("--generate-fixtures", type=Path, metavar="FONT")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(recognize(args.worker, args.tessdata), ensure_ascii=True))
        return
    if args.generate_fixtures:
        generate_fixtures(args.generate_fixtures)
    report = run_check(args.tessdata, args.output)
    for sample in report["samples"]:
        print(f"{sample['language']}: character error rate = {sample['character_error_rate']:.4f}")
    if not all(sample["nonempty"] for sample in report["samples"]):
        raise SystemExit("OCR smoke check failed: empty extraction")


if __name__ == "__main__":
    main()
