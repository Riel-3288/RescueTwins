"""Generate an AdvantEDGE scenario and a GeoJSON floor-plan from one factory definition."""
import json
import math
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
cfg = json.loads((ROOT / "config.example.json").read_text())
factory = cfg["factory"]


def point(x, y):
    return [factory["origin_lon"] + x / (111320 * math.cos(math.radians(factory["origin_lat"]))),
            factory["origin_lat"] + y / 111320]


def node(name, type, **kwargs):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "rescuetwin." + name)), "name": name, "type": type, **kwargs}


def net(latency=1, loss=0):
    return {"latency": latency, "latencyVariation": 0, "throughputUl": 100, "throughputDl": 100, "packetLoss": loss}


def geo(x, y):
    return {"location": {"type": "Point", "coordinates": point(x, y)}}


zones = []
for index, zone in enumerate(("A", "B")):
    x = 25 + index * 50
    host = node("mec-host-" + zone.lower(), "EDGE", connected=True, geoData=geo(x, 30), processes=[], netChar=net(0))
    terminals = []
    for i in range(1 + index * 5, 6 + index * 5):
        y = 8 + ((i-1) % 5) * 10
        path = [point(10, y), point(45, y), point(55, y), point(90, y)]
        terminals.append(node(f"10.10.0.{i}", "UE", connected=True, wireless=True, wirelessType="5g",
                              geoData={**geo(10 if index == 0 else 90, y), "path": {"type": "LineString", "coordinates": path}, "velocity": 1, "eopMode": "REVERSE"},
                              netChar=net(1)))
    default = node(f"zone-{zone}-DEFAULT", "DEFAULT", physicalLocations=[host], netChar=net(0))
    poa = node(f"poa-5g-{zone.lower()}", "POA-5G", poa5GConfig={"cellId": f"101010{index+1}"},
               geoData={**geo(x, 30), "radius": 45}, physicalLocations=terminals, netChar=net(1))
    zones.append(node(f"zone-{zone}", "ZONE", netChar=net(1), networkLocations=[default, poa], meta={"display.map.color": "#C23B22" if zone == "A" else "#1F6E8C"}))
cloud = node("in-host", "DC", connected=True, geoData=geo(50, 120), netChar=net(0), processes=[])
common = node("factory-COMMON", "COMMON", networkLocations=[node("factory-COMMON-DEFAULT", "DEFAULT", physicalLocations=[cloud], netChar=net(40))], netChar=net(0))
scenario = {"version": "1.11.0", "name": "rescuetwin-factory", "description": "100x60 metre synthetic factory, two logical MEC zones, ten UE tracks. Application deployment is separate.",
            "deployment": {"connectivity": {"model": "OPEN"}, "netChar": net(0), "domains": [node("factory-operator", "OPERATOR-CELLULAR", netChar=net(0), cellularDomainConfig={"mcc": "001", "mnc": "002", "defaultCellId": "CAFEDEC"}, zones=[common, *zones])]}}
(ROOT / "scenario").mkdir(exist_ok=True)
(ROOT / "scenario/factory.yaml").write_text(yaml.safe_dump(scenario, sort_keys=False), encoding="utf-8")
features = []
for zone, left, right in [("A", 0, 50), ("B", 50, 100)]:
    ring = [point(left, 0), point(right, 0), point(right, 60), point(left, 60), point(left, 0)]
    features.append({"type": "Feature", "properties": {"zone_id": zone}, "geometry": {"type": "Polygon", "coordinates": [ring]}})
(ROOT / "scenario/factory-map.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2), encoding="utf-8")
print("Generated scenario/factory.yaml and factory-map.geojson")
