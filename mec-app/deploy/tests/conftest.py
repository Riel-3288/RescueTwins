import asyncio
import copy
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    if not importlib.util.find_spec("acmecse") or not importlib.util.find_spec("amqtt"):
        pytest.skip("install requirements-test.txt for real MQTT/CSE integration tests")
    from amqtt.broker import Broker
    from tools.acme_config import render
    from rescuetwin.core import load_config
    from rescuetwin.onem2m import Cse
    from rescuetwin.__main__ import provision

    folder = tmp_path_factory.mktemp("live")
    broker_port = free_port()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    async def start_broker():
        broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{broker_port}"}},
                         "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
        await broker.start()
        return broker
    broker = asyncio.run_coroutine_threadsafe(start_broker(), loop).result(15)
    cfg = load_config(ROOT / "config.example.json")
    cfg["mqtt"]["port"] = broker_port
    cfg["state_file"] = str(folder / "bridge.sqlite")
    cfg["ipe_state_file"] = str(folder / "ipe.sqlite")
    processes, logs = [], []
    in_port = None
    try:
        for zone, kind, cse_id, name in [("IN", "IN", "id-in", "cse-in"), ("A", "MN", "id-mn-a", "cse-mn-a"), ("B", "MN", "id-mn-b", "cse-mn-b")]:
            port = free_port()
            directory = render(folder / zone, kind, cse_id, name, port,
                               broker_host="127.0.0.1" if zone == "A" else None, broker_port=broker_port,
                               remote=True, registrar_port=in_port or 18000)
            if zone == "IN":
                in_port = port
            cfg["cse"][zone]["base_url"] = f"http://127.0.0.1:{port}/~/{cse_id}/{name}"
            log = open(folder / (zone + ".log"), "w", encoding="utf-8")
            logs.append(log)
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "acmecse", "--base-directory", str(directory)], stdout=log, stderr=log, cwd=folder)
            processes.append(process)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"ACME startup failed: {(folder / (zone+'.log')).read_text()}")
                try:
                    Cse(cfg["cse"][zone], "CAdmin").call("GET")
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                raise RuntimeError(f"ACME did not become ready: {zone}")
        provision(cfg)
        cfg["cse"]["VIA_IN"] = {"base_url": f"http://127.0.0.1:{in_port}/~/id-mn-a/cse-mn-a", "originator": "/id-mn-a/CBenchmark"}
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            try:
                Cse(cfg["cse"]["VIA_IN"], "/id-mn-a/CBenchmark").call("GET", "/benchmark")
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)
        else:
            raise RuntimeError(f"MN-A did not register with IN-CSE for Mcc transit: {last_error}")
        yield cfg
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()
        asyncio.run_coroutine_threadsafe(broker.shutdown(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
