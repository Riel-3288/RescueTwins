from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import requests

from .core import Store, canonical, validate_reading
from .onem2m import Cse, CseError

LOG = logging.getLogger(__name__)


def native_cin(config, originator, cse_id, target, reading):
    """oneM2M MQTT binding (TS-0010); distinct from raw MQTT + HTTP bridge."""
    request_id = uuid.uuid4().hex
    client = make_client(config, "native-" + originator)
    ready, reply = threading.Event(), threading.Event()
    responses = []
    response_topic = f"/oneM2M/resp/{originator}/{cse_id.strip('/')}/json"
    def on_message(c, userdata, msg):
        value = json.loads(msg.payload)
        if value.get("rqi") == request_id:
            responses.append(value)
            reply.set()
    client.on_message = on_message
    client.on_subscribe = lambda *args: ready.set()
    try:
        connect(client, config)
        client.subscribe(response_topic, qos=1)
        if not ready.wait(5):
            raise RuntimeError("native MQTT response subscription timed out")
        primitive = {"op": 1, "fr": originator, "to": target, "rqi": request_id, "rvi": "4", "ty": 4,
                     "pc": {"m2m:cin": {"con": canonical(reading), "cnf": "application/json:0"}}}
        client.publish(f"/oneM2M/req/{originator}/{cse_id.strip('/')}/json", canonical(primitive), qos=1)
        if not reply.wait(10):
            raise RuntimeError("native MQTT CSE response timed out")
        response = responses[0]
        if not 2000 <= response.get("rsc", 0) < 3000:
            raise RuntimeError(f"native MQTT CSE returned {response.get('rsc')}")
        return response
    finally:
        client.disconnect()
        client.loop_stop()


def make_client(config, client_id, manual_ack=False):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                         clean_session=False, manual_ack=manual_ack)
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
        self.config = config
        self.store = store or Store(config["state_file"])
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.client = make_client(config["mqtt"], config["mqtt"].get("client_id", "rescuetwin-bridge"), True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_subscribe = lambda *args: self.ready.set()
        self.clients = {}

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
            self.store.enqueue(reading)
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            # Malformed messages are poison messages; acknowledge and count them.
            count = self.store.get("rejected", 0)
            self.store.set("rejected", count + 1)
            LOG.warning("Rejected telemetry on topic %s", msg.topic)
        except Exception:
            LOG.exception("Cannot durably store MQTT message; leaving it unacknowledged")
            client.disconnect()
            return
        if msg.qos:
            client.ack(msg.mid, msg.qos)

    def drain_one(self):
        item = self.store.pending()
        if item is None:
            return False
        key, reading, attempts = item
        zone, device = reading["zone_id"], reading["device_id"]
        client_key = (zone, device)
        if client_key not in self.clients:
            self.clients[client_key] = Cse(self.config["cse"][zone], self.config["devices"][device]["originator"])
        try:
            self.clients[client_key].cin(f"/{device}/{reading['sensor_type']}", reading, key)
            self.store.delivered(key)
            self.client.publish(f"rescuetwin-ack/{zone}/{device}", canonical({"record_id": key, "status": "stored"}), qos=1)
        except (requests.RequestException, CseError, ValueError) as exc:
            permanent = isinstance(exc, ValueError) or (isinstance(exc, CseError) and exc.status in (400, 401, 403))
            self.store.failed(key, attempts + 1, str(exc), permanent)
            LOG.warning("CSE delivery pending/quarantined: %s", exc)
        return True

    def run(self):
        cfg = self.config["mqtt"]
        self.client.connect_async(cfg["host"], cfg["port"], 30)
        self.client.loop_start()
        try:
            while not self.stop.is_set():
                if not self.drain_one():
                    self.stop.wait(0.1)
        finally:
            self.client.disconnect()
            self.client.loop_stop()
