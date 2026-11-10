from __future__ import annotations

import argparse
import copy
import math
import csv
import json
import logging
import statistics
import sys
import time
import uuid
from pathlib import Path

from .core import Store, UNITS, load_config, utcnow, digest, validate_reading
from .mec import Ipe, Mec
from .mqtt import Bridge, publish, native_cin
from .onem2m import Cse


def provision(cfg):
    if not cfg.get("allow_provision", True):
        raise ValueError("Team profile disables provisioning; independent AE/ACP approval is required")
    for zone, endpoint in cfg["cse"].items():
        if zone not in ("A", "B", "IN"):
            continue
        # This uses the same per-AE originators as runtime uploads, preserving aei.
        for name, info in cfg["devices"].items():
            if zone in info["zones"]:
                Cse(endpoint, info["originator"]).ensure_ae(name, list(UNITS))
        if zone in ("A", "B"):
            Cse(endpoint, "CIpe").ensure_ae("mec-ipe", ["location", "rnis", "requests", "responses", "handover"])
            if cfg.get('rescue', {}).get('enabled', False):
                Cse(endpoint, 'CRescueControl').ensure_ae('rescue-control', ['events'])
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


DEFAULT_SAMPLES = Path(__file__).resolve().parents[3] / 'simulation' / 'sample_data.json'


def strict_json(text):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: raise ValueError('duplicate JSON key')
            result[k] = v
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError('non-finite JSON')))


def sample_plan(path, cfg):
    raw = Path(path).read_bytes()
    if len(raw) > 4 * 1024 * 1024: raise ValueError('sample file exceeds 4 MiB')
    from .recordings import parse_records
    records = parse_records(raw)
    accepted, rejected = [], []
    for index, record in enumerate(records):
        try:
            validate_reading(record, cfg['devices'])
            accepted.append((index, record))
        except (ValueError, TypeError, KeyError) as error:
            rejected.append(dict(index=index, reason=str(error)))
    return dict(input_sha256=__import__('hashlib').sha256(raw).hexdigest(), input_rows=len(records),
                accepted=accepted, rejected=rejected, mode='recorded-file',
                note='Values, units, source and observed_at are preserved; no live-device claim.')


def save(path, value):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(p.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(p)


def selected(plan, skip_invalid):
    if plan['rejected'] and not skip_invalid:
        raise ValueError('invalid sample rows: inspect samples report or explicitly use --skip-invalid')
    if not plan['accepted']: raise ValueError('no compatible sample rows')
    return plan['accepted']


def replay(cfg, plan, skip_invalid=False, factory=Cse):
    if not cfg.get("allow_cse_writes", True):
        raise ValueError("Team profile is read-only; register an independent AE and agree ACPs before enabling writes")
    records = selected(plan, skip_invalid)  # validate the entire batch before any write
    result = dict(input_sha256=plan['input_sha256'], rejected=plan['rejected'], events=[], complete=False)
    for index, reading in records:
        client = factory(cfg['cse'][reading['zone_id']], cfg['devices'][reading['device_id']]['originator'])
        try:
            path = '/' + reading['device_id'] + '/' + reading['sensor_type']
            cin = client.cin(path, reading)
            actual = client.call('GET', path + '/' + cin['rn'])['m2m:cin']
            if strict_json(actual['con']) != reading: raise ValueError('exact event read-back differs')
            result['events'].append(dict(index=index, verified=True, resource=cin['rn']))
        except Exception as error:
            result['events'].append(dict(index=index, verified=False, error=type(error).__name__))
            return result
        finally:
            client.session.close()
    result['complete'] = True
    return result


def benchmark_samples(cfg, plan, count, output, skip_invalid=False, factory=Cse):
    records = selected(plan, skip_invalid)
    if count < 1: raise ValueError('count must be positive')
    if 'VIA_IN' not in cfg['cse']: raise ValueError('configure actual VIA_IN Mcc route; independent IN storage is not a substitute')
    # Require an explicitly configured cross-CSE identity for every device.
    via = cfg['cse']['VIA_IN']
    for _, reading in records:
        if reading['zone_id'] != 'A': raise ValueError('this VIA_IN comparison is explicitly scoped to MN-A')
        if not via.get('originators', {}).get(reading['device_id']):
            raise ValueError('VIA_IN.originators must specify the real cross-CSE identity per device')
    rows = []; run = uuid.uuid4().hex
    for iteration in range(count):
        for index, reading in records:
            zone = reading['zone_id']
            if zone != 'A': raise ValueError('this VIA_IN comparison is explicitly scoped to MN-A')
            for route in (['A', 'VIA_IN'] if iteration % 2 == 0 else ['VIA_IN', 'A']):
                origin = (via['originators'][reading['device_id']] if route == 'VIA_IN'
                          else cfg['devices'][reading['device_id']]['originator'])
                client = factory(cfg['cse'][route], origin)
                start = time.perf_counter(); error = ''
                try:
                    path = '/' + reading['device_id'] + '/' + reading['sensor_type']
                    key = digest(dict(run=run, iteration=iteration, index=index, route=route))
                    cin = client.cin(path, reading, key)
                    fetched = client.call('GET', path + '/' + cin['rn'])['m2m:cin']
                    if strict_json(fetched['con']) != reading: raise ValueError('read-back differs')
                except Exception as exc: error = type(exc).__name__
                finally: client.session.close()
                rows.append(dict(iteration=iteration, index=index, path=route, payload_sha256=digest(reading),
                                 elapsed_ms=round((time.perf_counter()-start)*1000, 3), ok=not error, error=error))
    p = Path(output); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    summary = dict(input_sha256=plan['input_sha256'], rejected=plan['rejected'], complete=all(r['ok'] for r in rows), paths={})
    for route in ['A', 'VIA_IN']:
        values = sorted(r['elapsed_ms'] for r in rows if r['path']==route and r['ok'])
        summary['paths'][route] = dict(attempts=len(rows)//2, successes=len(values),
            median_ms=__import__('statistics').median(values) if values else None,
            p95_ms=values[math.ceil(.95*len(values))-1] if values else None)
    save(str(output)+'.json', summary)
    return summary


def find_netchar(active, element):
    found = []
    def walk(node):
        if isinstance(node, dict):
            if node.get('name') == element and isinstance(node.get('netChar'), dict): found.append(node['netChar'])
            for value in node.values(): walk(value)
        elif isinstance(node, list):
            for value in node: walk(value)
    walk(active)
    if len(found) != 1: raise ValueError('active scenario must expose one unambiguous element netChar baseline')
    return copy.deepcopy(found[0])


def network_experiment(mec, base, element, kind, cases, measure, output):
    baseline = find_netchar(mec.call(base.rstrip('/')+'/active'), element)
    cases = list(cases)
    for delay, loss in cases:
        if not math.isfinite(delay) or not math.isfinite(loss) or delay < 0 or not 0 <= loss <= 100:
            raise ValueError('invalid latency or loss percentage')
    report = dict(started_at=utcnow(), baseline=baseline, cases=[], restored=False,
                  traffic_path_verified=False, note='Operator must prove application traffic traverses the emulated path.')
    save(output, report)  # recovery information is durable before the first change
    url = base.rstrip('/')+'/events/NETWORK-CHARACTERISTICS-UPDATE'
    def apply(net):
        mec.call(url, 'POST', dict(name='rescuetwin-network-experiment', type='NETWORK-CHARACTERISTICS-UPDATE',
            eventNetworkCharacteristicsUpdate=dict(elementName=element, elementType=kind, netChar=net)))
    try:
        for i, (delay, loss) in enumerate(cases):
            net = dict(baseline, latency=delay, packetLoss=loss)
            apply(net)
            result = measure(i)
            report['cases'].append(dict(latency_ms=delay, loss_percent=loss, result=result))
    finally:
        try: apply(baseline); report['restored'] = True
        finally: save(output, report)
    return report


def run_samples(args, cfg):
    plan = sample_plan(args.file, cfg)
    if args.action == 'samples':
        save(args.output, plan)
        print(json.dumps(dict(input_rows=plan['input_rows'], accepted=len(plan['accepted']), rejected=len(plan['rejected']))))
    elif args.action == 'replay-samples':
        save(args.output+'.input.json', plan)
        result = replay(cfg, plan, args.skip_invalid); save(args.output, result)
        if not result['complete']: raise RuntimeError('partial replay; inspect report before rerunning')
    elif args.action == 'benchmark-samples':
        result = benchmark_samples(cfg, plan, args.count, args.output, args.skip_invalid)
        print(json.dumps(result))
        if not result['complete']: raise RuntimeError('benchmark incomplete; failed attempts retained')
    else:
        from .mec import Mec
        selected(plan, args.skip_invalid)
        if 'VIA_IN' not in cfg['cse']: raise ValueError('configure VIA_IN before changing network')
        def measure(i):
            return benchmark_samples(cfg, plan, args.count, str(Path(args.output).with_suffix(''))+f'-case-{i}.csv', args.skip_invalid)
        result = network_experiment(Mec(cfg['mec'],cfg.get('require_tls',True)),
            cfg['mec']['sandbox_control_url'], args.element, args.element_type,
            [tuple(map(float,v.split(','))) for v in args.case], measure, args.output)
        if not all(x['result']['complete'] for x in result['cases']): raise RuntimeError('experiment contains failed probes')


def main():
    parser = argparse.ArgumentParser(description="RescueTwin MQTT + MEC integration")
    parser.add_argument("--config", default="config.example.json")
    subs = parser.add_subparsers(dest="action", required=True)
    subs.add_parser("provision")
    subs.add_parser("bridge")
    subs.add_parser("status")
    native = subs.add_parser("native-import")
    native.add_argument("--file", required=True)
    native.add_argument("--zone", choices=["A", "B"], default="A")
    native.add_argument("--send", action="store_true")
    native.add_argument("--skip-invalid", action="store_true")
    native.add_argument("--output", default="runtime/native-import.json")
    check = subs.add_parser("check-team")
    check.add_argument("--output", default="runtime/team-check.json")
    for action in ['samples','replay-samples','benchmark-samples','network-experiment']:
        command = subs.add_parser(action)
        command.add_argument('--file', default=str(DEFAULT_SAMPLES))
        command.add_argument('--skip-invalid', action='store_true')
        command.add_argument('--output', default='runtime/'+action+('.csv' if action=='benchmark-samples' else '.json'))
        command.add_argument('--count', type=int, default=3)
        if action == 'network-experiment':
            command.add_argument('--element', required=True)
            command.add_argument('--element-type', choices=['UE','DC','EDGE','ZONE'], required=True)
            command.add_argument('--case', action='append', required=True, help='latency_ms,loss_percent; repeat for matrix')
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
    p.add_argument("operation", choices=["get_location", "get_rnis", "configure_mobility"])
    p.add_argument("--address", default="10.10.0.1")
    p = subs.add_parser("benchmark")
    p.add_argument("--count", type=int, default=20)
    p.add_argument("--output", default="runtime/storage-rtt.csv")
    p = subs.add_parser("confirm-ready")
    p.add_argument("app_instance_id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if not cfg.get("allow_cse_writes", True) and args.action in ("bridge", "publish", "native-publish", "ipe", "command", "benchmark", "benchmark-samples", "network-experiment"):
        raise ValueError("Team profile disables write operations until an independent application identity and ACP are agreed")
    if args.action == "native-import":
        from .native import native_plan, native_upload
        plan = native_plan(cfg, args.zone, args.file)
        save(args.output, plan)
        result = native_upload(cfg, plan, args.skip_invalid, args.send)
        save(args.output, result)
        print(json.dumps(result, indent=2))
        if args.send and not result["complete"]: raise RuntimeError("Native upload incomplete; inspect per-record results")
    elif args.action == "check-team":
        from .team import check_team
        result = check_team(cfg)
        save(args.output, result)
        print(json.dumps(result, indent=2))
        if not result["complete"]: raise RuntimeError("Some read-only checks failed; inspect the report")
    elif args.action in ['samples','replay-samples','benchmark-samples','network-experiment']:
        run_samples(args,cfg)
    elif args.action == "provision":
        provision(cfg)
    elif args.action == "publish":
        value = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if args.now:
            value["observed_at"] = utcnow()
        publish(cfg["mqtt"], value, cfg["devices"])
        print(f"SUCCESS: published to {cfg['mqtt']['host']}:{cfg['mqtt']['port']} (broker acknowledged).")
        print("MQTTX connects separately to this broker. CSE read-back confirms storage.")
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
        elif args.operation == 'configure_mobility':
            command['address'] = args.address
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
