import asyncio
import json
import os
import ssl
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from amqtt.broker import Broker
from rescuetwin.mqtt import make_client, connect, publish, Bridge
from rescuetwin.core import load_config, utcnow
from rescuetwin.onem2m import Cse, CseError
from tools.certificates import generate
from tools.acme_config import render
from conftest import free_port, ROOT


def test_verified_mqtt_mutual_tls_and_https_cse(tmp_path, monkeypatch):
    certs = generate(tmp_path / "certs")
    bad_certs = generate(tmp_path / "wrong-ca")
    port, cse_port = free_port(), free_port()
    class MutualTlsBroker(Broker):
        @staticmethod
        def _create_ssl_context(listener):
            context = Broker._create_ssl_context(listener)
            context.verify_mode = ssl.CERT_REQUIRED
            return context
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    async def start():
        broker = MutualTlsBroker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}", "ssl": True,
               "certfile": str(certs/"broker.crt"), "keyfile": str(certs/"broker.key"), "cafile": str(certs/"ca.crt")}},
               "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
        await broker.start()
        return broker
    broker = asyncio.run_coroutine_threadsafe(start(), loop).result(10)
    tls_dir = tmp_path / "cse-certs"
    tls_dir.mkdir()
    (tls_dir/"tls.crt").write_bytes((certs/"cse-a.crt").read_bytes())
    (tls_dir/"tls.key").write_bytes((certs/"cse-a.key").read_bytes())
    token = "integration-test-token-" + os.urandom(8).hex()
    directory = render(tmp_path/"cse", "MN", "id-mn-a", "cse-mn-a", cse_port, tls=True, certificate_dir=tls_dir, token=token)
    endpoint = {"base_url": f"https://127.0.0.1:{cse_port}/~/id-mn-a/cse-mn-a", "ca_file": str(certs/"ca.crt"), "token_env": "TEST_CSE_TOKEN"}
    monkeypatch.setenv("TEST_CSE_TOKEN", token)
    log = open(tmp_path/"acme.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "acmecse", "--base-directory", str(directory)], stdout=log, stderr=log)
    bridge = None
    try:
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError((tmp_path/"acme.log").read_text())
            try:
                Cse(endpoint, "CAdmin").call("GET")
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)
        else:
            raise RuntimeError(f"HTTPS CSE startup timed out: {last_error}")
        Cse(endpoint, "CNode01").ensure_ae("node-01", ["temperature"])
        wrong = dict(endpoint, ca_file=str(bad_certs/"ca.crt"))
        with pytest.raises(requests.exceptions.SSLError):
            Cse(wrong, "CNode01").call("GET", "/node-01")
        monkeypatch.delenv("TEST_CSE_TOKEN")
        with pytest.raises(CseError) as exc:
            Cse(endpoint, "CNode01").call("GET", "/node-01")
        assert exc.value.status == 401
        monkeypatch.setenv("TEST_CSE_TOKEN", token)
        cfg = load_config(ROOT/"config.example.json")
        cfg["cse"]["A"] = endpoint
        cfg["state_file"] = str(tmp_path/"outbox.sqlite")
        cfg["mqtt"].update(port=port, ca_file=str(certs/"ca.crt"), cert_file=str(certs/"bridge.crt"), key_file=str(certs/"bridge.key"))
        bridge = Bridge(cfg)
        worker = threading.Thread(target=bridge.run, daemon=True)
        worker.start()
        assert bridge.ready.wait(5)
        value = {"device_id": "node-01", "zone_id": "A", "sensor_type": "temperature", "value": 28.6, "unit": "degC", "source": "simulation", "quality": "valid", "observed_at": utcnow()}
        node_cfg = dict(cfg["mqtt"], cert_file=str(certs/"node-01.crt"), key_file=str(certs/"node-01.key"))
        publish(node_cfg, value, cfg["devices"])
        deadline = time.monotonic()+10
        while time.monotonic() < deadline and not bridge.store.counts().get("delivered"):
            time.sleep(0.1)
        assert Cse(endpoint, "CNode01").latest("/node-01/temperature") == value
        client = make_client(dict(node_cfg, ca_file=str(bad_certs/"ca.crt")), "wrong-ca")
        with pytest.raises(ssl.SSLError):
            connect(client, dict(node_cfg, ca_file=str(bad_certs/"ca.crt")))
        no_identity = ssl.create_default_context(cafile=str(certs/"ca.crt"))
        no_identity.maximum_version = ssl.TLSVersion.TLSv1_2
        with no_identity.wrap_socket(socket.socket(), server_hostname="localhost") as sock:
            sock.settimeout(3)
            with pytest.raises(ssl.SSLError):
                sock.connect(("127.0.0.1", port))
    finally:
        if bridge:
            bridge.stop.set()
            worker.join(10)
        proc.terminate()
        proc.wait(10)
        log.close()
        asyncio.run_coroutine_threadsafe(broker.shutdown(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
