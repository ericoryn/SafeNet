"""Generate grouped synthetic data without placing evaluator metadata in detector inputs."""

import argparse
import hashlib
import json
import random
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from safenet.contracts import Contract, Language, Track
from safenet.evaluation.templates import POSITIVE_TRACKS, TEMPLATES

GENERATOR_VERSION = "1"
SPLITS = ("train", "validation", "test")
STYLES = ("plain", "translit", "slang", "obfuscated")
LANGUAGES = (Language.RU, Language.KK, Language.MIXED)
CYRILLIC = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяәғқңөұүһі"
LATIN = (
    "a",
    "b",
    "v",
    "g",
    "d",
    "e",
    "yo",
    "zh",
    "z",
    "i",
    "y",
    "k",
    "l",
    "m",
    "n",
    "o",
    "p",
    "r",
    "s",
    "t",
    "u",
    "f",
    "kh",
    "ts",
    "ch",
    "sh",
    "shch",
    "",
    "y",
    "",
    "e",
    "yu",
    "ya",
    "a",
    "gh",
    "q",
    "ng",
    "o",
    "u",
    "u",
    "h",
    "i",
)
TRANSLIT = str.maketrans(
    {
        **dict(zip(CYRILLIC, LATIN, strict=True)),
        **{
            letter.upper(): value.capitalize()
            for letter, value in zip(CYRILLIC, LATIN, strict=True)
        },
    }
)


class SyntheticInput(Contract):
    case_id: UUID
    provenance: Literal["synthetic"] = "synthetic"
    synthetic: bool = Field(strict=True)
    text: str = Field(repr=False)
    source_kind: Literal["text", "screenshot", "transcript"]
    synthetic_account_id: UUID
    created_at: AwareDatetime
    artifact_id: UUID | None = None
    image_path: str | None = None
    redirect_chain: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_synthetic_boundary(self) -> Self:
        if not self.synthetic:
            raise ValueError("generated inputs must be explicitly synthetic")
        if self.source_kind == "screenshot":
            if (
                self.text
                or self.artifact_id is None
                or self.image_path != f"images/{self.case_id}.png"
            ):
                raise ValueError("screenshot needs an opaque asset path and no reference text")
        elif not self.text or self.artifact_id is not None or self.image_path is not None:
            raise ValueError("text/transcript requires text and no image reference")
        for url in self.redirect_chain:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not (parsed.hostname or "").endswith(".test")
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("redirect fixtures must use reserved HTTPS test domains")
        return self


class GroundTruth(Contract):
    case_id: UUID
    family_id: UUID
    campaign_id: UUID
    scenario: str
    language: Literal[Language.RU, Language.KK, Language.MIXED]
    style: Literal["plain", "translit", "slang", "obfuscated"]
    labels: dict[Track, Literal["suspicious", "benign"]]
    ocr_reference: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def check_labels(self) -> Self:
        if set(self.labels) != set(Track):
            raise ValueError("ground truth must label all three tracks")
        if self.scenario not in TEMPLATES:
            raise ValueError("unknown synthetic scenario")
        return self


def identity(seed: int, value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"safenet-kz-synthetic-v{GENERATOR_VERSION}:{seed}:{value}")


def family_assignments(seed: int) -> dict[str, list[tuple[str, int]]]:
    assignments = {split: [] for split in SPLITS}
    for scenario, families in TEMPLATES.items():
        if len(families) != 5:
            raise ValueError("each scenario requires five independent template families")
        order = list(range(5))
        random.Random(f"{seed}:{scenario}").shuffle(order)
        for split, indices in zip(SPLITS, (order[:3], order[3:4], order[4:]), strict=True):
            assignments[split].extend((scenario, index) for index in indices)
    return assignments


def surface_variant(text: str, language: Language, style: str, source_kind: str) -> str:
    # Transform only the message, preserving fixture URLs and opaque synthetic identifiers.
    pieces = re.split(r"(https://\S+|SYN-[a-f0-9]+|card-[a-f0-9-]+)", text)
    for index in range(0, len(pieces), 2):
        value = pieces[index]
        if style == "translit":
            value = value.translate(TRANSLIT)
        elif style == "slang":
            for original, replacement in (
                ("получи", "забери"),
                ("сегодня", "сёдня"),
                ("теңге", "тг"),
                ("деньги", "деньжата"),
            ):
                value = value.replace(original, replacement)
        elif style == "obfuscated":
            for original, replacement in (
                ("казино", "к@зино"),
                ("Казино", "К@зино"),
                ("гарантирован", "г@рантирован"),
                ("кепілді", "к е п і л д і"),
                ("ақша", "а қ ш а"),
                ("бонус", "б о н у с"),
            ):
                value = value.replace(original, replacement)
            value = re.sub(
                r"[аеоіә]",
                lambda match: {"а": "@", "е": "e", "о": "0", "і": "i", "ә": "a"}[match.group()],
                value,
                count=1,
            )
        pieces[index] = value
    result = "".join(pieces)
    if style == "slang":
        result += (
            " Ағайын, толығырақ жоғарыда." if language == Language.KK else " Ребят, детали выше."
        )
    if style == "obfuscated" and source_kind != "screenshot":
        result += " 🎲 ✦"
    return "SYNTHETIC TEST DATA\n" + result


def render_screenshot(text: str, path: Path, font) -> None:
    from PIL import Image, ImageDraw

    lines = []
    for paragraph in text.splitlines():
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if font.getlength(candidate) > 1520:
                if not line or font.getlength(word) > 1520:
                    raise ValueError("fixture token exceeds image width")
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    image = Image.new("RGB", (1600, 60 + 44 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((40, 24 + 44 * index), line, font=font, fill="black")
    image.save(path, compress_level=6)


def default_font() -> Path:
    for path in (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ):
        if path.is_file():
            return path
    raise FileNotFoundError("supply --font with a Russian/Kazakh-capable TrueType font")


def canonical_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"https://\S+", "<url>", value)
    value = re.sub(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", "<id>", value)
    value = re.sub(r"syn-[a-f0-9]+", "<promo>", value)
    value = re.sub(r"\d+", "<number>", value)
    return " ".join(value.split())


def read_jsonl(path: Path, model: type[Contract]) -> list:
    with path.open(encoding="utf-8") as source:
        return [model.model_validate_json(line) for line in source if line.strip()]


def audit_dataset(root: Path) -> dict:
    """Structural audit only: does not run or calibrate the detector on held-out data."""
    ownership: dict[str, dict[str, str]] = {
        key: {} for key in ("family", "campaign", "account", "entity", "content", "image")
    }
    seen_ids = set()
    summary = {}
    for split in SPLITS:
        inputs = read_jsonl(root / "inputs" / f"{split}.jsonl", SyntheticInput)
        truth = read_jsonl(root / "ground_truth" / f"{split}.jsonl", GroundTruth)
        by_id = {row.case_id: row for row in truth}
        if len(by_id) != len(truth) or len(inputs) != len(truth):
            raise ValueError("duplicate or mismatched ground-truth records")
        if {row.case_id for row in inputs} != set(by_id):
            raise ValueError("input and ground-truth case IDs differ")
        languages, styles, kinds, scenarios = Counter(), Counter(), Counter(), Counter()
        track_counts = {track.value: Counter() for track in Track}
        families, campaigns, accounts = set(), set(), set()
        overlap = 0
        for row in inputs:
            if row.case_id in seen_ids:
                raise ValueError("duplicate case ID")
            seen_ids.add(row.case_id)
            target = by_id[row.case_id]
            expected_labels = {
                track: "suspicious"
                if track in POSITIVE_TRACKS.get(target.scenario, ())
                else "benign"
                for track in Track
            }
            if target.labels != expected_labels:
                raise ValueError("scenario and labels disagree")
            text = row.text
            keys = {
                "family": [str(target.family_id)],
                "campaign": [str(target.campaign_id)],
                "account": [str(row.synthetic_account_id)],
            }
            if row.image_path:
                image_path = root / row.image_path
                if not image_path.is_file() or not target.ocr_reference:
                    raise ValueError("missing screenshot or OCR reference")
                keys["image"] = [hashlib.sha256(image_path.read_bytes()).hexdigest()]
                text = target.ocr_reference
            elif target.ocr_reference is not None:
                raise ValueError("text input must not carry an OCR reference")
            if not text.startswith("SYNTHETIC TEST DATA\n"):
                raise ValueError("synthetic content marker missing")
            urls = re.findall(r"https://[^\s]+", text) + list(row.redirect_chain)
            if not urls or any(
                not (urlsplit(url).hostname or "").endswith(".test") for url in urls
            ):
                raise ValueError("generated content must use reserved test domains")
            keys["entity"] = [urlsplit(url).hostname for url in urls]
            keys["entity"] += re.findall(r"(?:card-[a-f0-9-]+|SYN-[a-f0-9]+)", text)
            keys["content"] = [canonical_text(text)]
            for kind, values in keys.items():
                for value in values:
                    previous = ownership[kind].setdefault(value, split)
                    if previous != split:
                        raise ValueError(f"cross-split {kind} leakage")
            languages[target.language.value] += 1
            styles[target.style] += 1
            kinds[row.source_kind] += 1
            scenarios[target.scenario] += 1
            families.add(target.family_id)
            campaigns.add(target.campaign_id)
            accounts.add(row.synthetic_account_id)
            overlap += sum(label == "suspicious" for label in target.labels.values()) > 1
            for track, label in target.labels.items():
                track_counts[track.value][label] += 1
        if (
            set(languages) != {language.value for language in LANGUAGES}
            or set(styles) != set(STYLES)
            or set(kinds) != {"text", "transcript", "screenshot"}
            or set(scenarios) != set(TEMPLATES)
            or not overlap
        ):
            raise ValueError("split lacks required language, style, modality, scenario, or overlap")
        summary[split] = {
            "records": len(inputs),
            "families": len(families),
            "campaigns": len(campaigns),
            "accounts": len(accounts),
            "languages": dict(languages),
            "styles": dict(styles),
            "source_kinds": dict(kinds),
            "scenarios": dict(scenarios),
            "track_labels": {key: dict(value) for key, value in track_counts.items()},
            "overlapping_track_records": overlap,
        }
    return {
        "total_records": len(seen_ids),
        "splits": summary,
        "cross_split_overlap": dict.fromkeys(ownership, 0),
        "detector_evaluated": False,
    }


def generate_dataset(
    output: Path, *, count: int = 1200, seed: int = 42, font_path: Path | None = None
) -> dict:
    if isinstance(count, bool) or not isinstance(count, int) or not 600 <= count <= 10000:
        raise ValueError("count must be an integer from 600 to 10000 to preserve required coverage")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if output.exists():
        raise FileExistsError("output already exists; choose a new directory")
    from PIL import ImageFont
    from PIL import __version__ as pillow_version

    font_path = font_path or default_font()
    font = ImageFont.truetype(str(font_path), 28)
    assignments = family_assignments(seed)
    quotas = {"train": count * 3 // 5, "validation": count // 5}
    quotas["test"] = count - sum(quotas.values())
    output.mkdir(parents=True)
    for directory in ("inputs", "ground_truth", "images"):
        (output / directory).mkdir()
    for split in SPLITS:
        records = []
        for number, (scenario, family_index) in enumerate(assignments[split]):
            families = len(assignments[split])
            size = quotas[split] // families + (number < quotas[split] % families)
            family_id = identity(seed, f"family:{scenario}:{family_index}")
            campaign_id = identity(seed, f"campaign:{family_id}")
            for index in range(size):
                case_id = identity(seed, f"case:{family_id}:{index}")
                account_id = identity(seed, f"account:{campaign_id}:{index % 2}")
                campaign_start = random.Random(f"{seed}:{campaign_id}:start").randrange(180 * 1440)
                language = LANGUAGES[index % 3]
                style = STYLES[(index // 3) % 4]
                kind = ("screenshot", "transcript", "text")[(index // 3 + index % 3) % 3]
                url = f"https://d-{campaign_id.hex}.test/offer?ref={campaign_id.hex[:8]}"
                short_url = f"https://go-{campaign_id.hex}.test/r"
                values = {
                    "amount": 1000 + 500 * (index % 8),
                    "reward": 2 * (1000 + 500 * (index % 8)),
                    "slots": 2 + index % 3,
                    "promo": f"SYN-{campaign_id.hex[:8]}",
                    "card": f"card-{campaign_id}",
                    "url": short_url if index % 4 == 0 else url,
                }
                ru, kk = (
                    template.format(**values) for template in TEMPLATES[scenario][family_index]
                )
                mixed = ru.partition(". ")[0] + ". " + (kk.partition(". ")[2] or kk)
                text = surface_variant(
                    {Language.RU: ru, Language.KK: kk, Language.MIXED: mixed}[language],
                    language,
                    style,
                    kind,
                )
                image_path = f"images/{case_id}.png" if kind == "screenshot" else None
                if image_path:
                    render_screenshot(text, output / image_path, font)
                item = SyntheticInput(
                    case_id=case_id,
                    synthetic=True,
                    text="" if image_path else text,
                    source_kind=kind,
                    synthetic_account_id=account_id,
                    artifact_id=identity(seed, f"artifact:{case_id}") if image_path else None,
                    image_path=image_path,
                    redirect_chain=(short_url, url) if index % 4 == 0 else (),
                    created_at=datetime(2026, 1, 1, tzinfo=UTC)
                    + timedelta(minutes=campaign_start + index),
                )
                target = GroundTruth(
                    case_id=case_id,
                    family_id=family_id,
                    campaign_id=campaign_id,
                    scenario=scenario,
                    language=language,
                    style=style,
                    labels={
                        track: "suspicious"
                        if track in POSITIVE_TRACKS.get(scenario, ())
                        else "benign"
                        for track in Track
                    },
                    ocr_reference=text if image_path else None,
                )
                records.append((item, target))
        random.Random(f"{seed}:{split}:record-order").shuffle(records)
        with (
            (output / "inputs" / f"{split}.jsonl").open(
                "x", encoding="utf-8", newline="\n"
            ) as inputs_file,
            (output / "ground_truth" / f"{split}.jsonl").open(
                "x", encoding="utf-8", newline="\n"
            ) as truth_file,
        ):
            for item, target in records:
                inputs_file.write(item.model_dump_json() + "\n")
                truth_file.write(target.model_dump_json() + "\n")
    audit = audit_dataset(output)
    hashes = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "generator_sha256": hashlib.sha256(
            Path(__file__).read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest(),
        "seed": seed,
        "synthetic": True,
        "pillow_version": pillow_version,
        "font_name": font_path.name,
        "font_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
        "template_sha256": hashlib.sha256(
            json.dumps(TEMPLATES, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "split_policy": "per scenario: 3/5 families train, 1/5 validation, 1/5 test",
        "test_policy": "structural audit only; no detector evaluation or calibration",
        **audit,
        "file_sha256": hashes,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/synthetic-v1"))
    parser.add_argument("--count", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    manifest = generate_dataset(args.output, count=args.count, seed=args.seed, font_path=args.font)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "total_records": manifest["total_records"],
                "splits": {name: data["records"] for name, data in manifest["splits"].items()},
                "cross_split_overlap": manifest["cross_split_overlap"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
