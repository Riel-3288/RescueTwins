from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone

import requests

from .core import Geofence, Store, canonical, digest, utc_timestamp, utcnow
from .onem2m import Cse, CseError

LOG = logging.getLogger(__name__)


class Mec:
    def __init__(self, config, require_tls=True):
        self.config = config
        self.session = requests.Session()
        self.session.trust_env = False
        for key in ("location_url", "rnis_url", "app_support_url"):
            if config.get(key) and require_tls and not config[key].startswith("https://"):
                raise ValueError("require_tls=true requires HTTPS MEC services")

    def call(self, url, method="GET", body=None, params=None):
        if not url:
            raise ValueError("MEC service URL is not configured")
        token = os.getenv(self.config.get("token_env", "MEC_TOKEN"))
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        result = self.session.request(method, url, json=body, params=params, headers=headers,
                                      verify=self.config.get("ca_file") or True, timeout=8,
                                      allow_redirects=False)
        result.raise_for_status()
        if not 200 <= result.status_code < 300:
            raise ValueError("MEC redirect is not accepted")
        return result.json() if result.content else {}

    def locations(self, address=None):
        return self.call(self.config["location_url"].rstrip("/") + "/queries/users",
                         params={"address": address} if address else None)

    def rnis(self):
        return self.call(self.config["rnis_url"].rstrip("/") + "/queries/rab_info")


class Ipe:
    def __init__(self, config, store=None):
        self.config = config
        self.store = store or Store(config.get("ipe_state_file", "runtime/ipe.sqlite"))
        self.mec = Mec(config["mec"], config.get("require_tls", True))
        self.geo = Geofence(config["factory"])
        self.zone = config.get("ipe_zone", "A")
        self.cse = Cse(config["cse"][self.zone], "CIpe")

    def position(self, agent, user):
        info = user.get("locationInfo") or {}
        latitudes, longitudes = info.get("latitude"), info.get("longitude")
        if info.get("shape") not in (2, 3, 4, 5, 6) or not latitudes or not longitudes:
            raise ValueError("MEC user location must describe a single point")
        lat, lon = float(latitudes[0]), float(longitudes[0])
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("invalid MEC coordinate")
        timestamp = user.get("timeStamp")
        if not isinstance(timestamp, dict) or "seconds" not in timestamp:
            raise ValueError("MEC location has no measurement timestamp")
        observed = datetime.fromtimestamp(int(timestamp["seconds"]) + int(timestamp.get("nanoSeconds", 0)) / 1e9, timezone.utc).isoformat().replace("+00:00", "Z")
        if time.time() - utc_timestamp(observed) > self.config["mec"].get("max_location_age_s", 30):
            raise ValueError("stale MEC location cannot trigger handover")
        x, y = self.geo.xy(lat, lon)
        old = self.store.get("agent:" + agent)
        state, handover = self.geo.update(old, x, y, observed)
        if old and state["timestamp"] == old["timestamp"]:
            return
        origin = self.config["agents"][agent]["originator"]
        target = Cse(self.config["cse"][state["zone"]], origin)
        # Prepare the target before committing the serving-zone change.
        target.ensure_ae(agent, ["position"])
        record = {"agent_id": agent, "zone_id": state["zone"], "x_m": round(x, 3), "y_m": round(y, 3),
                  "observed_at": observed, "source": "mec-sandbox", "mec_address": user["address"]}
        target.cin(f"/{agent}/position", record)
        if handover:
            event = dict(handover, agent_id=agent, target_originator=origin, mode="application-layer-reregistration")
            self.cse.cin("/mec-ipe/handover", event)
            self.store.set("last_handover:" + agent, event)
        self.store.set("agent:" + agent, state)

    def poll_locations(self):
        payload = self.mec.locations()
        users = payload.get("userList", {}).get("user", [])
        addresses = {item["address"]: name for name, item in self.config["agents"].items()}
        for user in users:
            if user.get("address") in addresses:
                try:
                    self.position(addresses[user["address"]], user)
                except (ValueError, KeyError, TypeError) as exc:
                    LOG.warning("Rejected MEC location: %s", exc)
        self.cse.cin("/mec-ipe/location", {"received_at": utcnow(), "service": "MEC013", "data": payload})

    def commands(self):
        for cin in self.cse.entries("/mec-ipe/requests"):
            key = "command:" + cin["ri"]
            if self.store.get(key):
                continue
            command = json.loads(cin["con"])
            command_id = command.get("command_id")
            if not isinstance(command_id, str) or len(command_id) > 64:
                response = {"command_id": None, "ok": False, "error": "invalid command_id"}
            else:
                saved = self.store.get("command-response:" + command_id)
                if saved:
                    if saved["request_digest"] != digest(command):
                        raise ValueError("command_id reused for different request")
                    response = saved["response"]
                else:
                    try:
                        if command.get("operation") == "get_location":
                            address = command.get("address")
                            if address not in {a["address"] for a in self.config["agents"].values()}:
                                raise ValueError("unmapped MEC address")
                            data = self.mec.locations(address)
                        elif command.get("operation") == "get_rnis":
                            data = self.mec.rnis()
                        else:
                            raise ValueError("allowed operations: get_location, get_rnis")
                        response = {"command_id": command_id, "ok": True, "data": data, "completed_at": utcnow()}
                    except ValueError as exc:
                        response = {"command_id": command_id, "ok": False, "error": str(exc)}
                    # Save before posting, so a lost CSE response cannot repeat the MEC call.
                    self.store.set("command-response:" + command_id, {"request_digest": digest(command), "response": response})
            self.cse.cin("/mec-ipe/responses", response, digest({"request_ri": cin["ri"]}))
            self.store.set(key, True)

    def cycle(self):
        # A failed location poll must not prevent command processing or RNIS.
        actions = [self.poll_locations, self.commands]
        if self.config["mec"].get("rnis_url"):
            actions.append(lambda: self.cse.cin("/mec-ipe/rnis", {"received_at": utcnow(), "service": "MEC012", "data": self.mec.rnis()}))
        errors = []
        for action in actions:
            try:
                action()
            except (requests.RequestException, CseError, ValueError, KeyError) as exc:
                LOG.warning("MEC-IPE cycle failed: %s", exc)
                errors.append(str(exc))
        return errors
