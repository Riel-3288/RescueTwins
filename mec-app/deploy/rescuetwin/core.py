from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

UNITS = {"temperature": "degC", "humidity": "percent", "pressure": "hPa",
         "smoke_raw": "adc", "co_raw": "adc", "presence": "boolean"}
FIELDS = {"device_id", "zone_id", "sensor_type", "value", "unit", "source", "quality", "observed_at"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def utc_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("observed_at must be a UTC timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("observed_at must include UTC Z or +00:00")
    return result.timestamp()


def validate_reading(value, devices, topic=None):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("reading must contain exactly the eight agreed fields")
    device, zone, sensor = value["device_id"], value["zone_id"], value["sensor_type"]
    if not isinstance(device, str) or device not in devices or not NAME.fullmatch(device):
        raise ValueError("unregistered device_id")
    if zone not in ("A", "B") or zone not in devices[device]["zones"]:
        raise ValueError("device is not assigned to this zone")
    if sensor not in UNITS or value["unit"] != UNITS[sensor]:
        raise ValueError("sensor_type and unit do not match the agreed contract")
    if value["source"] not in ("physical", "simulation") or value["quality"] not in ("valid", "invalid"):
        raise ValueError("invalid source or quality")
    if topic is not None and topic != f"rescuetwin/{zone}/{device}/{sensor}":
        raise ValueError("MQTT topic and reading identity differ")
    utc_timestamp(value["observed_at"])
    number = value["value"]
    if number is None and value["quality"] == "invalid":
        return value
    if sensor == "presence":
        if type(number) is not bool:
            raise ValueError("presence.value must be a JSON boolean")
    elif type(number) not in (int, float) or not math.isfinite(number):
        raise ValueError("value must be a finite number")
    elif value["quality"] == "valid":
        # These are format checks, not calibrated hazard thresholds.
        if sensor == "humidity" and not 0 <= number <= 100:
            raise ValueError("humidity must be between 0 and 100 percent")
        if sensor in ("smoke_raw", "co_raw") and (type(number) is not int or not 0 <= number <= 4095):
            raise ValueError("ESP32 ADC value must be an integer in 0..4095")
        if sensor == "pressure" and number <= 0:
            raise ValueError("pressure must be positive")
    return value


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    secure = cfg.get("require_tls", True)
    for endpoint in cfg["cse"].values():
        parsed = urlparse(endpoint["base_url"])
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username:
            raise ValueError("invalid CSE base_url")
        if secure and parsed.scheme != "https":
            raise ValueError("require_tls=true requires HTTPS CSE endpoints")
    mqtt = cfg["mqtt"]
    if secure and not mqtt.get("ca_file"):
        raise ValueError("require_tls=true requires MQTT ca_file")
    for name, device in cfg["devices"].items():
        if not NAME.fullmatch(name) or not device["originator"].startswith("C"):
            raise ValueError("invalid device registration mapping")
    return cfg


class Store:
    """Durable outbox; QoS 1 is acknowledged only after the SQLite commit."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS outbox (
              id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
              received REAL NOT NULL, delivered REAL, error TEXT);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def enqueue(self, reading):
        key = digest(reading)
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO outbox(id,body,received) VALUES(?,?,?)",
                            (key, canonical(reading), time.time()))
        return key

    def pending(self):
        with self.lock:
            row = self.db.execute("SELECT id,body,attempts FROM outbox WHERE status='pending' AND due<=? ORDER BY received LIMIT 1", (time.time(),)).fetchone()
        return (row[0], json.loads(row[1]), row[2]) if row else None

    def delivered(self, key):
        with self.lock, self.db:
            self.db.execute("UPDATE outbox SET status='delivered', delivered=?,error=NULL WHERE id=?", (time.time(), key))

    def failed(self, key, attempts, message, permanent=False):
        with self.lock, self.db:
            self.db.execute("UPDATE outbox SET attempts=?,due=?,error=?,status=? WHERE id=?",
                            (attempts, time.time() + min(60, 2 ** min(attempts, 6)), message[:300],
                             "quarantined" if permanent else "pending", key))

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, canonical(value)))

    def counts(self):
        with self.lock:
            return dict(self.db.execute("SELECT status,COUNT(*) FROM outbox GROUP BY status"))


class Geofence:
    def __init__(self, config):
        self.config = config

    def update(self, state, x, y, observed_at):
        x, y = float(x), float(y)
        if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= self.config["width_m"] and 0 <= y <= self.config["height_m"]):
            raise ValueError("location outside configured factory")
        timestamp = utc_timestamp(observed_at)
        if state and timestamp <= state["timestamp"]:
            return state, None
        boundary = self.config["boundary_x_m"]
        candidate = "A" if x < boundary else "B"
        if not state:
            return {"zone": candidate, "candidate": candidate, "count": 0, "timestamp": timestamp}, None
        result = dict(state, timestamp=timestamp)
        margin = self.config.get("hysteresis_m", 2)
        deep = x >= boundary + margin if candidate == "B" else x <= boundary - margin
        if candidate == state["zone"] or not deep:
            result.update(candidate=state["zone"], count=0)
            return result, None
        result["count"] = state["count"] + 1 if state["candidate"] == candidate else 1
        result["candidate"] = candidate
        if result["count"] < self.config.get("confirm_samples", 2):
            return result, None
        result.update(zone=candidate, count=0)
        return result, {"from_zone": state["zone"], "to_zone": candidate,
                        "observed_at": observed_at, "trigger": "factory_geofence"}

    def xy(self, latitude, longitude):
        lat0, lon0 = self.config["origin_lat"], self.config["origin_lon"]
        return ((longitude - lon0) * 111320 * math.cos(math.radians(lat0)),
                (latitude - lat0) * 111320)
