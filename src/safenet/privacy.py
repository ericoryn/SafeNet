"""Pattern masking for synthetic/already-deidentified inputs; no general name recognition."""

import re
import unicodedata
from urllib.parse import urlsplit

from safenet.contracts import Redaction, TextSpan

URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
CARD = re.compile(r"(?<![\w])(?:\d[ -]?){12,18}\d(?![\w])")
PHONE = re.compile(r"(?<![\w])(?:\+?7|8)[\s()-]*(?:\d[\s()-]*){10}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[^\W\d_]{2,}", re.UNICODE)
HANDLE = re.compile(r"(?<![\w])@[\w]{3,}", re.UNICODE)
LETTER = "A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі"
UPPER = "A-ZА-ЯЁӘҒҚҢӨҰҮҺІ"
PERSON = re.compile(
    rf"(?<![\w])([{UPPER}][{LETTER}'’-]+(?:[ \t]+[{UPPER}][{LETTER}'’-]+){{1,5}})(?![\w'-])"
)
LABELED_NAME = re.compile(
    rf"(?:фио|ф\.?и\.?о\.?|аты-жөні|автор|получатель|имя|name)\s*[:=]\s*"
    rf"([{LETTER}'’-]+(?:[ \t]+[{LETTER}'’-]+){{0,2}})",
    re.IGNORECASE,
)
MARKERS = {"SYNTHETIC TEST DATA", "СИНТЕТИЧЕСКИЙ ПРИМЕР", "СИНТЕТИКАЛЫҚ МЫСАЛ"}
SAFE_QUERY_KEYS = {"ref", "promo", "code", "utm_source", "utm_medium", "utm_campaign"}


def canonical_source(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text) if unicodedata.category(char) != "Cf"
    )


def redact(text: str) -> tuple[str, tuple[Redaction, ...]]:
    """Keep string length fixed so masking cannot invalidate OCR/source spans."""
    output = list(text)
    redactions = []
    surrogates = [
        match.span()
        for match in re.finditer(
            r"\b(?:card|account)-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b",
            text,
            re.I,
        )
    ]

    def mask(start: int, end: int, kind: str, keep_digits: int = 0) -> None:
        if kind in ("card", "phone") and any(a <= start and end <= b for a, b in surrogates):
            return
        digits = [index for index in range(start, end) if text[index].isdigit()]
        kept = set(digits[-keep_digits:]) if keep_digits else set()
        for index in range(start, end):
            if index not in kept and not text[index].isspace():
                output[index] = "*"
        redactions.append(Redaction(kind=kind, span=TextSpan(start=start, end=end)))

    for match in URL.finditer(text):
        value = match.group().rstrip(".,;!)]}")
        try:
            parsed = urlsplit(value)
            if not parsed.hostname:
                raise ValueError("missing host")
        except ValueError:
            mask(match.start(), match.start() + len(value), "url_private")
            continue
        if parsed.username is not None or parsed.password is not None:
            mask(match.start(), match.start() + len(value), "url_private")
            continue
        authority_end = len(parsed.scheme) + 3 + len(parsed.netloc)
        suffix = value[authority_end:]
        if suffix:
            for item in re.finditer(r"[^/?&#=]+", suffix):
                piece = item.group()
                is_key = item.end() < len(suffix) and suffix[item.end()] == "="
                if is_key and piece.casefold() in SAFE_QUERY_KEYS:
                    continue
                mask(
                    match.start() + authority_end + item.start(),
                    match.start() + authority_end + item.end(),
                    "url_private",
                )

    search = "".join(output)
    card_spans = []
    for match in CARD.finditer(search):
        digit_count = sum(char.isdigit() for char in match.group())
        if 13 <= digit_count <= 19:
            mask(match.start(), match.end(), "card", keep_digits=4)
            card_spans.append((match.start(), match.end()))
    for match in PHONE.finditer(search):
        if not any(start < match.end() and end > match.start() for start, end in card_spans):
            mask(match.start(), match.end(), "phone", keep_digits=2)
    for pattern, kind in ((EMAIL, "email"), (HANDLE, "handle")):
        for match in pattern.finditer("".join(output)):
            mask(match.start(), match.end(), kind)
    for match in LABELED_NAME.finditer("".join(output)):
        mask(match.start(1), match.end(1), "name")
    for match in PERSON.finditer("".join(output)):
        if match.group(1) not in MARKERS:
            mask(match.start(1), match.end(1), "name")
    return "".join(output), tuple(redactions)
