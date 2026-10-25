from uuid import uuid4

import pytest
from pydantic import ValidationError

from safenet.contracts import IncomingRecord, SyntheticNetwork
from safenet.features.investment import numeric_returns
from safenet.graph import relationships
from safenet.http_contracts import UploadRecord, fingerprint
from safenet.preprocessing import preprocess_record


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Вложи 5 000 тг — получи 10 000 тг", True),
        ("5000 теңге сал — 10000 теңге ал", True),
        ("Вложи 5000 — получи 5100", False),
        ("Вложи 0 — получи 10000", False),
        ("Новости: «вложи 5000 — получи 10000»", False),
        ("Не вложи 5000 — получи 10000", False),
    ],
)
def test_numeric_offer_with_context(config, text, expected):
    prepared = preprocess_record(
        IncomingRecord(provenance="synthetic", synthetic=True, text=text), config
    )
    assert bool(numeric_returns(prepared)) == expected


def test_synthetic_network_roles_and_bounded_audience(config):
    network = SyntheticNetwork(
        group_id=uuid4(), role="curator", channel_ids=(uuid4(),), audience_ids=(uuid4(),)
    )
    record = UploadRecord(
        provenance="synthetic", synthetic=True, text="Пример сообщения", network=network
    )
    prepared = preprocess_record(record.work().record, config)
    links = relationships(prepared, 1)
    assert {node.kind for node, _ in links.values()} == {"group", "channel", "audience"}
    assert {origin for _, origin in links.values()} == {
        "curator_metadata",
        "channel_metadata",
        "audience_metadata",
    }
    with pytest.raises(ValidationError):
        UploadRecord(provenance="deidentified", synthetic=False, text="Пример", network=network)
    with pytest.raises(ValidationError):
        SyntheticNetwork(audience_ids=tuple(uuid4() for _ in range(33)))


def test_legacy_pending_source_fingerprint_survives_new_optional_metadata():
    work = UploadRecord(provenance="synthetic", synthetic=True, text="Пример").work()
    legacy = work.record.model_dump(
        mode="json", exclude={"case_id", "artifact_id", "created_at", "network"}
    )
    legacy["image_sha256"] = None
    assert work.fingerprint == fingerprint(legacy)
