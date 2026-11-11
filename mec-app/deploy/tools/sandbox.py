"""Operate an already-authorized AdvantEDGE Sandbox Control API, using its shipped schema."""
import argparse
import json
import os
from pathlib import Path

import requests
import yaml

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", required=True, help="Exact sandbox-ctrl/v1 URL supplied by your environment")
subs = parser.add_subparsers(dest="action", required=True)
p = subs.add_parser("import")
p.add_argument("file")
p = subs.add_parser("activate")
p.add_argument("name", default="rescuetwin-factory")
p = subs.add_parser("network")
p.add_argument("element")
p.add_argument("--type", choices=["UE", "DC", "EDGE", "ZONE"], required=True)
p.add_argument("--latency-ms", type=int, default=0)
p.add_argument("--jitter-ms", type=int, default=0)
p.add_argument("--loss-percent", type=float, default=0)
subs.add_parser("status")
args = parser.parse_args()
if not args.base_url.startswith("https://") and not args.base_url.startswith("http://127.0.0.1"):
    raise ValueError("Use HTTPS for shared Sandbox Control")
headers = {"Accept": "application/json"}
if os.getenv("MEC_TOKEN"):
    headers["Authorization"] = "Bearer " + os.environ["MEC_TOKEN"]
session = requests.Session()
session.trust_env = False
body, method, path = None, "GET", "/active"
if args.action == "import":
    body = yaml.safe_load(Path(args.file).read_text(encoding="utf-8"))
    method, path = "POST", "/scenarios"
elif args.action == "activate":
    from urllib.parse import quote
    method, path = "POST", "/active/" + quote(args.name, safe="")
elif args.action == "network":
    if args.latency_ms < 0 or args.jitter_ms < 0 or not 0 <= args.loss_percent <= 100:
        raise ValueError("invalid network impairment")
    method, path = "POST", "/events/NETWORK-CHARACTERISTICS-UPDATE"
    body = {"name": "rescuetwin-test-network", "type": "NETWORK-CHARACTERISTICS-UPDATE",
            "eventNetworkCharacteristicsUpdate": {"elementName": args.element, "elementType": args.type,
              "netChar": {"latency": args.latency_ms, "latencyVariation": args.jitter_ms, "packetLoss": args.loss_percent}}}
response = session.request(method, args.base_url.rstrip("/")+path, headers=headers, json=body, timeout=30, allow_redirects=False)
response.raise_for_status()
if not 200 <= response.status_code < 300:
    raise ValueError("Sandbox Control redirect not accepted")
print(response.text if response.content else f"HTTP {response.status_code}")
