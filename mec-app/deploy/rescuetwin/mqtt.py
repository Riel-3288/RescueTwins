from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import uuid
import queue
from collections import deque
from contextlib import contextmanager

import paho.mqtt.client as mqtt
import requests

from .core import Store, canonical, validate_reading
from .onem2m import Cse, CseError

LOG = logging.getLogger(__name__)


@contextmanager
def bridge_owner(path):
    """Allow only one bridge to replay this outbox."""
    handle = open(str(path) + '.lock', 'a+b')
    try:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            if not handle.read(1):
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError('Another bridge already owns this outbox. Stop the earlier demo first.') from exc
    try:
        yield
    finally:
        handle.close()


def native_cin(config, originator, cse_id, target, reading):
    """Create a CIN through oneM2M MQTT binding."""
    request_id = uuid.uuid4().hex
    client = make_client(config, "native-" + originator + "-" + uuid.uuid4().hex[:12], clean_session=True)
    ready, reply = threading.Event(), threading.Event()
    responses = []
    response_topic = f"/oneM2M/resp/{originator}/{cse_id.strip('/')}/json"
    def on_message(c, userdata, msg):
        try:
            if msg.topic != response_topic or msg.retain or len(msg.payload) > 65536:
                return
            value = json.loads(msg.payload)
            if isinstance(value, dict) and value.get("rqi") == request_id:
                responses.append(value)
                reply.set()
        except (ValueError, TypeError, UnicodeDecodeError):
            return
    client.on_message = on_message
    subscription_failed = []
    def subscribed(c, userdata, mid, reasons, properties):
        subscription_failed.extend(r for r in reasons if r.is_failure)
        ready.set()
    client.on_subscribe = subscribed
    try:
        connect(client, config)
        client.subscribe(response_topic, qos=1)
        if not ready.wait(5) or subscription_failed:
            raise RuntimeError("native MQTT response subscription failed or timed out")
        primitive = {"op": 1, "fr": originator, "to": target, "rqi": request_id, "rvi": "4", "ty": 4,
                     "pc": {"m2m:cin": {"con": canonical(reading), "cnf": "application/json:0"}}}
        result = client.publish(f"/oneM2M/req/{originator}/{cse_id.strip('/')}/json", canonical(primitive), qos=1, retain=False)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("native MQTT request could not be queued")
        if not reply.wait(10):
            raise RuntimeError("native MQTT CSE response timed out")
        response = responses[0]
        if type(response.get("rsc")) is not int or response["rsc"] != 2001:
            raise RuntimeError(f"native MQTT CSE returned {response.get('rsc')}")
        return response
    finally:
        client.disconnect()
        client.loop_stop()


def make_client(config, client_id, manual_ack=False, clean_session=False):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                         clean_session=clean_session, manual_ack=manual_ack)
    if config.get("ca_file"):
        context = ssl.create_default_context(cafile=config["ca_file"])
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if config.get("cert_file"):
            context.load_cert_chain(config["cert_file"], config["key_file"])
        client.tls_set_context(context)
    if config.get("username"):
        client.username_pw_set(config["username"], os.getenv(config.get("password_env", "MQTT_PASSWORD")))
    client.reconnect_delay_set(1, 30)
    return client


def connect(client, config):
    ready = threading.Event()
    result = []
    def on_connect(c, userdata, flags, reason, properties):
        result.append(reason.is_failure)
        ready.set()
    client.on_connect = on_connect
    client.connect(config["host"], config["port"], 30)
    client.loop_start()
    if not ready.wait(10) or result[0]:
        client.disconnect()
        client.loop_stop()
        raise RuntimeError("MQTT connection was not accepted")


def publish(config, reading, device_config):
    validate_reading(reading, device_config)
    client = make_client(config, "publisher-" + reading["device_id"])
    try:
        connect(client, config)
        topic = f"rescuetwin/{reading['zone_id']}/{reading['device_id']}/{reading['sensor_type']}"
        result = client.publish(topic, canonical(reading), qos=1, retain=False)
        result.wait_for_publish(10)
        if not result.is_published():
            raise RuntimeError("MQTT publish acknowledgement timed out")
    finally:
        client.disconnect()
        client.loop_stop()


class Bridge:
    def __init__(self, config, store=None):
        if not config.get("allow_cse_writes", True):
            raise ValueError("Team profile disables the raw-topic HTTP bridge; team broker uses oneM2M binding")
        self.config = config
        self.store = store or Store(config["state_file"])
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.client = make_client(config["mqtt"], config["mqtt"].get("client_id", "rescuetwin-bridge"), True)
        self.client.max_queued_messages_set(4096)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_subscribe = lambda *args: self.ready.set()
        self.clients = {}
        self.incoming = queue.Queue(maxsize=2048)
        self.samples = {'accepted': deque(maxlen=20000), 'delivered': deque(maxlen=20000)}
        self.metrics_lock = threading.Lock()
        self.cached_stats = {'counts': {}, 'oldest_pending_s': 0, 'last_error': '', 'input_per_s': 0, 'stored_per_s': 0}

    def on_connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            LOG.error("MQTT connection rejected: %s", reason)
            return
        client.subscribe("rescuetwin/+/+/+", qos=1)

    def on_message(self, client, userdata, msg):
        try:
            if msg.retain or len(msg.payload) > 8192:
                raise ValueError("retained or oversized telemetry is not accepted")
            reading = json.loads(msg.payload.decode("utf-8"))
            validate_reading(reading, self.config["devices"], msg.topic)
            self.incoming.put_nowait((reading, msg.mid, msg.qos))
            return  # Acknowledge only after the SQLite commit.
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            # Acknowledge invalid messages to avoid endless redelivery.
            count = self.store.get("rejected", 0)
            self.store.set("rejected", count + 1)
            LOG.warning("Rejected telemetry on topic %s", msg.topic)
        except Exception:
            LOG.exception("Cannot durably store MQTT message; leaving it unacknowledged")
            client.disconnect()
            return
        if msg.qos:
            client.ack(msg.mid, msg.qos)

    def ingest(self):
        while not self.stop.is_set() or not self.incoming.empty():
            batch = []
            try:
                batch.append(self.incoming.get(timeout=0.1))
            except queue.Empty:
                continue
            deadline = time.monotonic() + 0.025
            while len(batch) < 64 and time.monotonic() < deadline:
                try:
                    batch.append(self.incoming.get(timeout=max(.001, deadline-time.monotonic())))
                except queue.Empty:
                    break
            try:
                self.store.enqueue_many([r for r, _, _ in batch])
                with self.metrics_lock:
                    self.samples['accepted'].extend([time.monotonic()] * len(batch))
                for _, mid, qos in batch:
                    if qos:
                        self.client.ack(mid, qos)
            except Exception:
                LOG.exception('Cannot commit ingestion batch; MQTT messages remain unacknowledged')
                self.client.disconnect()
                self.stop.set()

    def stats(self):
        with self.metrics_lock:
            return dict(self.cached_stats, counts=dict(self.cached_stats['counts']))

    def monitor(self):
        while not self.stop.is_set():
            stats = self.store.queue_stats()
            now = time.monotonic()
            with self.metrics_lock:
                for name, values in self.samples.items():
                    while values and values[0] < now - 10:
                        values.popleft()
                    stats['input_per_s' if name == 'accepted' else 'stored_per_s'] = round(len(values)/10, 2)
                stats['ingest_buffer'] = self.incoming.qsize()
                self.cached_stats = stats
            self.stop.wait(2)

    def deliver_batch(self, zone, limit=16):
        batch = self.store.claim_batch(zone, limit)
        outcomes, acks = [], []
        for key, reading, attempts in batch:
            if self.stop.is_set():
                break
            device = reading.get('device_id', 'rescue-control')
            client_key = (zone, device)
            if client_key not in self.clients:
                originator = 'CRescueControl' if reading.get('_integration_event') else self.config['devices'][device]['originator']
                self.clients[client_key] = Cse(self.config['cse'][zone], originator)
            try:
                if reading.get('_integration_event'):
                    self.clients[client_key].cin('/rescue-control/' + reading['container'], reading['value'], key)
                else:
                    self.clients[client_key].cin(f"/{device}/{reading['sensor_type']}", reading, key)
                outcomes.append((key, 0, None, False))
                acks.append((key, device))
            except (requests.RequestException, CseError, ValueError) as exc:
                permanent = isinstance(exc, ValueError) or (isinstance(exc, CseError) and exc.status in (400, 401, 403))
                outcomes.append((key, attempts + 1, str(exc), permanent))
                LOG.warning('CSE %s delivery pending/quarantined: %s', zone, exc)
        if outcomes:
            self.store.finish_batch(outcomes)
            with self.metrics_lock:
                self.samples['delivered'].extend([time.monotonic()] * len(acks))
            for key, device in acks:
                self.client.publish(f'rescuetwin-ack/{zone}/{device}', canonical({'record_id': key, 'status': 'stored'}), qos=1)
        return bool(batch)

    def deliver_zone(self, zone):
        try:
            while not self.stop.is_set():
                if not self.deliver_batch(zone):
                    self.stop.wait(.05)
        except Exception:
            LOG.exception('CSE delivery worker failed')
            self.stop.set()

    def drain_one(self):
        return self.deliver_batch('A', 1) or self.deliver_batch('B', 1)

    def run(self):
        with bridge_owner(self.store.path):
            self.store.recover_processing()
            cfg = self.config['mqtt']
            workers = [threading.Thread(target=target, args=args, daemon=True)
                       for target, args in [(self.ingest, ()), (self.monitor, ()),
                                            (self.deliver_zone, ('A',)), (self.deliver_zone, ('B',))]]
            for worker in workers:
                worker.start()
            self.client.connect_async(cfg['host'], cfg['port'], 30)
            self.client.loop_start()
            try:
                self.stop.wait()
            finally:
                self.client.disconnect()
                self.client.loop_stop()
                self.stop.set()
                for worker in workers:
                    worker.join(20)
                self.store.recover_processing()
                for client in self.clients.values():
                    client.session.close()
