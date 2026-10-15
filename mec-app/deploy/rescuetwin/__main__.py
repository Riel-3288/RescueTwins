from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
import time
import uuid
from pathlib import Path

from .core import Store, UNITS, load_config, utcnow
from .mec import Ipe, Mec
from .mqtt import Bridge, publish, native_cin
from .onem2m import Cse


def provision(cfg):
    for zone, endpoint in cfg["cse"].items():
        if zone not in ("A", "B", "IN"):
            continue
        # This uses the same per-AE originators as runtime uploads, preserving aei.
        for name, info in cfg["devices"].items():
            if zone in info["zones"]:
                Cse(endpoint, info["originator"]).ensure_ae(name, list(UNITS))
        if zone in ("A", "B"):
            Cse(endpoint, "CIpe").ensure_ae("mec-ipe", ["location", "rnis", "requests", "responses", "handover"])
        Cse(endpoint, "CBenchmark").ensure_ae("benchmark", ["samples"])
        print(f"Provisioned {zone}")


def benchmark(cfg, count, output):
    # Matched workloads: CIN write + read-back. This measures storage round-trip,
    # not ESP32-to-reroute latency. VIA_IN requires actual Mcc registration.
    paths = ("A", "VIA_IN") if "VIA_IN" in cfg["cse"] else ("A", "IN")
    clients = {zone: Cse(cfg["cse"][zone], cfg["cse"][zone].get("originator", "CBenchmark")) for zone in paths}
    rows = []
    for i in range(count):
        for zone in (paths if i % 2 == 0 else tuple(reversed(paths))):
            client = clients[zone]
            value = {"probe_id": uuid.uuid4().hex, "observed_at": utcnow(), "value": 28.6}
            start = time.perf_counter()
            error = ""
            try:
                cin = client.cin("/benchmark/samples", value)
                fetched = client.call("GET", "/benchmark/samples/" + cin["rn"])
                if json.loads(fetched["m2m:cin"]["con"]) != value:
                    raise ValueError("probe read-back differs")
            except Exception as exc:
                error = str(exc)
            rows.append({"iteration": i, "path": zone, "elapsed_ms": round((time.perf_counter() - start) * 1000, 3), "ok": not bool(error), "error": error})
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for zone in clients:
        values = sorted(r["elapsed_ms"] for r in rows if r["path"] == zone and r["ok"])
        print(json.dumps({"path": zone, "successes": len(values), "attempts": count,
                          "median_ms": statistics.median(values) if values else None,
                          "p95_ms": values[min(len(values)-1, int(len(values)*0.95))] if values else None}))
    if any(not r["ok"] for r in rows):
        raise RuntimeError("one or more benchmark probes failed; see CSV")


def main():
    parser = argparse.ArgumentParser(description="RescueTwin MQTT + MEC integration")
    parser.add_argument("--config", default="config.example.json")
    subs = parser.add_subparsers(dest="action", required=True)
    subs.add_parser("provision")
    subs.add_parser("bridge")
    subs.add_parser("status")
    p = subs.add_parser("publish")
    p.add_argument("file")
    p.add_argument("--now", action="store_true")
    p = subs.add_parser("native-publish")
    p.add_argument("file")
    p.add_argument("--cse-id", default="id-mn-a")
    p.add_argument("--cse-name", default="cse-mn-a")
    p = subs.add_parser("ipe")
    p.add_argument("--once", action="store_true")
    p = subs.add_parser("command")
    p.add_argument("operation", choices=["get_location", "get_rnis"])
    p.add_argument("--address", default="10.10.0.1")
    p = subs.add_parser("benchmark")
    p.add_argument("--count", type=int, default=20)
    p.add_argument("--output", default="runtime/storage-rtt.csv")
    p = subs.add_parser("confirm-ready")
    p.add_argument("app_instance_id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.action == "provision":
        provision(cfg)
    elif args.action == "publish":
        value = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if args.now:
            value["observed_at"] = utcnow()
        publish(cfg["mqtt"], value, cfg["devices"])
        print("MQTT broker acknowledged publication; use CSE read-back to confirm storage.")
    elif args.action == "bridge":
        Bridge(cfg).run()
    elif args.action == "native-publish":
        from .core import validate_reading
        value = json.loads(Path(args.file).read_text(encoding="utf-8"))
        validate_reading(value, cfg["devices"])
        originator = cfg["devices"][value["device_id"]]["originator"]
        target = f"/{args.cse_id}/{args.cse_name}/{value['device_id']}/{value['sensor_type']}"
        print(json.dumps(native_cin(cfg["mqtt"], originator, args.cse_id, target, value)))
    elif args.action == "status":
        store = Store(cfg["state_file"])
        print(json.dumps({"outbox": store.counts(), "rejected": store.get("rejected", 0)}))
    elif args.action == "ipe":
        ipe = Ipe(cfg)
        while True:
            errors = ipe.cycle()
            if args.once:
                if errors:
                    raise RuntimeError("IPE cycle has failures")
                break
            time.sleep(1)
    elif args.action == "command":
        command = {"command_id": uuid.uuid4().hex, "operation": args.operation}
        if args.operation == "get_location":
            command["address"] = args.address
        cin = Cse(cfg["cse"][cfg.get("ipe_zone", "A")], "CIpe").cin("/mec-ipe/requests", command)
        print(json.dumps({"command_id": command["command_id"], "resource": cin["rn"]}))
    elif args.action == "benchmark":
        if args.count < 1:
            raise ValueError("count must be positive")
        benchmark(cfg, args.count, args.output)
    elif args.action == "confirm-ready":
        from urllib.parse import quote
        url = cfg["mec"]["app_support_url"].rstrip("/") + "/applications/" + quote(args.app_instance_id, safe="") + "/confirm_ready"
        print(json.dumps(Mec(cfg["mec"], cfg.get("require_tls", True)).call(url, "POST", {"indication": "READY"})))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
