import json
import os
import subprocess
import sys
import time

import pytest

from conftest import ROOT, free_port
from rescuetwin.onem2m import Cse
from tools.acme_config import render


def launch(directory, log):
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen([sys.executable, "-X", "utf8", "-m", "tools.local_cse",
                             "--base-directory", str(directory), "--headless"],
                            stdin=subprocess.PIPE, text=True, stdout=log, stderr=log, cwd=ROOT, **options)


def wait_ready(process, cse):
    deadline = time.monotonic() + 30
    last_error = None
    while time.monotonic() < deadline:
        assert process.poll() is None, "CSE exited before becoming ready"
        try:
            cse.call("GET")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(.1)
    pytest.fail(f"CSE did not become ready: {last_error}")


def stop(process):
    if process.poll() is None:
        process.stdin.write("shutdown\n")
        process.stdin.flush()
    process.stdin.close()
    try:
        assert process.wait(25) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_cse_shutdown_flushes_records_and_restart_reads_them(tmp_path):
    port = free_port()
    directory = render(tmp_path / "cse", "IN", "id-lifecycle", "cse-lifecycle", port)
    endpoint = {"base_url": f"http://127.0.0.1:{port}/~/id-lifecycle/cse-lifecycle", "timeout_s": .5}
    cse = Cse(endpoint, "CStartupCheck")
    value = {"check": "persist immediately before shutdown"}
    with (tmp_path / "process.log").open("w", encoding="utf-8") as log:
        process = launch(directory, log)
        try:
            wait_ready(process, Cse(endpoint, "CAdmin"))
            cse.ensure_ae("startup-check", ["events"])
            cse.cin("/startup-check/events", value)
        finally:
            stop(process)
        for file in (directory / "data").glob("*.json"):
            if file.stat().st_size:
                json.loads(file.read_text(encoding="utf-8"))
        process = launch(directory, log)
        try:
            wait_ready(process, Cse(endpoint, "CAdmin"))
            assert cse.latest("/startup-check/events") == value
        finally:
            stop(process)


def test_headless_startup_failure_has_nonzero_exit_and_file_log(tmp_path):
    directory = render(tmp_path / "bad-cse", "IN", "id-bad", "cse-bad", free_port())
    data = directory / "data"
    data.mkdir()
    (data / "resources-('acme.example.com-id-bad',).json").write_text('{"resources": {}}broken', encoding="utf-8")
    with (tmp_path / "process.log").open("w", encoding="utf-8") as log:
        process = launch(directory, log)
        try:
            assert process.wait(25) == 1
        finally:
            process.stdin.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    logs = list((directory / "logs").glob("*.log"))
    assert logs and any("Extra data" in file.read_text(encoding="utf-8") for file in logs)
