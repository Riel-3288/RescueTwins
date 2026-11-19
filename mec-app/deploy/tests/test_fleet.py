"""oneM2M fleet layer against real local ACME MN-CSEs (uses the shared `live` fixture)."""
import time

import pytest

from rescuetwin.fleet import Fleet
from rescuetwin.onem2m import Cse, CseError


@pytest.fixture(scope="module")
def fleet(live):
    cfg = dict(live, fleet={"readers": ["CRescueTwinWeb"]})
    value = Fleet(cfg)
    value.start({"agent-01": "A", "agent-02": "A", "agent-05": "B", "robot-01": "A"})
    yield value
    value.close()


def position(zone, x):
    return {"x_m": x, "y_m": 9.0, "zone_id": zone, "source": "simulation", "observed_at": "2026-11-16T00:00:00Z"}


def test_discovery_by_label_and_type(fleet):
    assert sorted(fleet.discover("A", ["drone@zone-A"], 2)) == ["cse-mn-a/agent-01", "cse-mn-a/agent-02"]
    assert fleet.discover("A", ["robot@zone-A"], 2) == ["cse-mn-a/robot-01"]
    assert fleet.discover("B", ["drone@zone-B"], 2) == ["cse-mn-b/agent-05"]
    assert fleet.discover("A", ["agent-group"], 9) == ["cse-mn-a/rescue-mec/grp-agents"]


def test_acp_roles_grant_only_what_each_role_needs(fleet, live):
    fleet.report("agent-02", position("A", 20.0))
    twin, stranger = Cse(live["cse"]["A"], "CRescueTwinWeb"), Cse(live["cse"]["A"], "CStranger")
    mec, other_agent = fleet.client("A", "CRescueMec"), fleet.client("A", "CAgent01")
    assert twin.latest("/agent-02/position")["agent_id"] == "agent-02"      # twin may read
    with pytest.raises(CseError) as denied:
        twin.cin("/agent-02/cmd", {"operation": "recall"})                  # twin may not command
    assert denied.value.rsc == "4103"
    with pytest.raises(CseError):
        stranger.latest("/agent-02/position")                               # unknown originator: nothing
    with pytest.raises(CseError):
        other_agent.cin("/agent-02/position", {"spoof": True})              # agents cannot write each other
    with pytest.raises(CseError):
        mec.call("DELETE", "/agent-02/position")                            # MEC app cannot delete agent data
    assert fleet.discover("A", ["drone@zone-A"], 2, "CStranger") == []


def test_group_recall_reaches_every_agent_and_notifies(fleet):
    before = len(fleet.notifications.for_agent("agent-02"))
    result = fleet.recall("A", {"operation": "recall", "reason": "test"})
    assert result["members"] == result["created"] == 3
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(fleet.notifications.for_agent("agent-02")) == before:
        time.sleep(.05)
    assert len(fleet.notifications.for_agent("agent-02")) == before + 1
    assert fleet.client("A", "CAgent02").latest("/agent-02/cmd")["command_id"] == result["command_id"]
    assert fleet.client("A", "CAgent02").call("GET", "/agent-02/cmd")["m2m:cnt"]["mia"] == 600


def test_sdt_flexcontainers_are_discoverable(fleet):
    reading = {"sensor_type": "temperature", "value": 28.6, "quality": "valid", "source": "physical"}
    assert fleet.node_state("A", "node-01", reading)["cnd"] == "org.onem2m.common.moduleclass.temperature"
    assert fleet.node_state("A", "node-01", dict(reading, value=61.5))["curT0"] == 61.5
    smoke = fleet.node_state("A", "node-01", dict(reading, sensor_type="smoke_raw", value=2600))
    assert smoke["alarm"] is True and smoke["crv"] == 2600
    assert fleet.node_state("A", "node-01", dict(reading, quality="invalid")) is None
    found = fleet.discover("A", ["node-state"], 28, "CRescueTwinWeb")
    assert sorted(found) == ["cse-mn-a/rescue-mec/node-01-smokeSensor", "cse-mn-a/rescue-mec/node-01-temperature"]


def test_location_triggered_handover_restores_context_on_target(fleet):
    fleet.report("agent-01", position("A", 70.0))
    fleet.report("agent-01", position("B", 84.0))                # geofence now says zone B
    report = fleet.handovers[-1]
    assert report["ok"] and (report["from_zone"], report["to_zone"]) == ("A", "B")
    assert report["lost_reports"] == 0 and report["interruption_ms"] >= 0
    assert fleet.serving["agent-01"] == "B"
    agent_b = fleet.client("B", "CAgent01")
    assert agent_b.latest("/agent-01/position")["x_m"] == 84.0   # communication resumed on the target
    assert agent_b.call("GET", "/agent-01/cmd/sub-cmd")["m2m:sub"]["nu"]  # subscription rebuilt
    assert agent_b.call("GET", "/agent-01")["m2m:ae"]["acpi"]             # security context rebuilt
    # Group membership moved: zone A no longer commands the agent, zone B does.
    assert fleet.recall("A", {"operation": "recall"})["members"] == 2
    before = len(fleet.notifications.for_agent("agent-01"))
    assert fleet.recall("B", {"operation": "recall"})["members"] == 2
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(fleet.notifications.for_agent("agent-01")) == before:
        time.sleep(.05)
    assert len(fleet.notifications.for_agent("agent-01")) == before + 1
    assert fleet.client("B", "CRescueMec").latest("/rescue-mec/handover")["agent_id"] == "agent-01"
    with pytest.raises(CseError):
        fleet.client("A", "CAgent01").call("GET", "/agent-01/cmd/sub-cmd")  # old subscription removed


def test_reports_made_during_a_handover_are_buffered_not_lost(fleet):
    fleet.in_handover.add("agent-05")
    assert fleet.report("agent-05", position("B", 90.0)) is None
    fleet.in_handover.discard("agent-05")
    report = fleet.handover("agent-05", "A", trigger="test")
    assert report["ok"] and report["buffered_reports"] == 1 and report["lost_reports"] == 0
    assert fleet.client("A", "CAgent05").latest("/agent-05/position")["x_m"] == 90.0
