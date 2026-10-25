import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from safenet.contracts import ExtractedEntity, PreparedContent, TextSpan
from safenet.privacy import URL

SURROGATE = re.compile(r"\b(card|account)-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b", re.I)
PROMO = re.compile(
    r"\bSYN-[a-f0-9]{8}\b|(?:промо[ -]*код|promo[ -]*(?:code|kod)|code|код|kod)"
    r"(?:ом|ымен|ына|у)?\b\s*[:=]?\s*([a-z0-9][a-z0-9_-]{2,40})\b"
    r"|\b([a-z0-9][a-z0-9_-]{2,40})\s+(?:кодымен|промокодымен|промокоды)\b",
    re.I,
)
AMOUNT = re.compile(
    r"(?<![\w*])(?P<number>\d+(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?)"
    r"\s*(?:(?:теңге|тенге|тг|kzt|tenge|tg)\b|₸)",
    re.I,
)


def extract_entities(prepared: PreparedContent) -> tuple[ExtractedEntity, ...]:
    text = prepared.content.text
    entities = []
    seen = set()

    def add(kind: str, value: str, start: int, end: int) -> None:
        key = (kind, value, start, end)
        if key in seen:
            return
        if kind != "payment_reference" and any(
            item.span.start < end and item.span.end > start for item in prepared.redactions
        ):
            return
        seen.add(key)
        entities.append(
            ExtractedEntity(
                kind=kind,
                value=value,
                evidence=prepared.source_evidence(TextSpan(start=start, end=end)),
            )
        )

    for match in URL.finditer(text):
        try:
            parsed = urlsplit(match.group())
            hostname = parsed.hostname
            if not hostname or "*" in hostname:
                continue
            source_host = parsed.netloc
            bracket = source_host.startswith("[")
            source_host = (
                source_host[1 : source_host.index("]")] if bracket else source_host.split(":")[0]
            )
            start = match.start() + len(parsed.scheme) + 3 + int(bracket)
            add(
                "domain",
                hostname.encode("idna").decode("ascii").lower(),
                start,
                start + len(source_host),
            )
        except (ValueError, UnicodeError):
            continue
    for match in SURROGATE.finditer(text):
        add(
            "synthetic_card" if match.group(1).lower() == "card" else "account",
            match.group().lower(),
            match.start(),
            match.end(),
        )
    for match in PROMO.finditer(text):
        group = 1 if match.group(1) else 2 if match.group(2) else 0
        start, end = match.span(group)
        add("promo_code", text[start:end].casefold(), start, end)
    for match in AMOUNT.finditer(text):
        try:
            value = Decimal(re.sub(r"\s", "", match.group("number")).replace(",", "."))
            add("amount", f"{value:f} KZT", match.start(), match.end())
        except InvalidOperation:
            continue
    for item in prepared.redactions:
        if item.kind == "card":
            add("payment_reference", "masked_card", item.span.start, item.span.end)
    return tuple(entities)
