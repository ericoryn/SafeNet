"""Скопировать документацию и обезличенные отчёты для статической сборки."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / "cloud/docs"
    target.mkdir(exist_ok=True)
    for source in ROOT.glob("*.md"):
        if source.name != "AGENTS.md":
            shutil.copy2(source, target / source.name)
    if (ROOT / "metrics.json").is_file():
        shutil.copy2(ROOT / "metrics.json", target / "metrics.json")
    reports = target / "reports"
    reports.mkdir(exist_ok=True)
    for source in (ROOT / "reports").iterdir():
        if source.suffix in {".md", ".json", ".jsonl", ".xml"} and not source.name.startswith(
            "casino_"
        ):
            shutil.copy2(source, reports / source.name)
    for evaluation in (ROOT / "reports").glob("evaluation*"):
        if not evaluation.is_dir():
            continue
        (reports / evaluation.name).mkdir(exist_ok=True)
        for source in evaluation.iterdir():
            if source.suffix in {".json", ".toml"}:
                shutil.copy2(source, reports / evaluation.name / source.name)
    presentation = target / "presentation"
    presentation.mkdir(exist_ok=True)
    for name in (
        "README.md",
        "Текст_выступления.md",
        "Слайды_и_источники.json",
        "Проверка_вёрстки.json",
        "SafeNet_KZ_Защита.pdf",
        "SafeNet_KZ_Защита.pptx",
        "Обзор_слайдов.png",
    ):
        shutil.copy2(ROOT / "presentation" / name, presentation / name)
    return target


if __name__ == "__main__":
    print(main())
