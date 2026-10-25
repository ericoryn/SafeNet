import re
import unicodedata

from safenet.contracts import Language, NormalizedText, TextSpan

LOOKALIKES = str.maketrans("aceopxykmi@03", "асеорхукміаоз")
EMOJI = {"🎲": " случайный выбор ", "🎰": " казино ", "💸": " оплата ", "💰": " деньги "}
RU_WORDS = {"и", "в", "на", "для", "это", "после", "из", "без", "что", "нет", "деньги", "участие"}
KK_LATIN = {"tegin", "tenge", "qatysu", "aqsha", "qazhet", "qosymsha", "zhok", "zhuldegеr"}
RU_LATIN = {"vznos", "dengi", "poluchi", "priglasi", "uchastie", "besplatnyy", "rozygrysh"}
DIGRAPHS = {
    "shch": "щ",
    "sh": "ш",
    "ch": "ч",
    "zh": "ж",
    "kh": "х",
    "ts": "ц",
    "yo": "ё",
    "yu": "ю",
    "ya": "я",
    "gh": "ғ",
    "ng": "ң",
}
SINGLES = dict(zip("abvgdeziyklmnoprstufheqw", "абвгдезийклмнопрстуфхеқу", strict=True))


def detect_language(text: str) -> Language:
    value = text.casefold()
    words = set(re.findall(r"[^\W\d_]+", value))
    kk = bool(set(value) & set("әғқңөұүһі") or words & KK_LATIN)
    ru = bool(words & (RU_WORDS | RU_LATIN))
    if kk and ru:
        return Language.MIXED
    if kk:
        return Language.KK
    if ru or re.search(r"[а-яё]", value):
        return Language.RU
    return Language.UNKNOWN


def normalize(text: str) -> tuple[NormalizedText, ...]:
    chars, spans = [], []
    position = 0
    while position < len(text):
        end = position + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        cluster = text[position:end]
        value = EMOJI.get(cluster, unicodedata.normalize("NFKC", cluster).casefold())
        for char in value:
            if unicodedata.category(char) != "Cf":
                chars.append(char)
                spans.append(TextSpan(start=position, end=end))
        position = end
    initial = "".join(chars)
    for match in re.finditer(r"[\w@]+", initial):
        if re.search(r"[а-яёәғқңөұүһі]", match.group()):
            for index in range(match.start(), match.end()):
                char = chars[index]
                if char.isdigit() and re.search(r"\d{2,}", match.group()):
                    continue
                chars[index] = (
                    "и"
                    if char == "i" and not set(match.group()) & set("әғқңөұүһі")
                    else char.translate(LOOKALIKES)
                )
    joined = "".join(chars)
    remove = set()
    for match in re.finditer(r"(?<!\w)(?:[^\W\d_]\s+){3,}[^\W\d_](?!\w)", joined):
        remove.update(
            index for index in range(match.start(), match.end()) if chars[index].isspace()
        )
    collapsed_chars, collapsed_spans = [], []
    for index, char in enumerate(chars):
        if index in remove:
            continue
        if char.isspace():
            if not collapsed_chars:
                continue
            if collapsed_chars[-1] == " ":
                collapsed_spans[-1] = TextSpan(
                    start=collapsed_spans[-1].start, end=spans[index].end
                )
                continue
            char = " "
        collapsed_chars.append(char)
        collapsed_spans.append(spans[index])
    if collapsed_chars and collapsed_chars[-1] == " ":
        collapsed_chars.pop()
        collapsed_spans.pop()
    primary = NormalizedText(
        name="normalized", text="".join(collapsed_chars), source_spans=tuple(collapsed_spans)
    )
    variants = [primary]
    protected = [
        match.span()
        for match in re.finditer(
            r"https?://\S+|card-[a-f0-9-]+|syn-[a-f0-9]+",
            primary.text,
        )
    ]
    for language in ("ru", "kk"):
        output, mapping = [], []
        cursor = 0
        for match in re.finditer(r"[a-z]+", primary.text):
            output.extend(primary.text[cursor : match.start()])
            mapping.extend(primary.source_spans[cursor : match.start()])
            if any(start < match.end() and end > match.start() for start, end in protected):
                output.extend(match.group())
                mapping.extend(primary.source_spans[match.start() : match.end()])
            else:
                index = match.start()
                while index < match.end():
                    key = next(
                        (
                            key
                            for key in DIGRAPHS
                            if primary.text.startswith(key, index)
                            and index + len(key) <= match.end()
                        ),
                        primary.text[index],
                    )
                    value = DIGRAPHS.get(key, SINGLES.get(key, key))
                    if language == "kk":
                        value = {"и": "і", "у": "ұ", "о": "ө"}.get(value, value)
                    output.append(value)
                    mapping.append(
                        TextSpan(
                            start=primary.source_spans[index].start,
                            end=primary.source_spans[index + len(key) - 1].end,
                        )
                    )
                    index += len(key)
            cursor = match.end()
        output.extend(primary.text[cursor:])
        mapping.extend(primary.source_spans[cursor:])
        result = "".join(output)
        if result not in {variant.text for variant in variants}:
            variants.append(
                NormalizedText(
                    name=f"translit_{language}", text=result, source_spans=tuple(mapping)
                )
            )
    return tuple(variants)
