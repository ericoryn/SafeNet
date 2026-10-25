import hashlib
import json

import pytest

from safenet.contracts import AnalysisResult, IncomingRecord
from safenet.pipeline import analyze
from safenet.preprocessing import preprocess_record


def casino_result(text, config):
    prepared = preprocess_record(
        IncomingRecord(provenance="synthetic", synthetic=True, text=text), config
    )
    result = analyze(prepared, config).result
    return prepared, result, next(track for track in result.tracks if track.track == "casino")


@pytest.mark.parametrize(
    "text",
    [
        "Казино-партнёр платит за пополнение. Присоединяйся к игрокам.",
        "Канал ведёт к игре на деньги. Zhmi i aktiviruy promokod.",
        "Kanal vedet k igre na dengi. Zhmi i aktiviruy promokod.",
        "Серіктес казино: шотты толтыр, ойыншыларға қосыл.",
        "Seriktes kazino: shotty toltyr, oyynshylargha qosyl.",
        "Strimde oyyn slottaryn aynaldyramyn. Zholdama siltemem, shotty toltyr.",
        "В слотах джекпот. Присоединяйся по ссылке.",
        "Казино: бесплатный вход, жми и пополни депозит.",
        "Казино без реальных денег. Но пополни депозит для платного турнира.",
        "Novosti o riskah. Но zhmi v kazino, popolni deposit.",
    ],
)
def test_casino_active_promotion_preserves_source_quotes(config, text):
    prepared, _, track = casino_result(text, config)
    assert track.score >= 55
    assert "casino.gambling_promotion" in {f.feature_id for f in track.features}
    for feature in track.features:
        for evidence in feature.evidence:
            assert evidence.quote == prepared.content.text[evidence.span.start : evidence.span.end]


@pytest.mark.parametrize(
    "text",
    [
        "Novosti: kazino reklamiruet promokod, zhmi i popolni deposit.",
        "Zhangalyq: seriktes kazino, zholdama silteme, shotty toltyr.",
        "Kazino net. Perehodit ne nuzhno, deposit ne trebuetsya.",
        "Казино без реальных денег: жми, регистрируйся и играй.",
        "Kazino bez realnyh deneg: zhmi i registriruysya.",
        "Казино: ақша салмай ойна. Тіркел.",
        "Слот для записи на курс. Партнёр приглашает зарегистрироваться.",
        "Банк-партнёр: пополнение депозита и бонус клиенту.",
        "Редакция сообщает: казино-партнёр платит за пополнение.",
        "Казино. Не пополняй депозит, не присоединяйся.",
    ],
)
def test_casino_benign_context_stays_below_review_threshold(config, text):
    _, _, track = casino_result(text, config)
    assert track.score < 40
    assert "casino.gambling_promotion" not in {f.feature_id for f in track.features}


def test_rule_revision_separates_graph_context_but_reads_old_results(config):
    _, result, _ = casino_result("Казино. Жми ссылку.", config)
    legacy_payload = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    assert config.fingerprint != hashlib.sha256(legacy_payload.encode()).hexdigest()
    assert result.rule_version == "3"
    for version in ("1", "2"):
        old = result.model_dump(mode="json")
        old["rule_version"] = version
        restored = AnalysisResult.model_validate(old)
        assert restored.rule_version == version
        assert restored.tracks == result.tracks
