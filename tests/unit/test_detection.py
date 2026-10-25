import math

import pytest

from safenet import DISCLAIMER
from safenet.config import DetectorConfig
from safenet.contracts import AnalysisOutcome, IncomingRecord, JobStatus, Track
from safenet.features import casino, investment, lottery
from safenet.features.common import FeatureMatch
from safenet.pipeline import analyze
from safenet.preprocessing import load_prepared, preprocess_record, save_prepared
from safenet.scoring import risk_level, score_track


def prepare(text, config, **changes):
    return preprocess_record(
        IncomingRecord(provenance="synthetic", synthetic=True, text=text, **changes), config
    )


def results(text, config):
    outcome = analyze(prepare(text, config), config)
    assert outcome.status == JobStatus.COMPLETED
    return {item.track: item for item in outcome.result.tracks}


@pytest.mark.parametrize(
    "track,text,score",
    [
        (
            "lottery",
            "Оплати участие 5000 тг. Рандомайзер выберет победителя. Организатор удержит комиссию.",
            100,
        ),
        ("lottery", "Қатысу үшін 5000 теңге жарна. Жеңімпаз кездейсоқ таңдалады.", 85),
        ("lottery", "Жарна 5000 тенге. Жеңімпаз кездейсок таңдалады.", 85),
        ("lottery", "Vznos 5000 tg. Randomayzer vyberet pobeditelya.", 85),
        ("lottery", "В з н о с 5000 тг. 🎲 выберет победителя.", 85),
        (
            "casino",
            "Казино. Переходи по ссылке, пополни депозит. Мой выигрыш! Промокод BONUS42.",
            100,
        ),
        ("casino", "Казино. Сілтемеге өтіп тіркел. Депозит сал. Ұтқан ақша.", 90),
        ("casino", "Kazino. Perehodi po ssylke.", 75),
        ("casino", "К@зино. Жми ссылку.", 75),
        (
            "investment",
            "Гарантированный доход 30% в день. Пригласи друзей. "
            "Осталось 3 места. Перевод на личную карту.",
            100,
        ),
        ("investment", "Тәуекелсіз табыс. Досыңды шақыр. Тек 3 орын қалды.", 100),
        ("investment", "Garantirovannyy dohod. Priglasi druzey.", 90),
        ("investment", "Гарантированный доход 20% за сутки.", 45),
        ("investment", "Вложи 1000, получи 2000 завтра.", 45),
    ],
)
def test_positive_combinations_across_languages_and_surface_forms(config, track, text, score):
    result = results(text, config)[track]
    assert result.score == score
    assert result.level == ("high" if score >= 70 else "medium")


@pytest.mark.parametrize(
    "text",
    [
        "Участие без оплаты. Рандомайзер выберет победителя.",
        "Бесплатный конкурс. Случайный выбор.",
        "Тегін байқау. Қатысу тегін. Жеңімпаз кездейсоқ таңдалады.",
        "На концерт продаются билеты за 5000 тг. Жеребьёвка сувениров.",
        "Новости: казино рекламирует промокод, переходи и пополни депозит.",
        "Учебный разбор: «взнос, случайный выбор, комиссия». Это пример риска.",
        "На уроке обсуждаем: «гарантированный доход, пригласи друзей».",
        "Жадынама: кепілді табыс, досыңды шақыр — тәуекел мысалы.",
        "Благотворительный сбор на помощь, перевод добровольный, отчёт открыт.",
        "Реклама магазина: скидка за приглашение друга. Пригласи друзей.",
        "Свободный слот для записи на курс, регистрируйтесь сегодня.",
        "Доход не гарантирован. Пригласи друзей на открытый урок.",
        "Табыс кепілді емес. Оқу курсы.",
        "Игры на деньги не предлагают. Казино нет.",
        "Казино. Переходить не нужно, депозит не вносите.",
    ],
)
def test_hard_negative_and_negated_context_stays_low(config, text):
    assert all(result.score < 40 for result in results(text, config).values())


@pytest.mark.parametrize(
    "track,text,counter",
    [
        (
            "lottery",
            "Бесплатный подарок всем! Взнос за участие 5000 тг. Случайный выбор.",
            "lottery.free_participation",
        ),
        (
            "lottery",
            "Учебная заметка о рекламе. Но сейчас внеси взнос. Случайный выбор сегодня.",
            "lottery.reporting_context",
        ),
        (
            "casino",
            "Новости о рисках. Но переходи в казино, пополни депозит.",
            "casino.reporting_context",
        ),
        (
            "investment",
            "Учебный курс. Но гарантированный доход для тебя, пригласи друзей.",
            "investment.reporting_context",
        ),
        (
            "investment",
            "Доход не гарантирован по старому продукту. "
            "В новом гарантированный доход. Пригласи друзей.",
            "investment.risk_disclaimer",
        ),
        ("casino", "«Казино: переходи, пополни депозит»", "casino.reporting_context"),
        (
            "casino",
            "Образовательный курс: казино, переходи по ссылке, пополни депозит.",
            "casino.reporting_context",
        ),
        (
            "investment",
            "Учебный курс: гарантированный доход, пригласи друзей.",
            "investment.reporting_context",
        ),
    ],
)
def test_counter_words_do_not_globally_cancel_active_promotions(config, track, text, counter):
    result = results(text, config)[track]
    assert result.level == "high"
    assert counter not in {feature.feature_id for feature in result.features}


def test_independent_tracks_queue_max_and_repeated_features(config):
    text = "Казино. Жми ссылку. Гарантированный доход. Пригласи друзей."
    prepared = prepare(text, config)
    first = analyze(prepared, config).result
    second = analyze(prepared, config).result
    repeated = analyze(prepare(text * 4, config), config).result
    assert first.tracks == second.tracks
    assert [item.score for item in repeated.tracks] == [item.score for item in first.tracks]
    assert first.queue_score == 90
    assert first.queue_level == "high"
    assert {item.track for item in first.tracks if item.level == "high"} == {
        Track.CASINO,
        Track.INVESTMENT,
    }
    assert first.disclaimer == DISCLAIMER
    assert first.run.config_fingerprint == config.fingerprint
    assert first.preprocessing_fingerprint == prepared.preprocessing_fingerprint
    assert first.rule_version == "3"
    assert (
        AnalysisOutcome.model_validate_json(
            analyze(prepared, config).model_dump_json()
        ).result.tracks
        == first.tracks
    )
    for track in first.tracks:
        assert track.graph_adjustment == 0
        assert math.isclose(
            sum(item.contribution for item in track.features) + track.cap_adjustment, track.score
        )
        assert len(track.features) == len({item.feature_id for item in track.features})
        for feature in track.features:
            assert feature.description
            assert len(feature.evidence) <= 3
            for evidence in feature.evidence:
                assert evidence.source_id == prepared.content.case_id
                assert (
                    prepared.content.text[evidence.span.start : evidence.span.end] == evidence.quote
                )


def test_transliterated_evidence_uses_exact_sanitized_source(config):
    prepared = prepare("Vznos 5000 tg. Randomayzer vyberet pobeditelya.", config)
    matches = lottery.extract(prepared)
    assert (
        next(item for item in matches if item.feature_id == "lottery.payment").evidence[0].quote
        == "Vznos"
    )


@pytest.mark.parametrize(
    "score,level",
    [(0, "low"), (39.999, "low"), (40, "medium"), (69.999, "medium"), (70, "high"), (100, "high")],
)
def test_risk_threshold_boundaries(config, score, level):
    assert risk_level(score, config) == level


@pytest.mark.parametrize("score", [-1, 101, float("nan"), float("inf")])
def test_invalid_scores_are_rejected(config, score):
    with pytest.raises(ValueError):
        risk_level(score, config)


def test_signed_counters_and_upper_clipping_reconcile(config):
    low = results("Участие бесплатно. Случайный выбор.", config)["lottery"]
    assert {item.contribution for item in low.features} == {25, -40}
    assert low.score == 0 and low.cap_adjustment == 15
    high = results("Гарантированный доход. Пригласи друзей. Успей! Перевод.", config)["investment"]
    assert high.score == 100 and high.cap_adjustment == -10


def test_scorer_rejects_duplicate_cross_track_and_empty_evidence(config):
    matches = casino.extract(prepare("Казино. Жми ссылку.", config))
    with pytest.raises(ValueError, match="duplicate"):
        score_track(Track.CASINO, matches + matches[:1], config)
    with pytest.raises(ValueError, match="track"):
        score_track(Track.LOTTERY, matches, config)
    with pytest.raises(ValueError, match="missing evidence"):
        score_track(Track.CASINO, (FeatureMatch("casino.gambling", ()),), config)


def test_weight_changes_reuse_preparation_but_store_new_scoring_context(config, tmp_path):
    prepared = prepare("Взнос 5000 тг. Случайный выбор.", config)
    directory = save_prepared(prepared, tmp_path)
    data = config.model_dump()
    data["features"][0]["weight"] = 10.0
    changed = DetectorConfig.model_validate(data)
    reused = load_prepared(directory, changed)
    previous = analyze(prepared, config).result
    current = analyze(reused, changed, version=2).result
    assert previous.tracks[0].score == 85 and current.tracks[0].score == 65
    assert previous.run.config_fingerprint != current.run.config_fingerprint
    assert current.run.config_fingerprint == changed.fingerprint
    assert current.preprocessing_fingerprint == previous.preprocessing_fingerprint
    assert current.version == 2
    assert previous.tracks[0].features[0].contribution == 30


def test_stale_preprocessing_is_incomplete_and_unknown_language_is_analyzed(config):
    prepared = prepare("xyz 🎲", config)
    result = analyze(prepared, config).result
    assert len(result.tracks) == 3
    assert result.tracks[0].score == 25
    for stale in (
        prepared.model_copy(update={"preprocessing_fingerprint": "0" * 64}),
        prepared.model_copy(
            update={"run": prepared.run.model_copy(update={"code_version": "old"})}
        ),
    ):
        outcome = analyze(stale, config)
        assert outcome.status == "incomplete" and outcome.result is None


def test_features_do_not_depend_on_source_kind_or_entity_identity(config):
    text = "Казино. Жми ссылку."
    regular = prepare(text, config)
    transcript = prepare(text, config, source_kind="transcript")
    assert [item.score for item in analyze(regular, config).result.tracks] == [
        item.score for item in analyze(transcript, config).result.tracks
    ]
    assert not investment.extract(prepare("xyz", config))


def test_distant_keywords_do_not_form_a_combination(config):
    filler = "нейтральное описание " * 30
    assert results("Взнос. " + filler + ". Случайный выбор.", config)["lottery"].score == 55
    assert results("Казино. " + filler + ". Жми ссылку.", config)["casino"].score == 30
    assert results("Гарантированный. " + filler + ". Доход.", config)["investment"].score == 0
