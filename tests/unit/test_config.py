import copy
import tomllib

import pytest
from pydantic import ValidationError

from safenet.config import DetectorConfig, load_config


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("thresholds", "medium", 70.0),
        ("thresholds", "high", 101.0),
        ("thresholds", "high", float("nan")),
        ("thresholds", "medium", True),
        ("limits", "batch_size", 0),
        ("limits", "batch_size", 1001),
        ("limits", "max_image_pixels", True),
        ("ocr", "concurrency", -1),
        ("ocr", "languages", "eng"),
        ("graph", "max_adjustment", float("inf")),
        ("graph", "max_nodes", 0),
        ("graph", "max_edges", True),
        ("graph", "display_max_nodes", 100001),
        ("graph", "display_max_edges", 200001),
    ],
)
def test_invalid_configuration_is_rejected(config, section, key, value):
    data = config.model_dump()
    data[section][key] = value
    with pytest.raises(ValidationError):
        DetectorConfig.model_validate(data)


def test_duplicate_features_and_unknown_settings_rejected(config):
    data = config.model_dump()
    feature = {"feature_id": "lottery.payment", "track": "lottery", "weight": 12.0}
    data["features"] = [feature, feature.copy()]
    with pytest.raises(ValidationError, match="duplicate feature"):
        DetectorConfig.model_validate(data)
    data = config.model_dump()
    data["fetch_urls"] = True
    with pytest.raises(ValidationError):
        DetectorConfig.model_validate(data)


def test_fingerprint_is_order_independent_and_changes_with_settings(config):
    data = config.model_dump()
    reordered = dict(reversed(list(data.items())))
    assert DetectorConfig.model_validate(reordered).fingerprint == config.fingerprint
    changed = copy.deepcopy(data)
    changed["thresholds"]["medium"] = 41.0
    assert DetectorConfig.model_validate(changed).fingerprint != config.fingerprint


def test_malformed_toml_and_missing_config_fail(tmp_path):
    path = tmp_path / "invalid.toml"
    path.write_text("[broken", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(path)
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("change", ["unknown", "missing", "track", "sign", "zero"])
def test_feature_catalog_and_direction_are_enforced(config, change):
    data = config.model_dump()
    if change == "missing":
        data["features"] = data["features"][1:]
    elif change == "unknown":
        data["features"][0]["feature_id"] = "lottery.unknown"
    elif change == "track":
        data["features"][0]["track"] = "casino"
    else:
        data["features"][0]["weight"] = -1.0 if change == "sign" else 0.0
    with pytest.raises(ValidationError):
        DetectorConfig.model_validate(data)
