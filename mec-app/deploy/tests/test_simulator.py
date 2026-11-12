import json
import threading
import time

import pytest
import requests

from rescuetwin.core import FIELDS, UNITS, validate_reading
from rescuetwin.demo import Demo, make_server
from rescuetwin.mqtt import Bridge, publish
from rescuetwin.onem2m import Cse
from rescuetwin.simulator import Factory


def test_spatial_hazard_diffuses_and_wall_is_impermeable():
    model = Factory()
    model.walls = {(25, y) for y in range(model.height)}
    model.ignite(24, 14)
    model.paused = False
    for _ in range(40):
        model.step()
    assert model.heat[model.index(23, 14)] > 30
    assert model.smoke[model.index(23, 14)] > 0
    assert model.heat[model.index(26, 14)] == 28.6
    assert model.smoke[model.index(26, 14)] == 0
    assert all(28.6 <= t <= 450 for t in model.heat)
    assert all(0 <= s <= 1 for s in model.smoke)
    assert model.heat[model.index(3, 2)] < model.heat[model.index(23, 14)]


def test_routing_avoids_fire_obstacles_and_detects_unreachable_target():
    model = Factory()
    model.ignite(9, 8)
    path = model.route((4, 8), (13, 8))
    assert path[0] == (4, 8) and path[-1] == (13, 8)
    assert (9, 8) not in path
    assert not any(p in model.walls or model.unsafe(*p) for p in path)
    model.walls.update({(12, 8), (14, 8), (13, 7), (13, 9)})
    assert model.route((4, 8), (13, 8)) == []


def test_zone_confirmation_pause_reset_and_contract():
    model = Factory()
    model.agents = [dict(model.agents[0], x=model.zone_x-3, y=14, goal=[model.zone_x+5, 14], _fence=None)]
    model.command({"action":"goal","agent_id":model.agents[0]["id"],"x":model.zone_x+5,"y":14})
    model.command({"action": "resume"})
    events = []
    for _ in range(24):
        events.extend(model.step())
    assert len(events) == 1 and events[0]["from_zone"] == "A" and events[0]["to_zone"] == "B"
    model.command({"action": "pause"})
    before = model.elapsed
    model.step()
    assert model.elapsed == before
    devices = {name: {"zones": ["A", "B"]} for name in model.sensor_positions}
    rows = model.readings()
    assert len(rows) == 18
    assert all(set(row) == FIELDS and row["source"] == "simulation" for row in rows)
    for row in rows:
        validate_reading(row, devices)
    with pytest.raises(ValueError):
        model.command({"action": "ignite", "x": True, "y": 8})
    model.command({"action": "reset"})
    assert model.paused and not model.fires and model.elapsed == 0


def test_seed_reproduces_scenario():
    a, b = Factory(), Factory()
    for model in (a, b):
        model.command({"action": "demo"})
        for _ in range(48):
            model.step()
    assert a.heat == b.heat and a.smoke == b.smoke and a.fires == b.fires


def test_goal_change_clears_old_route_and_identifies_agent_event():
    model = Factory()
    agent = model.agents[0]
    agent["path"] = model.route((agent["x"], agent["y"]), tuple(agent["goal"]))
    assert agent["path"]
    model.command({"action": "goal", "agent_id": agent["id"], "x": 8, "y": 10})
    assert agent["goal"] == [8, 10] and agent["path"] == []
    assert agent["status"] == "ready" and model.paused
    assert model.events[0]["agent_id"] == agent["id"]
    model.command({"action": "resume"})
    for _ in range(3):
        model.step()
    assert agent["path"][-1] == (8, 10)


def test_dashboard_real_mqtt_cse_external_input_and_http_controls(live):
    bridge = Bridge(live)
    worker = threading.Thread(target=bridge.run, daemon=True)
    worker.start()
    demo = Demo(live, bridge)
    server = make_server(demo, 0)
    http_worker = threading.Thread(target=server.serve_forever, daemon=True)
    url = f"http://127.0.0.1:{server.server_port}"
    http_worker.start()
    try:
        assert bridge.ready.wait(5)
        demo.start()
        assert demo.mqtt_ready.wait(5)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            snapshot = demo.snapshot()
            if snapshot["mqtt"]["received"] >= 18 and snapshot["cse"]["counts"].get("delivered", 0) >= 18:
                break
            time.sleep(0.1)
        assert snapshot["mqtt"]["received"] >= 18
        assert snapshot["cse"]["counts"]["delivered"] >= 18
        assert Cse(live["cse"]["A"], "CNode01").latest("/node-01/temperature")["source"] == "simulation"
        assert Cse(live["cse"]["B"], "CNode03").latest("/node-03/temperature")["zone_id"] == "B"
        value = demo.factory.readings()[0]
        value.update(source="physical", value=42.5)
        publish(live["mqtt"], value, live["devices"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(r["source"] == "physical" for r in demo.snapshot()["readings"]):
            time.sleep(0.1)
        assert any(r["source"] == "physical" and r["value"] == 42.5 for r in demo.snapshot()["readings"])
        assert requests.get(url + "/").status_code == 200
        assert requests.get(url + "/api/state").json()["mqtt"]["connected"]
        assert requests.post(url + "/api/control", json={"action": "ignite", "x": 10, "y": 8}).status_code == 200
        assert requests.get(url + "/api/state").json()["metrics"]["fire_cells"] == 1
        assert requests.post(url + "/api/control", json={"action": "ignite", "x": -1, "y": 8}).status_code == 400
        goal = {"action": "goal", "agent_id": "agent-04", "x": 47, "y": 22}
        assert requests.post(url + "/api/control", json=goal).status_code == 200
        snapshot = requests.get(url + "/api/state").json()
        agent = next(a for a in snapshot["agents"] if a["id"] == "agent-04")
        assert agent["goal"] == [47, 22] and agent["path"] == []
        assert any(e.get("agent_id") == "agent-04" and e["kind"] == "goal" for e in snapshot["events"])
        assert len(snapshot["messages"]) > 12
        assert requests.post(url + "/api/control", json={"action": "reset"}, headers={"Origin": "https://example.org"}).status_code == 403
        assert requests.get(url + "/api/state", headers={"Host": "example.org"}).status_code == 403
        csv_data = requests.get(url + "/api/readings.csv").content.decode("utf-8-sig")
        assert "physical" in csv_data and "simulation" in csv_data
        assert requests.get(url + "/api/export.json").json()["readings"]
        assert requests.get(url + "/../config.example.json").status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        http_worker.join(5)
        demo.close()
        bridge.stop.set()
        worker.join(10)
