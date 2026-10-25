"""Собрать исходники сервера без баз, окружений, токенов и исходных загрузок."""

import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "cloud/private/backend.tgz"


def build():
    files = list((ROOT / "src/safenet").rglob("*.py"))
    files += [
        ROOT / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            "config/detector.toml",
            "config/calibrated.toml",
            "tools/fetch_ocr_models.py",
            "cloud/boot.py",
        )
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(OUTPUT, "w:gz") as archive:
        for path in sorted(files):
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    return OUTPUT


if __name__ == "__main__":
    print(build())
