"""oneM2M service layer for the simulated fleet and the MEC application.

What this module does, using plain oneM2M requests on the zone MN-CSEs:
  * registers each drone / robot and the MEC application as an AE
  * gives every agent its own ACP (least privilege, three roles)
  * keeps a zone <group> so one request recalls every agent in the zone
  * mirrors sensor readings into TS-0023 (SDT) flexContainers for discovery
  * discovers resources with label / type filter queries
  * moves an agent to the other zone's MN-CSE (CSE handover) and measures
    the service interruption and data loss

Status: implemented and tested against local ACME CSEs (tests/test_fleet.py).
It has not been run on the team's hardware deployment yet.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from .core import utcnow
from .onem2m import Cse, CseError

LOG = logging.getLogger(__name__)

# accessControlOperations bits (TS-0004): CREATE 1, RETRIEVE 2, UPDATE 4,
# DELETE 8, NOTIFY 16, DISCOVERY 32.
ALL, CREATE_READ_DISCOVER, READ_DISCOVER = 63, 35, 34
AGENT_CONTAINERS = {"position": {"mni": 100}, "observations": {"mni": 100},
                    # Commands expire after 10 minutes so a late agent never runs a stale order.
                    "cmd": {"mni": 20, "mia": 600}}
SDT = "org.onem2m.common.moduleclass."


def originator_for(agent_id):
    """agent-01 -> CAgent01, robot-02 -> CRobot02."""
    kind, _, number = agent_id.partition("-")
    return "C" + kind.capitalize() + number


def expiry(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y%m%dT%H%M%S")


class Notifications:
    """Loopback HTTP endpoint that receives oneM2M notifications."""

    def __init__(self, host="127.0.0.1", port=0):
        self.items, self.lock = [], threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(size) if 0 < size <= 262144 else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {}
                sgn = body.get("m2m:sgn", {}) if isinstance(body, dict) else {}
                # The CSE sends a verification request when the subscription is created.
                if not sgn.get("vrq"):
                    with outer.lock:
                        outer.items.append({"received": time.time(), "path": self.path, "sgn": sgn})
                self.send_response(200)
                self.send_header("X-M2M-RSC", "2000")
                self.send_header("X-M2M-RI", self.headers.get("X-M2M-RI", ""))
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.url = f"http://{host}:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, name="onem2m-notify", daemon=True).start()

    def for_agent(self, agent_id):
        with self.lock:
            return [n for n in self.items if n["path"].rstrip("/").endswith("/" + agent_id)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Fleet:
    def __init__(self, config, notifications=None):
        self.config = config
        self.settings = config.get("fleet", {})
        self.mec = self.settings.get("mec_originator", "CRescueMec")
        # Originators that may read and discover, e.g. the twin dashboard on the IN-CSE.
        self.readers = list(self.settings.get("readers", []))
        self.notifications = notifications or Notifications(
            self.settings.get("notify_host", "127.0.0.1"), self.settings.get("notify_port", 0))
        self.clients, self.serving, self.kinds = {}, {}, {}
        self.sequence, self.last_ok, self.buffers = {}, {}, {}
        self.in_handover, self.handovers = set(), []
        self.lock = threading.RLock()

    # ---- helpers -------------------------------------------------------
    def client(self, zone, originator):
        key = (zone, originator)
        if key not in self.clients:
            self.clients[key] = Cse(self.config["cse"][zone], originator)
        return self.clients[key]

    def base_name(self, zone):
        return self.config["cse"][zone]["base_url"].rstrip("/").split("/")[-1]

    def acp(self, cse, parent, name, rules):
        body = {"pv": {"acr": [{"acor": list(who), "acop": ops} for who, ops in rules if who]},
                "pvs": {"acr": [{"acor": [cse.originator], "acop": ALL}]}}
        return cse.ensure(parent, name, 1, body, "m2m:acp")["ri"]

    def protect(self, cse, path, wrapper, acp_id):
        """Attach the ACP to an existing resource (acpi must be updated alone)."""
        if acp_id not in cse.call("GET", path)[wrapper].get("acpi", []):
            cse.call("PUT", path, {wrapper: {"acpi": [acp_id]}})

    # ---- registration --------------------------------------------------
    def setup_zone(self, zone):
        """Register the MEC application AE on this zone's MN-CSE."""
        cse = self.client(zone, self.mec)
        cse.ensure("", "rescue-mec", 2, {"api": "NRescueTwin.mec", "rr": True, "srv": ["4"],
                                         "lbl": ["RescueTwin", "mec-app", "zone-" + zone]}, "m2m:ae")
        acp_id = self.acp(cse, "/rescue-mec", "acp-mec", [((self.mec,), ALL), (self.readers, READ_DISCOVER)])
        self.protect(cse, "/rescue-mec", "m2m:ae", acp_id)
        cse.ensure("/rescue-mec", "handover", 3, {"mni": 100, "acpi": [acp_id],
                                                  "lbl": ["RescueTwin", "handover", "zone-" + zone]}, "m2m:cnt")
        # mt=3: every member is a container, so one fan-out request reaches all agents.
        cse.ensure("/rescue-mec", "grp-agents", 9, {"mt": 3, "mnm": 32, "mid": [], "acpi": [acp_id],
                                                    "lbl": ["RescueTwin", "agent-group", "zone-" + zone]}, "m2m:grp")
        return acp_id

    def register_agent(self, agent_id, zone, kind="drone"):
        """AE + own ACP + containers + command subscription + group membership."""
        cse = self.client(zone, originator_for(agent_id))
        # Several lbl filters in one query match ANY of them, so one combined
        # label ("drone@zone-A") lets a single query find one kind in one zone.
        labels = ["RescueTwin", "agent", kind, "zone-" + zone, f"{kind}@zone-{zone}"]
        cse.ensure("", agent_id, 2, {"api": "NRescueTwin.agent", "rr": True, "srv": ["4"], "lbl": labels}, "m2m:ae")
        # Three roles: the agent owns its data, the MEC app may add commands, the twin may only read.
        acp_id = self.acp(cse, "/" + agent_id, "acp-agent",
                          [((cse.originator,), ALL), ((self.mec,), CREATE_READ_DISCOVER), (self.readers, READ_DISCOVER)])
        self.protect(cse, "/" + agent_id, "m2m:ae", acp_id)
        for name, attrs in AGENT_CONTAINERS.items():
            cse.ensure("/" + agent_id, name, 3, dict(attrs, acpi=[acp_id], lbl=labels + [name]), "m2m:cnt")
        # The subscription expires on its own; a handover creates a fresh one on the target CSE.
        cse.ensure(f"/{agent_id}/cmd", "sub-cmd", 23,
                   {"nu": [f"{self.notifications.url}/notify/{agent_id}"], "enc": {"net": [3]}, "nct": 1,
                    "et": expiry(self.settings.get("subscription_ttl_s", 3600))}, "m2m:sub")
        self.set_member(zone, agent_id, True)
        with self.lock:
            self.serving[agent_id], self.kinds[agent_id] = zone, kind
            self.sequence.setdefault(agent_id, 0)
        return acp_id

    def set_member(self, zone, agent_id, present):
        cse = self.client(zone, self.mec)
        member = f"{self.base_name(zone)}/{agent_id}/cmd"
        group = cse.call("GET", "/rescue-mec/grp-agents")["m2m:grp"]
        # ACME may return member resource IDs, so compare against both forms.
        ids = set()
        if not present:
            try:
                ids.add(self.client(zone, originator_for(agent_id)).call("GET", f"/{agent_id}/cmd")["m2m:cnt"]["ri"])
            except CseError:
                pass
        current = [m for m in group.get("mid", []) if m != member and m not in ids
                   and not m.endswith(f"/{agent_id}/cmd")]
        if present:
            current.append(member)
        if sorted(current) != sorted(group.get("mid", [])):
            cse.call("PUT", "/rescue-mec/grp-agents", {"m2m:grp": {"mid": current}})

    def start(self, agents):
        """Register the MEC app on both zones, then every agent on its start zone."""
        for zone in ("A", "B"):
            self.setup_zone(zone)
        for agent_id, zone in agents.items():
            # The live simulator names its drones agent-NN; ground robots are robot-NN.
            prefix = agent_id.partition("-")[0]
            self.register_agent(agent_id, zone, "drone" if prefix == "agent" else prefix)

    # ---- data ----------------------------------------------------------
    def report(self, agent_id, record):
        """Store one position record on the agent's serving MN-CSE.

        The zone in the record comes from the location geofence. When it is
        not the serving zone, the handover runs first (location-based trigger).
        """
        zone = record.get("zone_id")
        if zone in ("A", "B") and self.serving.get(agent_id) not in (None, zone):
            self.handover(agent_id, zone)
        with self.lock:
            self.sequence[agent_id] += 1
            record = dict(record, agent_id=agent_id, seq=self.sequence[agent_id])
            if agent_id in self.in_handover:
                # Keep reports made during a handover and store them on the target afterwards.
                self.buffers.setdefault(agent_id, []).append(record)
                return None
            zone = self.serving[agent_id]
        cin = self.client(zone, originator_for(agent_id)).cin(f"/{agent_id}/position", record)
        with self.lock:
            self.last_ok[agent_id] = time.monotonic()
        return cin

    def discover(self, zone, labels=(), ty=None, originator=None):
        """Filter-usage discovery: returns the paths of matching resources."""
        query = "?fu=1" + (f"&ty={ty}" if ty else "") + "".join("&lbl=" + label for label in labels)
        return self.client(zone, originator or self.mec).call("GET", query).get("m2m:uril", [])

    def recall(self, zone, command):
        """One request to the group fans the command out to every agent in the zone."""
        value = dict(command, command_id=command.get("command_id") or uuid.uuid4().hex, issued_at=utcnow())
        body = {"m2m:cin": {"cnf": "application/json:0", "con": json.dumps(value, sort_keys=True)}}
        result = self.client(zone, self.mec).call("POST", "/rescue-mec/grp-agents/fopt", body, 4)
        responses = result.get("m2m:agr", {}).get("m2m:rsp", [])
        return {"command_id": value["command_id"], "members": len(responses),
                "created": sum(r.get("rsc") == 2001 for r in responses)}

    def node_state(self, zone, node, reading):
        """Mirror one sensor reading into an SDT (TS-0023) flexContainer."""
        models = {"temperature": ("cod:tempe", "temperature", lambda v: {"curT0": float(v)}),
                  "humidity": ("cod:relHy", "relativeHumidity", lambda v: {"relHy": float(v)}),
                  "smoke_raw": ("cod:smoSr", "smokeSensor", lambda v: {"crv": int(v), "alarm": v >= self.threshold("smoke_raw")}),
                  "presence": ("cod:motSr", "motionSensor", lambda v: {"alarm": bool(v)})}
        if reading["sensor_type"] not in models or reading.get("quality") != "valid":
            return None
        wrapper, name, attrs = models[reading["sensor_type"]]
        cse = self.client(zone, self.mec)
        values = attrs(reading["value"])
        path = f"/rescue-mec/{node}-{name}"
        try:
            return cse.call("PUT", path, {wrapper: values})[wrapper]
        except CseError as exc:
            if exc.status != 404:
                raise
        acp_id = cse.call("GET", "/rescue-mec/acp-mec")["m2m:acp"]["ri"]
        body = dict(values, rn=f"{node}-{name}", cnd=SDT + name, acpi=[acp_id],
                    lbl=["RescueTwin", "node-state", node, "zone-" + zone, reading["source"]])
        return cse.call("POST", "/rescue-mec", {wrapper: body}, 28)[wrapper]

    def threshold(self, sensor):
        defaults = {"smoke_raw": 2500}
        return self.config.get("rescue", {}).get("thresholds", {}).get(sensor, {}).get("trigger", defaults[sensor])

    # ---- CSE handover --------------------------------------------------
    def handover(self, agent_id, to_zone, trigger="factory_geofence"):
        """Move an agent to the target zone's MN-CSE and measure the gap.

        Steps: prepare the target (AE, ACP, containers, subscription), copy the
        last known state, switch the group membership, then store the reports
        that were buffered while the switch was running.
        """
        with self.lock:
            from_zone = self.serving[agent_id]
            if from_zone == to_zone or agent_id in self.in_handover:
                return None
            self.in_handover.add(agent_id)
            last_ok = self.last_ok.get(agent_id)
        started, wall = time.monotonic(), utcnow()
        origin = originator_for(agent_id)
        report = {"agent_id": agent_id, "from_zone": from_zone, "to_zone": to_zone, "trigger": trigger,
                  "started_at": wall, "mode": "application-layer-reregistration", "steps": []}
        try:
            source = self.client(from_zone, origin)
            self.register_agent(agent_id, to_zone, self.kinds.get(agent_id, "drone"))
            report["steps"] += ["target_ae_registered", "target_acp_created", "target_subscription_created"]
            target = self.client(to_zone, origin)
            try:
                state = source.latest(f"/{agent_id}/position")
                target.cin(f"/{agent_id}/position", dict(state, transferred_from=from_zone))
                report["steps"].append("last_state_copied")
            except CseError:
                report["steps"].append("no_source_state")
            self.set_member(from_zone, agent_id, False)
            try:
                source.call("DELETE", f"/{agent_id}/cmd/sub-cmd", allowed=(200,))
            except (CseError, ValueError):
                pass  # A DELETE response has no body; a missing subscription is fine too.
            report["steps"].append("group_and_subscription_switched")
            with self.lock:
                self.serving[agent_id] = to_zone
                pending = self.buffers.pop(agent_id, [])
                self.in_handover.discard(agent_id)
            lost = 0
            for record in pending:
                try:
                    target.cin(f"/{agent_id}/position", record)
                except (CseError, requests.RequestException):
                    lost += 1
            finished = time.monotonic()
            with self.lock:
                self.last_ok[agent_id] = finished
            report.update(ok=True, buffered_reports=len(pending), lost_reports=lost,
                          procedure_ms=round((finished - started) * 1000, 1),
                          # Time between the last report stored on the source and the first write on the target.
                          interruption_ms=round((finished - last_ok) * 1000, 1) if last_ok else None)
        except Exception as exc:
            with self.lock:
                self.in_handover.discard(agent_id)
            report.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        for zone in (from_zone, to_zone):
            try:
                self.client(zone, self.mec).cin("/rescue-mec/handover", report)
            except (CseError, requests.RequestException) as exc:
                LOG.warning("Handover record not stored on zone %s: %s", zone, exc)
        self.handovers.append(report)
        return report

    def snapshot(self):
        with self.lock:
            return {"serving": dict(self.serving), "handovers": self.handovers[-20:],
                    "notifications": len(self.notifications.items)}

    def close(self):
        for client in self.clients.values():
            client.session.close()
        self.notifications.close()


class FleetWorker:
    """Runs Fleet calls on its own thread so CSE delays never block the simulator."""

    def __init__(self, fleet):
        self.fleet, self.jobs, self.errors = fleet, queue.Queue(maxsize=2048), 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="onem2m-fleet", daemon=True)
        self.thread.start()

    def submit(self, method, *args):
        try:
            self.jobs.put_nowait((method, args))
        except queue.Full:
            self.errors += 1

    def run(self):
        while not self.stop.is_set():
            try:
                method, args = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                getattr(self.fleet, method)(*args)
            except Exception as exc:
                self.errors += 1
                LOG.warning("Fleet %s failed: %s", method, exc)

    def close(self):
        self.stop.set()
        self.thread.join(5)
        self.fleet.close()
