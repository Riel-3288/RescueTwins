import json
from pathlib import Path

import pytest

from rescuetwin.core import Store, Geofence, validate_reading, load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    return load_config(ROOT / "config.example.json")


@pytest.fixture
def sample():
    return json.loads((ROOT / "sample-temperature.json").read_text())


@pytest.mark.parametrize("change", [
    {"zone_id": "C"}, {"zone_id": "B"}, {"device_id": "../../admin"},
    {"unit": "Celsius"}, {"value": True}, {"value": float("nan")},
    {"source": "unknown"}, {"quality": "good"},
    {"observed_at": "2026-10-10T08:30:00"}, {"observed_at": "2026-10-10T08:30:00+08:00"},
])
def test_rejects_bad_sensor_contract(cfg, sample, change):
    with pytest.raises(ValueError):
        validate_reading(dict(sample, **change), cfg["devices"])


def test_topic_must_match_identity(cfg, sample):
    with pytest.raises(ValueError):
        validate_reading(sample, cfg["devices"], "rescuetwin/A/node-02/temperature")


def test_presence_bool_and_invalid_null(cfg, sample):
    validate_reading(dict(sample, sensor_type="presence", unit="boolean", value=False), cfg["devices"])
    validate_reading(dict(sample, value=None, quality="invalid"), cfg["devices"])
    with pytest.raises(ValueError):
        validate_reading(dict(sample, sensor_type="presence", unit="boolean", value=1), cfg["devices"])


def test_outbox_survives_restart_and_duplicate(cfg, sample, tmp_path):
    path = tmp_path / "outbox.sqlite"
    store = Store(path)
    key = store.enqueue(sample)
    store.enqueue(sample)
    store.db.close()
    resumed = Store(path)
    assert resumed.counts() == {"pending": 1}
    assert resumed.pending()[1] == sample
    resumed.delivered(key)
    resumed.enqueue(sample)
    assert resumed.counts() == {"delivered": 1}


def test_geofence_hysteresis_stale_and_confirmation(cfg):
    fence = Geofence(cfg["factory"])
    state, event = fence.update(None, 40, 30, "2026-10-15T01:00:00Z")
    assert state["zone"] == "A"
    state, event = fence.update(state, 51, 30, "2026-10-15T01:00:01Z")
    assert event is None
    state, event = fence.update(state, 60, 30, "2026-10-15T01:00:02Z")
    assert state["zone"] == "A"
    state, event = fence.update(state, 61, 30, "2026-10-15T01:00:03Z")
    assert state["zone"] == "B" and event["from_zone"] == "A"
    unchanged, event = fence.update(state, 30, 30, "2026-10-15T01:00:02Z")
    assert unchanged == state and event is None


def test_production_config_refuses_plaintext(cfg, tmp_path):
    cfg["require_tls"] = True
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):
        load_config(path)
