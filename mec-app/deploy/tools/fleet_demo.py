"""Run the oneM2M fleet features once and save the evidence as JSON.

Covers: AE registration (agents + MEC app), per-agent ACPs, label/type
discovery, SDT flexContainers, group recall with notifications, and one
CSE handover with interruption and data-loss figures.

Example (from mec-app/deploy, with both MN-CSEs running):
    python tools/fleet_demo.py --config config.example.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rescuetwin.core import load_config, utcnow  # noqa: E402
from rescuetwin.fleet import Fleet  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--output", default="runtime/fleet-evidence.json")
    parser.add_argument("--handover-agent", default="agent-01")
    args = parser.parse_args()
    cfg = load_config(args.config)
    fleet = Fleet(cfg)
    evidence = {"started_at": utcnow(), "cse": {z: cfg["cse"][z]["base_url"] for z in ("A", "B")}}
    try:
        fleet.start({"agent-01": "A", "agent-02": "A", "robot-01": "A", "agent-05": "B"})
        evidence["discovery"] = {
            "drones_zone_A": fleet.discover("A", ["drone@zone-A"], 2),
            "robots_zone_A": fleet.discover("A", ["robot@zone-A"], 2),
            "drones_zone_B": fleet.discover("B", ["drone@zone-B"], 2),
            "agent_groups_zone_A": fleet.discover("A", ["agent-group"], 9)}
        for sensor, value in (("temperature", 61.5), ("humidity", 48.0), ("smoke_raw", 2600), ("presence", True)):
            fleet.node_state("A", "node-01", {"sensor_type": sensor, "value": value, "quality": "valid", "source": "simulation"})
        evidence["discovery"]["node_state_flexcontainers"] = fleet.discover("A", ["node-state"], 28)
        evidence["group_recall_zone_A"] = fleet.recall("A", {"operation": "recall", "reason": "evidence run"})
        time.sleep(1)
        evidence["notifications_received"] = {a: len(fleet.notifications.for_agent(a)) for a in fleet.serving}
        agent = args.handover_agent
        for seq in range(3):
            fleet.report(agent, {"x_m": 70.0 + seq, "y_m": 9.0, "zone_id": "A", "source": "simulation", "observed_at": utcnow()})
            time.sleep(.2)
        # The zone in this report differs from the serving zone, which triggers the handover.
        fleet.report(agent, {"x_m": 84.0, "y_m": 9.0, "zone_id": "B", "source": "simulation", "observed_at": utcnow()})
        evidence["handover"] = fleet.handovers[-1]
        evidence["group_recall_zone_B_after_handover"] = fleet.recall("B", {"operation": "recall"})
        evidence["serving"] = dict(fleet.serving)
    finally:
        fleet.close()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
