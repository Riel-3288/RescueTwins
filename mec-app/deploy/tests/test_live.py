import json
import math
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from rescuetwin.core import Store, utcnow
from rescuetwin.mqtt import Bridge, publish, native_cin
from rescuetwin.onem2m import Cse, CseError
from rescuetwin.mec import Ipe
from rescuetwin.__main__ import benchmark


def reading(zone="A", device="node-01"):
    return {"device_id": device, "zone_id": zone, "sensor_type": "temperature", "value": 28.6,
            "unit": "degC", "source": "simulation", "quality": "valid", "observed_at": utcnow()}


def test_real_mqtt_bridge_readback_duplicate_and_zone_routing(live):
    bridge = Bridge(live)
    worker = threading.Thread(target=bridge.run, daemon=True)
    worker.start()
    try:
        # Wait for the subscription, not just a TCP connection.
        assert bridge.ready.wait(5)
        first, second = reading(), reading("B", "node-03")
        publish(live["mqtt"], first, live["devices"])
        publish(live["mqtt"], first, live["devices"])
        publish(live["mqtt"], second, live["devices"])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and bridge.store.counts().get("delivered", 0) < 2:
            time.sleep(0.1)
        assert bridge.store.counts() == {"delivered": 2}
        assert Cse(live["cse"]["A"], "CNode01").latest("/node-01/temperature") == first
        assert Cse(live["cse"]["B"], "CNode03").latest("/node-03/temperature") == second
    finally:
        bridge.stop.set()
        worker.join(10)
        assert not worker.is_alive()


def test_native_onem2m_mqtt_binding(live):
    # Give the ACME MQTT client time to subscribe after HTTP readiness.
    time.sleep(0.3)
    value = reading()
    response = native_cin(live["mqtt"], "CNode01", "id-mn-a", "/id-mn-a/cse-mn-a/node-01/temperature", value)
    assert response["rsc"] == 2001
    assert Cse(live["cse"]["A"], "CNode01").latest("/node-01/temperature") == value


def test_http_retry_is_idempotent(live):
    client = Cse(live["cse"]["A"], "CNode01")
    value = reading()
    a = client.cin("/node-01/temperature", value)
    b = client.cin("/node-01/temperature", value)
    assert a["ri"] == b["ri"]


def test_real_cse_denies_wrong_originator(live):
    with pytest.raises(CseError) as exc:
        Cse(live["cse"]["A"], "CNotAllowed").latest("/node-01/temperature")
    assert exc.value.status == 403


def test_handover_and_bidirectional_mec_with_contract_server(live, tmp_path):
    class Handler(BaseHTTPRequestHandler):
        x = 40
        seconds = time.time()
        calls = 0
        def do_GET(self):
            type(self).calls += 1
            if self.path.startswith("/location/queries/users"):
                data = {"userList": {"user": [{"address": "10.10.0.1", "zoneId": "network-zone",
                        "timeStamp": {"seconds": int(self.seconds), "nanoSeconds": int((self.seconds % 1)*1e9)},
                        "locationInfo": {"shape": 2, "latitude": [2.831 + 30/111320],
                          "longitude": [101.706 + self.x/(111320*math.cos(math.radians(2.831)))]}}]}}
            else:
                data = {"rabInfo": [{"cellId": "1010101", "ecgi": "test-fixture"}]}
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = json.loads(json.dumps(live))
    cfg["ipe_state_file"] = str(tmp_path / "ipe.sqlite")
    base = f"http://127.0.0.1:{server.server_port}"
    cfg["mec"].update(location_url=base+"/location", rnis_url=base+"/rni")
    try:
        ipe = Ipe(cfg)
        assert ipe.cycle() == []
        for x in (60, 62):
            Handler.x = x
            Handler.seconds += 0.1
            assert ipe.cycle() == []
        assert ipe.store.get("agent:agent-01")["zone"] == "B"
        event = ipe.cse.latest("/mec-ipe/handover")
        assert event["from_zone"] == "A" and event["to_zone"] == "B"
        assert Cse(cfg["cse"]["B"], "CAgent01").latest("/agent-01/position")["zone_id"] == "B"
        # Create two queued oneM2M commands before polling: both must complete.
        commands = [{"command_id": uuid.uuid4().hex, "operation": "get_location", "address": "10.10.0.1"},
                    {"command_id": uuid.uuid4().hex, "operation": "get_rnis"}]
        for command in commands:
            ipe.cse.cin("/mec-ipe/requests", command)
        ipe.commands()
        responses = [json.loads(cin["con"]) for cin in ipe.cse.entries("/mec-ipe/responses")]
        assert {r["command_id"] for r in responses} >= {c["command_id"] for c in commands}
        assert all(r["ok"] for r in responses)
        before = Handler.calls
        ipe.commands()
        assert before == Handler.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_storage_rtt_benchmark_probes(live, tmp_path):
    output = tmp_path / "storage.csv"
    benchmark(live, 3, output)
    assert len(output.read_text().splitlines()) == 7


def test_mqtt_cse_outage_recovers_from_durable_outbox(live, tmp_path):
    from conftest import free_port
    cfg = json.loads(json.dumps(live))
    cfg["state_file"] = str(tmp_path/"outage.sqlite")
    good_endpoint = cfg["cse"]["A"]
    cfg["cse"]["A"] = {"base_url": f"http://127.0.0.1:{free_port()}/~/id-mn-a/cse-mn-a", "timeout_s": 0.2}
    bridge = Bridge(cfg)
    worker = threading.Thread(target=bridge.run, daemon=True)
    worker.start()
    value = reading()
    try:
        assert bridge.ready.wait(5)
        publish(cfg["mqtt"], value, cfg["devices"])
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            with bridge.store.lock:
                row = bridge.store.db.execute("SELECT attempts,status FROM outbox").fetchone()
            if row and row[0] >= 1:
                break
            time.sleep(0.1)
        assert row and row[0] >= 1 and row[1] == "pending"
        cfg["cse"]["A"] = good_endpoint
        bridge.clients.clear()
        with bridge.store.lock, bridge.store.db:
            bridge.store.db.execute("UPDATE outbox SET due=0")
        deadline = time.monotonic()+10
        while time.monotonic() < deadline and not bridge.store.counts().get("delivered"):
            time.sleep(0.1)
        assert bridge.store.counts() == {"delivered": 1}
        assert Cse(good_endpoint, "CNode01").latest("/node-01/temperature") == value
    finally:
        bridge.stop.set()
        worker.join(10)


import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from rescuetwin.core import load_config
from rescuetwin.__main__ import sample_plan, replay, benchmark_samples, network_experiment, DEFAULT_SAMPLES
from rescuetwin.mec import Ipe, Mec
from rescuetwin.onem2m import Cse


def recorded_config():
    return load_config(Path(__file__).resolve().parents[1]/'config.example.json')


def test_actual_sample_data_is_preserved_and_incompatible_rows_reported():
    cfg=recorded_config(); before=DEFAULT_SAMPLES.read_bytes(); plan=sample_plan(DEFAULT_SAMPLES,cfg)
    assert (plan['input_rows'],len(plan['accepted']),len(plan['rejected'])) == (25,24,1)
    raw=json.loads(before)
    assert all(value == raw[index] for index,value in plan['accepted'])
    assert DEFAULT_SAMPLES.read_bytes()==before
    with pytest.raises(ValueError):replay(cfg,plan,factory=Mock())


def test_demo_file_input_is_once_only_and_does_not_replace_timestamps():
    from rescuetwin.demo import Demo
    cfg=recorded_config();cfg['sensor_file']=str(DEFAULT_SAMPLES)
    demo=Demo(cfg)
    assert demo.next_sensor_readings()==[]
    demo.connected=True;demo.mqtt_ready.set()
    rows=demo.next_sensor_readings()
    assert len(rows)==24 and rows[0]['observed_at']=='2026-11-01T22:08:30Z'
    assert demo.next_sensor_readings()==[]


def test_network_baseline_restored_after_measurement_failure(tmp_path):
    mec=Mock(); baseline={'latency':7,'latencyVariation':2,'packetLoss':.5,'throughput':50}
    mec.call.return_value={'nodes':[{'name':'edge-a','netChar':baseline}]}
    def fail(i):raise RuntimeError('probe failed')
    with pytest.raises(RuntimeError):
        network_experiment(mec,'https://sandbox.example/control','edge-a','EDGE',[(20,1)],fail,tmp_path/'result.json')
    calls=mec.call.call_args_list
    assert calls[-1].args[2]['eventNetworkCharacteristicsUpdate']['netChar']==baseline
    assert json.loads((tmp_path/'result.json').read_text())['restored']
    before=len(calls)
    with pytest.raises(ValueError):
        network_experiment(mec,'https://sandbox.example/control','edge-a','EDGE',[(0,101)],fail,tmp_path/'bad.json')
    assert len(mec.call.call_args_list)==before+1  # GET only; no mutation


def test_recorded_samples_replay_and_real_mcc_measurement(live,tmp_path):
    cfg=copy.deepcopy(live)
    cfg['cse']['VIA_IN']['originators']={'node-01':'/id-mn-a/CNode01'}
    plan=sample_plan(DEFAULT_SAMPLES,cfg)
    result=replay(cfg,plan,True)
    assert result['complete'] and len(result['events'])==24
    assert replay(cfg,plan,True)['complete']
    output=tmp_path/'matched.csv'
    report=benchmark_samples(cfg,plan,1,output,True)
    assert report['complete']
    assert report['paths']['A']['successes']==24 and report['paths']['VIA_IN']['successes']==24
    assert len(output.read_text().splitlines())==49
    no_route=copy.deepcopy(cfg);no_route['cse'].pop('VIA_IN')
    with pytest.raises(ValueError):benchmark_samples(no_route,plan,1,tmp_path/'bad.csv',True)


def test_onem2m_command_really_updates_mec_configuration(live,tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body=dict(appMobilityServiceId='fixture-service',serviceConsumerId=dict(appInstanceId='fixture-app'),
                deviceInformation=[dict(appMobilityServiceLevel=3,associateId=dict(type=1,value='existing-ue'),contextTransferState=1)])
            raw=json.dumps(body).encode();self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_PUT(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path,body))
            raw=json.dumps(body).encode();self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    cfg=copy.deepcopy(live);cfg['ipe_state_file']=str(tmp_path/'commands.sqlite')
    cfg['ipe_zone']='B'  # independent request queue from the existing shared-fixture tests
    cfg['mec'].update(ams_url=f'http://127.0.0.1:{server.server_port}/amsi/v1',
                      app_instance_id='fixture-app',mobility_service_id='fixture-service')
    try:
        ipe=Ipe(cfg)
        command={'command_id':'week4-configure','operation':'configure_mobility','address':'10.10.0.1'}
        ipe.cse.cin('/mec-ipe/requests',command)
        ipe.commands();ipe.commands()
        assert len(requests)==1
        assert requests[0][0]=='/amsi/v1/app_mobility_services/fixture-service'
        assert requests[0][1]['deviceInformation'][0]['contextTransferState']==1
        assert requests[0][1]['deviceInformation'][1]['contextTransferState']==0
        responses=[json.loads(c['con']) for c in ipe.cse.entries('/mec-ipe/responses')]
        assert any(r['command_id']=='week4-configure' and r['ok'] for r in responses)
        count=len(requests)
        ipe.cse.cin('/mec-ipe/requests',dict(command,command_id='week4-bad',address='unknown'))
        ipe.commands();assert len(requests)==count
    finally:server.shutdown();server.server_close();thread.join(5)
