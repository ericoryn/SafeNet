from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from safenet.config import DetectorConfig
from safenet.contracts import AnalysisOutcome, AnalysisResult, GraphInfluence, IncomingRecord, Track
from safenet.graph import GraphIndex, node_id, relationships, template_key
from safenet.pipeline import analyze
from safenet.preprocessing import preprocess_record

HIGH = "Гарантированный доход. Пригласи друзей."
MEDIUM = "Гарантированный доход. Успей сегодня."


def prepared(config, text=MEDIUM, **changes):
    data = {"provenance": "synthetic", "synthetic": True, "text": text, **changes}
    return preprocess_record(IncomingRecord.model_validate(data), config)


def process(index, content):
    outcome = analyze(content, index.config, graph=index, scope=index.snapshot.scope)
    assert outcome.status == "completed"
    return outcome.result


def investment(result):
    return next(track for track in result.tracks if track.track == Track.INVESTMENT)


@pytest.mark.parametrize(
    "relationship", ["account_metadata", "account_text", "card", "domain", "promo", "redirect"]
)
def test_indexed_links_and_explained_bounded_influence(config, relationship):
    index = GraphIndex(config, "demo")
    account, card = uuid4(), uuid4()
    text_suffix, metadata = "", {}
    if relationship == "account_metadata":
        metadata = {"synthetic_account_id": account}
    elif relationship == "account_text":
        text_suffix = f" account-{account}"
    elif relationship == "card":
        text_suffix = f" card-{card}"
    elif relationship == "domain":
        text_suffix = " https://example.test/offer"
    elif relationship == "promo":
        text_suffix = " Промокод DEMO42."
    else:
        metadata = {"redirect_chain": ("https://short.test/r?ref=PRIVATE", "https://final.test/")}
    seed = prepared(config, HIGH + text_suffix, **metadata)
    first = process(index, seed)
    prior = index.snapshot
    assert investment(first).graph_adjustment == 0
    target = prepared(config, MEDIUM + text_suffix, **metadata)
    result = process(index, target)
    track = investment(result)
    assert track.score == 70 and track.level == "high"
    assert track.graph_adjustment == 15
    assert track.graph_evidence.seed_case_id == seed.content.case_id
    assert track.graph_evidence.seed_base_score == 90
    assert track.graph_evidence.snapshot_id == prior.snapshot_id
    assert track.graph_evidence.previous_cases == track.graph_evidence.high_base_cases == 1
    assert result.graph_snapshot == prior
    assert index.snapshot.largest_component_cases == 2
    assert index.snapshot.component_count == 1
    assert AnalysisResult.model_validate_json(result.model_dump_json()) == result
    graph = index.export()
    assert all(
        edge["source"].startswith("case:") and not edge["target"].startswith("case:")
        for edge in graph["edges"]
    )
    assert "PRIVATE" not in str(graph)


def test_account_metadata_and_explicit_synthetic_token_share_one_node(config):
    account = uuid4()
    content = prepared(config, f"account-{account}", synthetic_account_id=account)
    links = relationships(content, 1)
    assert len(links) == 1
    assert next(iter(links.values()))[0].kind == "account"


def test_template_skeleton_is_exact_content_derived_and_ignores_variable_fields(config):
    text = (
        "Сегодня публикуем подробные условия участия, правила выбора победителя "
        "и сроки вручения подарков. Взнос {amount} тг, случайный выбор. {url}"
    )
    first = prepared(config, text.format(amount=5000, url="https://a.test/"))
    second = prepared(config, text.format(amount=9000, url="https://b.test/"))
    assert template_key(first) == template_key(second) is not None
    assert template_key(prepared(config, "Казино. Жми ссылку.")) is None
    assert template_key(
        prepared(
            config,
            text.replace("подарков", "документов").format(amount=5000, url="https://a.test/"),
        )
    ) != template_key(first)
    index = GraphIndex(config, "demo")
    process(index, first)
    result = process(index, second)
    track = next(track for track in result.tracks if track.track == "lottery")
    assert track.graph_evidence.kind == "template"
    assert track.graph_adjustment == 15


def test_no_recursive_amplification_or_transitive_component_seeding(config):
    index = GraphIndex(config, "demo")
    a = prepared(config, HIGH + " https://a.test/")
    b = prepared(config, MEDIUM + " https://a.test/ https://b.test/")
    c = prepared(config, MEDIUM + " https://b.test/")
    process(index, a)
    boosted = process(index, b)
    assert investment(boosted).level == "high"
    assert index.base_scores[b.content.case_id][Track.INVESTMENT] == 55
    unchanged = process(index, c)
    assert investment(unchanged).graph_adjustment == 0
    assert investment(unchanged).score == 55
    assert index.snapshot.largest_component_cases == 3


def test_common_entity_is_diluted_and_multiple_links_are_not_added(config):
    index = GraphIndex(config, "demo")
    process(index, prepared(config, HIGH + " https://common.test/"))
    for _ in range(3):
        process(index, prepared(config, "Открытая справочная информация. https://common.test/"))
    result = process(index, prepared(config, MEDIUM + " https://common.test/"))
    assert investment(result).graph_adjustment == 3.75
    assert investment(result).graph_evidence.previous_cases == 4
    assert investment(result).graph_evidence.high_base_cases == 1
    another = GraphIndex(config, "demo")
    account = uuid4()
    for text in (HIGH, MEDIUM):
        result = process(
            another,
            prepared(
                config, text + " https://a.test/ Промокод DEMO42.", synthetic_account_id=account
            ),
        )
    assert investment(result).graph_adjustment == 15
    assert another.snapshot.edge_count == 6


def test_low_base_and_cross_track_links_do_not_receive_influence(config):
    index = GraphIndex(config, "demo")
    account = uuid4()
    process(index, prepared(config, HIGH, synthetic_account_id=account))
    innocent = process(
        index,
        prepared(
            config, "Бесплатный конкурс. Доход не гарантирован.", synthetic_account_id=account
        ),
    )
    assert all(track.graph_adjustment == 0 for track in innocent.tracks)
    assert all(track.score == 0 for track in innocent.tracks)
    other = GraphIndex(config, "demo")
    process(other, prepared(config, "Казино. Жми ссылку.", synthetic_account_id=account))
    unrelated_track = process(other, prepared(config, MEDIUM, synthetic_account_id=account))
    assert investment(unrelated_track).score == 55


def test_masked_suffixes_amounts_and_untrusted_account_surrogates_are_not_keys(config):
    index = GraphIndex(config, "demo")
    for number in ("0000 1111 2222 3333", "9999 8888 7777 3333"):
        process(index, prepared(config, "Перевод на карту " + number + "; 5000 тг."))
    assert index.snapshot.component_count == 2
    assert all(node.kind == "case" for node in index.nodes.values())
    content = prepared(
        config,
        "card-" + str(uuid4()) + " account-" + str(uuid4()),
        provenance="deidentified",
        synthetic=False,
    )
    assert relationships(content, 1) == {}


def test_snapshot_and_neighborhood_do_not_expose_future_cases(config):
    index = GraphIndex(config, "train")
    first = prepared(config, HIGH + " https://a.test/", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    process(index, first)
    before = index.neighborhood(first.content.case_id)
    second = prepared(
        config,
        MEDIUM + " https://a.test/ https://future.test/",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    process(index, second)
    assert index.neighborhood(first.content.case_id) == before
    assert before.snapshot.sequence == 1 and before.snapshot.largest_component_cases == 1
    latest = index.neighborhood(first.content.case_id, sequence=2)
    assert len(latest.nodes) == 3 and len(latest.edges) == 2
    assert not any(node.value == "future.test" for node in latest.nodes)
    with pytest.raises(ValueError):
        index.neighborhood(second.content.case_id, sequence=1)
    with pytest.raises(ValueError):
        index.neighborhood(first.content.case_id, sequence=True)


def test_observation_order_handles_late_publications_without_looking_ahead(config):
    index = GraphIndex(config, "demo")
    now = datetime.now(UTC)
    seed = prepared(config, HIGH + " https://a.test/", created_at=now)
    early = prepared(config, MEDIUM + " https://a.test/", created_at=now - timedelta(days=2))
    process(index, seed)
    result = process(index, early)
    assert investment(result).graph_adjustment == 15
    assert result.graph_snapshot.sequence == 1


def test_view_truncation_is_explicit_and_never_changes_scores_or_graph_stats(config):
    index = GraphIndex(config, "demo")
    account = uuid4()
    process(index, prepared(config, HIGH, synthetic_account_id=account))
    for _ in range(10):
        target = prepared(config, MEDIUM, synthetic_account_id=account)
        result = process(index, target)
    full = index.export()
    view = index.neighborhood(target.content.case_id, max_nodes=2, max_edges=1)
    assert len(view.nodes) == 2 and len(view.edges) == 1
    assert view.truncated and view.total_edges == 11
    assert index.export() == full
    assert investment(result).graph_adjustment == 1.5
    for bounds in ({"max_nodes": 0}, {"max_nodes": 101}, {"max_edges": 201}, {"max_edges": True}):
        with pytest.raises(ValueError):
            index.neighborhood(target.content.case_id, **bounds)


def test_partitions_and_configurations_cannot_share_graph_context(config):
    source = prepared(config, HIGH + " https://a.test/")
    target = prepared(config, MEDIUM + " https://a.test/")
    train, validation = GraphIndex(config, "train"), GraphIndex(config, "validation")
    process(train, source)
    assert investment(process(validation, target)).graph_adjustment == 0
    assert train.snapshot.snapshot_id != validation.snapshot.snapshot_id
    before = train.export()
    for scope in (None, "validation", "test"):
        outcome = analyze(target, config, graph=train, scope=scope)
        assert outcome.status == "incomplete" and outcome.result is None
    changed = config.model_dump()
    changed["thresholds"]["high"] = 71.0
    outcome = analyze(target, DetectorConfig.model_validate(changed), graph=train, scope="train")
    assert outcome.status == "incomplete"
    assert train.export() == before
    with pytest.raises(KeyError):
        validation.neighborhood(source.content.case_id)


def test_duplicates_adjusted_seeds_and_capacity_fail_without_partial_mutation(config):
    data = config.model_dump()
    data["graph"].update(max_edges=1, display_max_edges=1)
    limited = DetectorConfig.model_validate(data)
    index = GraphIndex(limited, "demo")
    first = prepared(limited, HIGH + " https://a.test/")
    result = process(index, first)
    snapshot = index.export()
    for content in (first, prepared(limited, MEDIUM + " https://a.test/")):
        outcome = analyze(content, limited, graph=index, scope="demo")
        assert outcome.status == "incomplete" and outcome.result is None
        assert index.export() == snapshot
    with pytest.raises(ValueError):
        index.process(first, result, scope="demo")
    assert AnalysisOutcome.model_validate_json(analyze(first, limited).model_dump_json()).result


def test_graph_results_are_deterministic_for_same_order_and_scope(config):
    records = [prepared(config, text + " https://a.test/") for text in (HIGH, MEDIUM, MEDIUM)]
    outputs = []
    for _ in range(2):
        index = GraphIndex(config, "demo")
        results = [process(index, record) for record in records]
        outputs.append(([result.tracks for result in results], index.export()))
    assert outputs[0] == outputs[1]


def test_graph_evidence_counts_and_adjustment_are_validated(config):
    index = GraphIndex(config, "demo")
    process(index, prepared(config, HIGH + " https://a.test/"))
    result = process(index, prepared(config, MEDIUM + " https://a.test/"))
    data = result.model_dump()
    track = next(track for track in data["tracks"] if track["track"] == "investment")
    track["graph_evidence"]["high_base_cases"] = 2
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)
    bad = investment(result).graph_evidence.model_dump()
    bad["seed_case_id"] = result.case_id
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(
            {
                **result.model_dump(),
                "tracks": [
                    {**item.model_dump(), "graph_evidence": GraphInfluence.model_validate(bad)}
                    if item.track == "investment"
                    else item
                    for item in result.tracks
                ],
            }
        )
    assert node_id("domain", "x.test") != node_id("promo_code", "x.test")


def test_common_hub_has_linear_edges_and_bounded_display(config):
    index = GraphIndex(config, "demo")
    for i in range(1000):
        content = prepared(config, (HIGH if i == 0 else MEDIUM) + " https://shared.test/")
        result = process(index, content)
    assert index.snapshot.node_count == 1001
    assert index.snapshot.edge_count == 1000
    assert index.snapshot.component_count == 1
    assert index.snapshot.largest_component_cases == 1000
    assert 0 < investment(result).graph_adjustment <= config.graph.max_adjustment
    view = index.neighborhood(content.content.case_id)
    assert len(view.nodes) <= config.graph.display_max_nodes
    assert len(view.edges) <= config.graph.display_max_edges
    assert view.truncated and view.total_edges == 1000


@pytest.mark.parametrize("capacity", ["nodes", "records"])
def test_graph_node_and_record_capacity_are_explicit(config, capacity):
    data = config.model_dump()
    if capacity == "nodes":
        data["graph"].update(max_nodes=2, display_max_nodes=2)
    else:
        data["limits"].update(max_records=1, batch_size=1)
    small = DetectorConfig.model_validate(data)
    index = GraphIndex(small, "demo")
    process(index, prepared(small, HIGH + " https://a.test/"))
    before = index.export()
    result = analyze(prepared(small, MEDIUM + " https://a.test/"), small, graph=index, scope="demo")
    assert result.status == "incomplete" and result.result is None
    assert index.export() == before


def test_zero_graph_cap_and_empty_relationships_remain_explicit_snapshots(config):
    data = config.model_dump()
    data["graph"]["max_adjustment"] = 0.0
    disabled = DetectorConfig.model_validate(data)
    index = GraphIndex(disabled, "demo")
    for text in (HIGH, MEDIUM):
        result = process(index, prepared(disabled, text + " https://a.test/"))
    assert investment(result).graph_adjustment == 0
    isolated = prepared(disabled, "Справочная информация.")
    process(index, isolated)
    view = index.neighborhood(isolated.content.case_id, max_nodes=1, max_edges=1)
    assert len(view.nodes) == 1 and not view.edges and not view.truncated


def test_reanalysis_excludes_own_votes_and_does_not_reindex_case(config):
    index = GraphIndex(config, "demo")
    content = prepared(config, MEDIUM + " https://shared.test/")
    base = analyze(content, config).result
    process(index, content)
    first = index.preview(content, base, scope="demo")
    assert investment(first).graph_adjustment == 0
    peer = prepared(config, HIGH + " https://shared.test/")
    process(index, peer)
    snapshot = index.export()
    reanalysis = index.preview(content, base, scope="demo")
    assert investment(reanalysis).score == 70
    assert investment(reanalysis).graph_evidence.previous_cases == 1
    assert investment(reanalysis).graph_evidence.seed_case_id == peer.content.case_id
    assert index.export() == snapshot
    own_high = analyze(peer, config).result
    assert investment(index.preview(peer, own_high, scope="demo")).graph_adjustment == 0
