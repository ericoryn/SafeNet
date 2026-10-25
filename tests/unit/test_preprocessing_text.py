import csv
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from safenet.config import DetectorConfig
from safenet.contracts import IncomingRecord
from safenet.errors import ProcessingError
from safenet.normalization import detect_language, normalize
from safenet.preprocessing import (
    iter_records,
    load_prepared,
    preprocess_record,
    read_image,
    save_prepared,
)
from safenet.privacy import canonical_source, redact


def incoming(text="Тегін байқау, төлем қажет емес.", **changes):
    return IncomingRecord.model_validate(
        {"provenance": "synthetic", "synthetic": True, "text": text, **changes}
    )


@pytest.mark.parametrize(
    "source,expected",
    [
        ("пiрамида", "пирамида"),
        ("үшiн", "үшін"),
        ("к@зино", "казино"),
        ("а қ ш а", "ақша"),
        ("Ә Ғ Қ Ң Ө Ұ Ү Һ І", "әғқңөұүһі"),
        ("1000тг", "1000тг"),
        ("🎲", "случайный выбор"),
        ("И\u0306", "й"),
    ],
)
def test_normalization_and_exact_source_mapping(source, expected):
    variant = normalize(source)[0]
    assert variant.text == expected
    span = variant.source_span(0, len(variant.text))
    assert source[span.start : span.end] == source
    assert len(variant.source_spans) == len(variant.text)


def test_transliteration_keeps_primary_and_evidence(config):
    result = preprocess_record(incoming("Vznos 5000 tg. qatysu tegin."), config)
    assert result.variants[0].text == "vznos 5000 tg. qatysu tegin."
    variant = next(item for item in result.variants if item.name == "translit_ru")
    assert "взнос" in variant.text
    index = result.variants.index(variant)
    start = variant.text.index("взнос")
    assert result.evidence(index, start, start + 5).quote == "Vznos"
    assert any("қ" in item.text for item in result.variants if item.name == "translit_kk")


@pytest.mark.parametrize(
    "text,language",
    [
        ("Участие без оплаты и без взноса.", "ru"),
        ("Тегін байқау, қатысу үшін төлем жоқ.", "kk"),
        ("Участие без оплаты. Қатысу тегін.", "mixed"),
        ("qatysu tegin", "kk"),
        ("vznos dengi", "ru"),
        ("xyz", "unknown"),
    ],
)
def test_language_heuristic(text, language):
    assert detect_language(text).value == language


def test_masks_sensitive_sentinels_before_every_derived_output(config, tmp_path, caplog):
    text = (
        "ФИО: Тестов Тест Тестович; Аты-жөні: Әлиев Нұрлан Сынақұлы; "
        "name: Test Person; телефон +7 000 111 22 33; карта 0000 1111 2222 3333; "
        "sentinel.private@example.test @private_sentinel "
        "https://demo.test/private/secret?ref=SECRET_SENTINEL#private_fragment"
    )
    result = preprocess_record(incoming(text), config)
    target = save_prepared(result, tmp_path)
    serialized = (target / "prepared.json").read_text(encoding="utf-8")
    for sentinel in (
        "Тестов",
        "Нұрлан",
        "Person",
        "+7 000 111 22 33",
        "0000 1111 2222 3333",
        "sentinel.private",
        "private_sentinel",
        "SECRET_SENTINEL",
        "private_fragment",
    ):
        assert sentinel not in serialized
        assert sentinel not in caplog.text
    assert len(result.content.text) == len(text)
    assert "3333" in result.content.text
    assert "33" in result.content.text
    assert {item.kind for item in result.redactions} >= {"name", "phone", "card", "email", "handle"}
    assert any(
        entity.kind == "payment_reference" and entity.value == "masked_card"
        for entity in result.entities
    )
    assert load_prepared(target, config).content == result.content


def test_masking_handles_zero_width_and_fullwidth_digits():
    raw = canonical_source("+７\u200b ０００ １１１ ２２ ３３")
    masked, redactions = redact(raw)
    assert "+7 000 111 22 33" not in masked
    assert any(item.kind == "phone" for item in redactions)


def test_explicit_synthetic_uuid_tokens_are_not_payment_details():
    value = "card-12345678-1234-1234-1234-123456789012"
    masked, redactions = redact(value)
    assert masked == value
    assert not redactions


def test_entities_have_correct_sanitized_evidence_and_decimal_amounts(config):
    card = f"card-{uuid4()}"
    result = preprocess_record(
        incoming(
            f"Взнос 5 000,50 теңге; ещё 25₸. Промокод SYN-abcdef12. {card} "
            "https://example.test/offer?ref=PRIVATE_SENTINEL"
        ),
        config,
    )
    values = {(entity.kind, entity.value) for entity in result.entities}
    assert ("amount", "5000.50 KZT") in values
    assert ("amount", "25 KZT") in values
    assert ("domain", "example.test") in values
    assert ("promo_code", "syn-abcdef12") in values
    assert ("synthetic_card", card) in values
    for entity in result.entities:
        span = entity.evidence.span
        assert result.content.text[span.start : span.end] == entity.evidence.quote
    assert "PRIVATE_SENTINEL" not in result.model_dump_json()


def test_cache_invalidation_and_no_overwrite(config, tmp_path):
    prepared = preprocess_record(incoming(), config)
    directory = save_prepared(prepared, tmp_path)
    with pytest.raises(FileExistsError):
        save_prepared(prepared, tmp_path)
    data = config.model_dump()
    data["thresholds"]["medium"] += 1
    assert load_prepared(directory, DetectorConfig.model_validate(data)).content == prepared.content
    data["ocr"]["timeout_seconds"] += 1
    with pytest.raises(ProcessingError):
        load_prepared(directory, DetectorConfig.model_validate(data))


@pytest.mark.parametrize(
    "changes",
    [
        {"label": "benign"},
        {"text": ""},
        {"synthetic": False},
        {
            "source_kind": "screenshot",
            "text": "",
            "artifact_id": str(uuid4()),
            "image_path": "../raw.png",
        },
        {
            "source_kind": "screenshot",
            "text": "",
            "artifact_id": str(uuid4()),
            "image_path": "C:/raw.png",
        },
    ],
)
def test_input_boundary_rejects_answers_and_unsafe_inputs(changes):
    with pytest.raises(ValidationError):
        incoming(**changes)


def test_size_record_count_and_malformed_json_errors_are_sanitized(config, tmp_path):
    data = config.model_dump()
    data["limits"]["max_text_characters"] = 5
    with pytest.raises(ProcessingError) as error:
        preprocess_record(
            incoming("SYNTHETIC-PRIVATE-SENTINEL"), DetectorConfig.model_validate(data)
        )
    assert "SENTINEL" not in str(error.value)
    source = tmp_path / "bad.jsonl"
    source.write_text('{"text":"SYNTHETIC-PRIVATE-SENTINEL"', encoding="utf-8")
    with pytest.raises(ProcessingError) as error:
        list(iter_records(source, config))
    assert str(error.value) == "invalid_input"
    source.write_text("", encoding="utf-8")
    with pytest.raises(ProcessingError):
        list(iter_records(source, config))


def test_csv_and_jsonl_read_identical_records(config, tmp_path):
    row = incoming().model_dump(mode="json", exclude_none=True)
    row["redirect_chain"] = []
    source = tmp_path / "input.csv"
    with source.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(row))
        writer.writeheader()
        writer.writerow({**row, "synthetic": "true", "redirect_chain": "[]"})
    assert list(iter_records(source, config)) == [IncomingRecord.model_validate(row)]
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert list(iter_records(source, config)) == [IncomingRecord.model_validate(row)]


def test_symlink_escape_is_rejected_before_open(config, tmp_path, monkeypatch):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"synthetic outside asset")
    root = tmp_path / "assets"
    root.mkdir()
    original_resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path: outside if path.name == "nested.png" else original_resolve(path),
    )
    record = incoming("", source_kind="screenshot", image_path="nested.png", artifact_id=uuid4())
    with pytest.raises(ProcessingError):
        read_image(record, root, config)


def test_record_count_and_csv_unknown_fields_are_rejected(config, tmp_path):
    data = config.model_dump()
    data["limits"].update(max_records=1, batch_size=1)
    small = DetectorConfig.model_validate(data)
    path = tmp_path / "too-many.jsonl"
    path.write_text(
        incoming().model_dump_json() + "\n" + incoming().model_dump_json() + "\n", encoding="utf-8"
    )
    with pytest.raises(ProcessingError, match="invalid_input"):
        list(iter_records(path, small))
    path = tmp_path / "truth.csv"
    path.write_text(
        "text,provenance,synthetic,label\nexample,synthetic,true,benign\n", encoding="utf-8"
    )
    with pytest.raises(ProcessingError, match="invalid_input"):
        list(iter_records(path, config))


def test_unicode_domain_evidence_uses_source_coordinates(config):
    prepared = preprocess_record(incoming("Адрес https://ҚАЗАҚ.test/path"), config)
    domain = next(entity for entity in prepared.entities if entity.kind == "domain")
    assert domain.evidence.quote == "ҚАЗАҚ.test"
    assert domain.value.startswith("xn--")


@pytest.mark.parametrize("text", ["promo code: BONUS42", "promokod BONUS42", "BONUS42 кодымен"])
def test_multilingual_promo_code_order_and_exact_evidence(config, text):
    result = preprocess_record(incoming(text), config)
    promo = next(entity for entity in result.entities if entity.kind == "promo_code")
    assert promo.value == "bonus42"
    assert promo.evidence.quote == "BONUS42"
