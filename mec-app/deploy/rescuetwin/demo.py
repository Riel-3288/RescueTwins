"""Local factory simulator + dashboard, with optional MQTT broker and ACME CSEs."""
from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import io
import json
import logging
import mimetypes
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .core import UNITS, canonical, load_config, utcnow, utc_timestamp, validate_reading
from .mqtt import Bridge, make_client
from .onem2m import Cse
from .simulator import Factory
from .rescue import Rescue, LocalExecutor, TelemetryGate

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "dashboard"
LOG = logging.getLogger(__name__)


class Demo:
    def __init__(self, config, bridge=None):
        self.config, self.bridge = config, bridge
        self.factory = Factory(config=config.get("live_factory"))
        for name,pos in config.get('rescue',{}).get('sensor_positions',{}).items():
            self.factory.sensor_positions[name]=(int(pos['x_m']/self.factory.cell_m),int(pos['y_m']/self.factory.cell_m))
        self.live_only=False
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.mqtt_ready = threading.Event()
        self.client = make_client(config["mqtt"], "rescuetwin-dashboard-" + uuid.uuid4().hex[:8])
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_subscribe = lambda *args: self.mqtt_ready.set()
        self.client.on_message = self.on_message
        self.client.on_publish = self.on_publish
        self.client.max_queued_messages_set(1000)
        self.latest, self.messages = {}, deque(maxlen=100)
        self.received_rows = deque(maxlen=10000)
        self.received = self.rejected = self.sent = self.acked = self.failed = self.cse_acks = 0
        self.connected, self.last_received, self.last_error = False, None, ""
        self.worker = None
        self.publisher = None
        self.outgoing = queue.Queue(maxsize=4096)
        self.telemetry_gate = TelemetryGate()
        self.rescue = Rescue(config, self.factory, self.publish, bridge)
        self.executor = LocalExecutor(self.factory, self.rescue, self.publish)
        from .recordings import RecordedMission
        self.recorded = RecordedMission(config)
        self.recorded.load_defaults()
        from .links import RobotTasks
        self.robot = RobotTasks(self.rescue.store)
        self.mec_context = None
        self.file_rows = None
        self.file_report = None
        if config.get('sensor_file'):
            from .__main__ import sample_plan
            self.file_report = sample_plan(config['sensor_file'],config)
            self.file_rows = [r for _,r in self.file_report['accepted']]
            LOG.warning('Recorded sensor file: %s accepted, %s incompatible; original timestamps retained',
                        len(self.file_rows),len(self.file_report['rejected']))

    def next_sensor_readings(self):
        if self.file_rows is None:
            return self.factory.readings()
        if not self.live_only and (not self.connected or not self.mqtt_ready.is_set()):
            return []
        rows,self.file_rows = self.file_rows,[]
        return rows

    def on_connect(self, client, userdata, flags, reason, properties):
        with self.lock:
            self.connected = not reason.is_failure
            self.last_error = str(reason) if reason.is_failure else ""
        if not reason.is_failure:
            client.subscribe([("rescuetwin/+/+/+", 1), ("rescuetwin-ack/#", 1),
                              ('rescuetwin-control/commands/+', 1), ('rescuetwin-control/feedback/+', 1)])

    def on_disconnect(self, client, userdata, flags, reason, properties):
        with self.lock:
            self.connected = False
        self.mqtt_ready.clear()

    def on_publish(self, client, userdata, mid, reason, properties):
        with self.lock:
            if not reason.is_failure:
                self.acked += 1

    def on_message(self, client, userdata, message):
        try:
            if len(message.payload) > 8192 or message.retain:
                raise ValueError("retained/oversized message")
            value = json.loads(message.payload.decode("utf-8"))
            if message.topic.startswith('rescuetwin-control/'):
                if not isinstance(value, dict) or message.topic.split('/')[-1] != value.get('agent_id'):
                    raise ValueError('Invalid command or feedback identity')
                with self.lock:
                    if '/commands/' in message.topic:
                        self.executor.command(value)
                    elif '/feedback/' in message.topic:
                        self.rescue.feedback(value)
                return
            if message.topic.startswith("rescuetwin-ack/"):
                if not isinstance(value, dict) or value.get("status") != "stored":
                    raise ValueError("invalid CSE acknowledgment")
                with self.lock:
                    self.cse_acks += 1
                return
            validate_reading(value, self.config["devices"], message.topic)
            with self.lock:self.accept_reading(value,message.topic)
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            with self.lock:
                self.rejected += 1

    def accept_reading(self,value,topic):
        self.received+=1;self.last_received=utcnow()
        key=(value['device_id'],value['source'],value['sensor_type'])
        if key not in self.latest or utc_timestamp(value['observed_at'])>=utc_timestamp(self.latest[key]['observed_at']):self.latest[key]=value
        self.rescue.ingest(value)
        self.messages.appendleft(dict(topic=topic,reading=value,received_at=self.last_received));self.received_rows.append(value)

    def start(self,live_only=False):
        self.live_only=live_only
        if not live_only:
            self.publisher=threading.Thread(target=self.publish_loop,name='mqtt-dashboard-publisher',daemon=True);self.publisher.start()
            self.client.connect_async(self.config['mqtt']['host'],self.config['mqtt']['port'],30);self.client.loop_start()
        self.worker=threading.Thread(target=self.run,name='factory-simulator',daemon=True);self.worker.start()

    def publish(self, topic, data):
        if self.live_only:
            if topic.startswith('rescuetwin-control/commands/'):self.executor.command(data)
            elif topic.startswith('rescuetwin-control/feedback/'):self.rescue.feedback(data)
            return True
        if not self.connected:
            return False
        # Queue writes to avoid deadlocks between Paho and the factory lock.
        try:
            self.outgoing.put_nowait((topic, data))
            return True
        except queue.Full:
            with self.lock:
                self.failed += 1
                self.last_error = 'Outgoing MQTT buffer is full'
            return False

    def publish_loop(self):
        while not self.stop.is_set():
            try:
                topic, data = self.outgoing.get(timeout=.1)
            except queue.Empty:
                continue
            result = self.client.publish(topic, canonical(data), qos=1, retain=False)
            with self.lock:
                if result.rc == 0:
                    self.sent += 1
                else:
                    self.failed += 1

    def run(self):
        deadline = time.monotonic()
        last_publish = 0
        while not self.stop.is_set():
            now = time.monotonic()
            if now < deadline:
                self.stop.wait(min(0.05, deadline - now))
                continue
            deadline = max(deadline + 0.25, now)
            with self.lock:
                self.rescue.tick()
                crossings = []
                for _ in range(self.factory.speed):
                    crossings.extend(self.factory.step())
                self.executor.tick()
                # Keep telemetry timing independent of simulation speed.
                due = now - last_publish >= 1
                readings = self.next_sensor_readings() if due else []
                positions = [{"agent_id": a["id"], "x_m": (a["x"] + 0.5) * self.factory.cell_m,
                              "y_m": (a["y"] + 0.5) * self.factory.cell_m, "zone_id": a["zone"],
                              "source": "simulation", "observed_at": utcnow()}
                             for a in self.factory.agents] if due else []
            if due:
                last_publish = now
            for reading in readings:
                backlog = self.bridge.stats()['counts'].get('pending', 0) if self.bridge else 0
                if self.live_only:
                    try:
                        validate_reading(reading,self.config['devices'])
                        with self.lock:self.accept_reading(reading,'recorded-file' if self.file_rows is not None else 'local-simulation')
                    except (ValueError,KeyError,TypeError):self.rejected+=1
                    continue
                if self.file_rows is not None or self.telemetry_gate.accept(reading, backlog):
                    self.publish(f"rescuetwin/{reading['zone_id']}/{reading['device_id']}/{reading['sensor_type']}", reading)
            for position in positions:
                self.publish("rescuetwin-sim/agents/" + position["agent_id"], position)
            for crossing in crossings:
                self.publish("rescuetwin-sim/events", crossing)

    def snapshot(self):
        with self.lock:
            value = self.factory.snapshot()
            value["recorded"] = self.recorded.snapshot()
            value["live_mode"] = self.live_only
            value["robot_tasks"] = self.robot.tasks
            value["mec_grid_context"] = self.mec_context
            value['sensor_input'] = dict(mode='recorded-file' if self.file_rows is not None else 'generated',
                file=self.config.get('sensor_file'), rejected=self.file_report['rejected'] if self.file_report else [],
                note='File input preserves source/time; stale records do not assert an online physical sensor.')
            value["mqtt"] = {"connected": self.connected, "host": self.config["mqtt"]["host"],
                             "port": self.config["mqtt"]["port"], "sent": self.sent, "puback": self.acked,
                             "received": self.received, "rejected": self.rejected, "failed": self.failed,
                             "last_received": self.last_received, "error": self.last_error}
            value["readings"] = list(self.latest.values())
            value["messages"] = list(self.messages)
            value['rescue'] = self.rescue.snapshot()
            value['mqtt']['simulator_suppressed'] = self.telemetry_gate.skipped
            stats = self.bridge.stats() if self.bridge else {}
            value["cse"] = {"enabled": self.bridge is not None, "stored_ack_messages": self.cse_acks,
                            **stats,
                            "counts": stats.get('counts', {}),
                            "endpoints": {z: e["base_url"] for z, e in self.config["cse"].items() if z in ("A", "B", "IN")}}
            return value

    def control(self, data):
        if data.get('action') == 'mec_snapshot':
            from .links import mapped_locations
            self.mec_context = mapped_locations(self.config)
        elif data.get('action') == 'robot_simulate':
            suggestions = self.recorded.snapshot()['robot_task_suggestions']
            suggestion = next((t for t in suggestions if t['task_id'] == data.get('suggestion_id')), None)
            if suggestion is None: raise ValueError('Suggestion is unavailable at this replay step')
            self.robot.simulate(suggestion,self.recorded.frame(),self.recorded.grid)
        elif data.get('action') == 'robot_feedback':
            self.robot.feedback(data.get('feedback',{}))
        elif data.get('action') == 'recorded_seek':
            self.robot.invalidate('Replay step changed')
            self.recorded.seek(data.get('step'))
        elif data.get('action') == 'drone_plan':
            self.recorded.plan(data.get('drone_id'), data.get('goal'), data.get('battery', 100))
        elif data.get('action') == 'drone_simulate':
            self.recorded.simulate(data.get('drone_id'), data.get('plan_id'))
        elif data.get('action') == 'resume_swarm':
            for agent in self.factory.agents:self.rescue.cancel_agent(agent['id'],'Operator resumed autonomous exploration')
            self.factory.command(data)
        elif data.get('action') == 'ack_incident':
            self.rescue.acknowledge(data.get('incident_id'))
        elif data.get('action') == 'retry_incident':
            self.rescue.retry(data.get('incident_id'))
        else:
            if data.get('action') in ('reset', 'demo', 'rescue_demo'):
                for agent in self.factory.agents:
                    self.rescue.cancel_agent(agent['id'], 'Scenario restarted by operator')
                self.rescue.reset_simulation()
                self.telemetry_gate.previous.clear()
            elif data.get('action') == 'goal':
                # Reject invalid goals before cancelling an active task.
                x, y = data.get('x'), data.get('y')
                if type(x) is not int or type(y) is not int or not self.factory.inside(x,y) or (x,y) in self.factory.walls or self.factory.unsafe(x,y):
                    raise ValueError('Goal is not walkable')
                self.rescue.cancel_agent(data.get('agent_id'), 'Manual goal selected by operator')
            self.factory.command(data)
            self.factory.set_observed_hazards(self.rescue.hazards())

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join(5)
        if self.publisher:
            self.publisher.join(5)
        self.client.disconnect()
        self.client.loop_stop()


def make_server(demo, port, host="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def valid_host(self):
            try:
                host = urlparse("http://" + self.headers.get("Host", "")).hostname
                return host in ("127.0.0.1", "localhost", "::1")
            except ValueError:
                return False

        def send(self, data, status=200, content_type="application/json; charset=utf-8", attachment=None):
            if not isinstance(data, bytes):
                data = canonical(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.valid_host():
                return self.send({"error": "invalid host"}, 403)
            path = urlparse(self.path).path
            if path == "/api/state":
                return self.send(demo.snapshot())
            if path == "/api/recorded/export.json":
                with demo.lock:
                    return self.send(demo.recorded.snapshot(), attachment="rescuetwin-recorded-mission.json")
            if path == "/api/export.json":
                return self.send(demo.snapshot(), attachment="rescuetwin-snapshot.json")
            if path == "/api/readings.csv":
                output = io.StringIO(newline="")
                fields = ["device_id", "zone_id", "sensor_type", "value", "unit", "source", "quality", "observed_at"]
                writer = csv.DictWriter(output, fieldnames=fields)
                writer.writeheader()
                with demo.lock:
                    writer.writerows(list(demo.received_rows))
                return self.send(output.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8", attachment="rescuetwin-readings.csv")
            files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}
            if path not in files:
                return self.send({"error": "not found"}, 404)
            file = STATIC / files[path]
            return self.send(file.read_bytes(), content_type=(mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8")

        def do_POST(self):
            if not self.valid_host():
                return self.send({"error": "invalid host"}, 403)
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                return self.send({"error": "cross-origin command rejected"}, 403)
            path = urlparse(self.path).path
            if path.startswith("/api/upload/"):
                try:
                    kind = path.rsplit("/", 1)[-1]
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4 * 1024 * 1024:
                        raise ValueError("Upload must contain 1 byte to 4 MiB")
                    raw = self.rfile.read(length)
                    if len(raw) != length: raise ValueError("Incomplete upload")
                    with demo.lock:
                        result = demo.recorded.upload(kind, raw)
                        demo.robot.invalidate("Recorded input changed")
                    return self.send(result)
                except (ValueError, TypeError, KeyError, UnicodeDecodeError, OverflowError) as exc:
                    return self.send({"error": str(exc)}, 400)
            if path != "/api/control":
                return self.send({"error": "not found"}, 404)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send({"error": "Content-Type must be application/json"}, 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("invalid body length")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("expected object")
                with demo.lock:
                    demo.control(data)
                self.send({"ok": True})
            except (ValueError, TypeError, KeyError, __import__("requests").RequestException) as exc:
                self.send({"error": str(exc)}, 400)

    return ThreadingHTTPServer((host, port), Handler)


class LocalBroker:
    def __init__(self, port):
        self.port = port
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def start(self):
        from amqtt.broker import Broker
        self.thread.start()
        async def boot():
            self.broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}"}},
                                  "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
            await self.broker.start()
        asyncio.run_coroutine_threadsafe(boot(), self.loop).result(15)

    def close(self):
        if hasattr(self, "broker") and self.thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(self.broker.shutdown(), self.loop).result(10)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(5)


class LocalCses:
    def __init__(self, config, directory):
        self.config, self.directory = config, directory
        self.processes, self.logs = [], []

    def start(self):
        from tools.acme_config import render
        from .__main__ import provision
        for zone, kind, cse_id, name in (("IN", "IN", "id-in", "cse-in"), ("A", "MN", "id-mn-a", "cse-mn-a"), ("B", "MN", "id-mn-b", "cse-mn-b")):
            endpoint = self.config["cse"][zone]
            port = urlparse(endpoint["base_url"]).port
            require_free_port(port)
            folder = render(self.directory / zone, kind, cse_id, name, port)
            ini = folder / "acme.ini"
            ini.write_text(ini.read_text(encoding="utf-8").replace("networkInterface=0.0.0.0", "networkInterface=127.0.0.1"), encoding="utf-8")
            log = open(self.directory / f"{zone}.log", "a", encoding="utf-8")
            self.logs.append(log)
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            self.processes.append(subprocess.Popen([sys.executable, "-X", "utf8", "-m", "tools.local_cse", "--base-directory", str(folder), "--headless"],
                                                   stdin=subprocess.PIPE, text=True, stdout=log, stderr=log, cwd=ROOT, **options))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.processes[-1].poll() is not None:
                    raise RuntimeError(f"CSE {zone} startup failed (exit {self.processes[-1].returncode}); see {folder / 'logs'} and {self.directory / (zone + '.log')}")
                try:
                    Cse(dict(endpoint, timeout_s=0.5), "CAdmin").call("GET")
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                raise RuntimeError(f"CSE {zone} did not become ready")
        provision(self.config)

    def close(self):
        for process in self.processes:
            if process.poll() is None and process.stdin:
                try:
                    process.stdin.write("shutdown\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            if process.stdin:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
        for process in self.processes:
            try:
                process.wait(20)
            except subprocess.TimeoutExpired:
                LOG.warning("CSE process %s did not finish graceful shutdown; forcing exit", process.pid)
                process.kill()
                process.wait()
        for log in self.logs:
            log.close()


def require_free_port(port):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Port {port} is already in use. Stop the earlier demo, or omit --local-broker to use your existing broker.") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1", help="Bind address; containers need 0.0.0.0")
    parser.add_argument("--local-broker", action="store_true")
    parser.add_argument('--live-swarm',action='store_true',help='Run the live Fusion swarm dashboard without MQTT')
    parser.add_argument('--drone-count',type=int,choices=range(2,17))
    parser.add_argument('--swarm-url',help='Optional separate Fusion HTTP service; default is embedded Fusion')
    parser.add_argument("--recorded-only", action="store_true", help="Offline files only; no MQTT or CSE processes")
    parser.add_argument("--with-cse", action="store_true", help="Launch isolated local ACME IN/A/B and bridge")
    parser.add_argument("--bridge", action="store_true", help="Connect bridge to CSE endpoints already running in config")
    from .__main__ import DEFAULT_SAMPLES
    parser.add_argument('--sensor-file',type=Path,default=DEFAULT_SAMPLES)
    parser.add_argument('--generated-sensors',action='store_true',help='Explicit opt-in to illustrative generated sensor readings')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = copy.deepcopy(load_config(args.config))
    cfg.setdefault('live_factory',{})
    if args.drone_count:cfg['live_factory']['drone_count']=args.drone_count
    if args.swarm_url:cfg['live_factory']['swarm_url']=args.swarm_url
    if args.live_swarm and (args.recorded_only or args.local_broker or args.with_cse or args.bridge):parser.error('--live-swarm is independent of MQTT/CSE/replay-only modes')
    if args.recorded_only and (args.local_broker or args.with_cse or args.bridge):
        parser.error("--recorded-only does not start MQTT or CSE components")
    if (args.bridge or args.with_cse) and not cfg.get("allow_cse_writes", True):
        parser.error("Team profile is read-only; start without --bridge/--with-cse for offline review")
    if not args.generated_sensors:
        cfg['sensor_file'] = str(args.sensor_file.resolve())
    runtime = ROOT / "runtime" / "demo"
    runtime.mkdir(parents=True, exist_ok=True)
    cfg["state_file"] = str(runtime / "bridge.sqlite")
    if args.local_broker and (cfg["mqtt"]["host"] != "127.0.0.1" or cfg["mqtt"].get("ca_file")):
        parser.error("--local-broker supports loopback non-TLS development config only")
    if args.with_cse:
        expected = {"A": "http://127.0.0.1:18001/~/id-mn-a/cse-mn-a", "B": "http://127.0.0.1:18002/~/id-mn-b/cse-mn-b", "IN": "http://127.0.0.1:18000/~/id-in/cse-in"}
        if cfg.get("require_tls", True) or any(cfg["cse"][z]["base_url"] != url for z, url in expected.items()):
            parser.error("--with-cse requires the default local A/B/IN endpoints; use --bridge for existing CSEs")
    broker = LocalBroker(cfg["mqtt"]["port"]) if args.local_broker else None
    cses = LocalCses(cfg, runtime) if args.with_cse else None
    bridge = worker = demo = server = None
    require_free_port(args.port)
    try:
        if broker:
            require_free_port(cfg["mqtt"]["port"])
            broker.start()
        if cses:
            print("Starting local ACME CSEs; please wait...", flush=True)
            cses.start()
        if args.with_cse or args.bridge:
            bridge = Bridge(cfg)
            worker = threading.Thread(target=bridge.run, name="mqtt-cse-bridge", daemon=True)
            worker.start()
        demo = Demo(cfg, bridge)
        if not args.recorded_only:
            demo.start(live_only=args.live_swarm)
            if args.live_swarm:demo.factory.command({"action":"resume"})
        server = make_server(demo, args.port, args.host)
        print(f"Dashboard: http://127.0.0.1:{args.port}", flush=True)
        if args.live_swarm:
            print("Live swarm: eight configurable drones; Fusion map sharing and autonomous exploration.",flush=True)
        elif args.recorded_only:
            print("Recorded Mission: offline files; no MQTT broker, bridge, or CSE writes. Open /#recorded.", flush=True)
        else:
            print(f"Local demo MQTT: mqtt://{cfg['mqtt']['host']}:{cfg['mqtt']['port']} (isolated example)", flush=True)
        print("Ctrl+C stops this demo. Sensor input: recorded file once (unless --generated-sensors was explicitly selected).", flush=True)
        signal.signal(signal.SIGINT, lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
        server.serve_forever(poll_interval=0.25)
    finally:
        if server:
            server.server_close()
        if demo:
            demo.close()
        if bridge:
            bridge.stop.set()
            worker.join(12)
        if cses:
            cses.close()
        if broker:
            broker.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
