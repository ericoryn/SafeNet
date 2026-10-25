"""Shared matching/context/evidence handling for all three tracks."""

import re
from bisect import bisect_right
from dataclasses import dataclass

from safenet.contracts import Evidence, FeatureId, PreparedContent, TextSpan
from safenet.features.catalog import CATALOG

REPORTING = (
    r"новост\w*|репортаж\w*|редакци\w*|журналист\w*|"
    r"(?:урок\w*|семинар\w*)[^.!?;]{0,40}(?:обсужд\w*|изуч\w*|разбира\w*)|"
    r"памятк\w*|разбор\w*|жалоб\w*|интервью|обзор обращений|"
    r"жаңалық\w*|жадынама\w*|талдау\w*|шағым\w*|сұхбат\w*|"
    r"novost\w*|reportazh\w*|redakci\w*|zhurnalist\w*|pamyatk\w*|razbor\w*|"
    r"zhalob\w*|intervyu|zhanalyq\w*|zhangalyq\w*|zhadynama\w*|taldau\w*|shaghym\w*"
)
WARNING = r"(?:признак\w*.{0,20}риск|тревожн\w* признак|тәуекел мысал|алаңдат\w* белгі)"
ADVERSATIVE = re.compile(r"\b(?:но|однако|бірақ|odnako|biraq|however|but)\b")
NEGATIVE_BEFORE = re.compile(r"\b(?:не|нет|без|никакого|никакой|ne|net|bez|no|not|without)\s*$")
NEGATIVE_AFTER = re.compile(
    r"^\W*(?:\w+\W+){0,2}(?:не\s+(?:нуж\w*|треб\w*|надо|предлага\w*|обеща\w*|"
    r"гарантир\w*|прода\w*|собира\w*|оплачива\w*|да[её]тся)|нет\b|жоқ\b|емес\b|"
    r"берілмей\w*|төленбей\w*|сатылмай\w*|етілмей\w*|"
    r"ne\s+(?:nuzh\w*|treb\w*|nado|predlag\w*|obesh\w*|garant\w*)|net\b|zhoq\b|zhok\b|emes\b)"
)
QUOTES = re.compile(r"«[^»]*»|“[^”]*”|\"[^\"]*\"")


@dataclass(frozen=True)
class Occurrence:
    evidence: Evidence
    span: TextSpan
    reported: bool
    negated: bool
    clause: str


@dataclass(frozen=True)
class FeatureMatch:
    feature_id: str
    evidence: tuple[Evidence, ...]


def context(text, start, stop, quote_spans):
    # shortcut: context is local (320 chars); upgrade for document-level discourse.
    left, right = max(0, start - 320), min(len(text), stop + 320)
    boundary = max(left, max(text.rfind(char, left, start) + 1 for char in ".!?;\n"))
    stops = [text.find(char, stop, right) for char in ".!?;\n"]
    end = min((position for position in stops if position >= 0), default=right)
    prefix, suffix = text[boundary:start], text[stop:end]
    markers = list(re.finditer(REPORTING, prefix, re.IGNORECASE))
    reported = bool(markers and not ADVERSATIVE.search(prefix[markers[-1].end() :].lower()))
    quote_index = bisect_right(quote_spans, (start, len(text))) - 1
    quoted = quote_index >= 0 and stop <= quote_spans[quote_index][1]
    if quoted and re.search(REPORTING, text[max(0, start - 300) : start], re.IGNORECASE):
        reported = True
    if re.search(WARNING, suffix, re.IGNORECASE):
        reported = True
    negated = bool(NEGATIVE_BEFORE.search(prefix.lower()) or NEGATIVE_AFTER.search(suffix.lower()))
    return reported, negated, text[boundary:end]


def find(
    prepared: PreparedContent, pattern: str, *, asserted: bool = True
) -> tuple[Occurrence, ...]:
    results = []
    seen = set()
    compiled = re.compile(pattern, re.IGNORECASE)
    source = prepared.content.text
    source_quotes = [match.span() for match in QUOTES.finditer(source)]
    for index, variant in enumerate(prepared.variants):
        text = variant.text
        quote_spans = [match.span() for match in QUOTES.finditer(text)]
        for match in compiled.finditer(text):
            span = variant.source_span(match.start(), match.end())
            key = (span.start, span.end)
            if key in seen:
                continue
            reported, negated, clause = context(text, match.start(), match.end(), quote_spans)
            # A lossy transliteration must not erase reporting or negation in the source.
            source_reported, source_negated, _ = context(
                source, span.start, span.end, source_quotes
            )
            reported, negated = reported or source_reported, negated or source_negated
            if asserted and (reported or negated):
                continue
            seen.add(key)
            results.append(
                Occurrence(
                    prepared.evidence(index, match.start(), match.end()),
                    span,
                    reported,
                    negated,
                    clause,
                )
            )
    return tuple(results)


def near(left: tuple[Occurrence, ...], right: tuple[Occurrence, ...]) -> tuple[Occurrence, ...]:
    left = tuple(sorted(left, key=lambda item: item.span.start))
    right = tuple(sorted(right, key=lambda item: item.span.start))
    i, j = 0, 0
    while i < len(left) and j < len(right):
        first, second = left[i], right[j]
        if max(first.span.start, second.span.start) - min(first.span.end, second.span.end) <= 320:
            return first, second
        if first.span.end < second.span.start:
            i += 1
        else:
            j += 1
    return ()


def emit(feature_id: FeatureId, *groups: tuple[Occurrence, ...]) -> FeatureMatch:
    if feature_id not in CATALOG:
        raise ValueError("unknown feature")
    evidence = []
    seen = set()
    for group in groups:
        for occurrence in group:
            key = occurrence.evidence.model_dump_json()
            if key not in seen:
                seen.add(key)
                evidence.append(occurrence.evidence)
                break
    return FeatureMatch(feature_id, tuple(evidence[:3]))


def reporting_context(prepared: PreparedContent) -> tuple[Occurrence, ...]:
    return find(prepared, REPORTING + "|" + WARNING, asserted=False)
