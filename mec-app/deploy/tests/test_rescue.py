import copy
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from rescuetwin.core import Store, utcnow, load_config
from rescuetwin.demo import Demo, make_server
from rescuetwin.mqtt import Bridge, bridge_owner, publish
from rescuetwin.onem2m import Cse
from rescuetwin.rescue import Rescue, TelemetryGate
from rescuetwin.simulator import Factory


def reading(value=80, source='physical', **extra):
    return dict(device_id='node-01', zone_id='A', sensor_type='temperature', value=value,
                unit='degC', source=source, quality='valid', observed_at=utcnow(), **extra)


def wait_for(predicate, timeout=15):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Condition did not become true within the timeout')


def test_legacy_queue_migration_priority_atomic_claim_and_crash_recovery(tmp_path):
    path = tmp_path/'queue.sqlite'
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE outbox(id TEXT PRIMARY KEY,body TEXT,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,due REAL DEFAULT 0,received REAL,delivered REAL,error TEXT)")
    c.execute('INSERT INTO outbox(id,body,received) VALUES(?,?,?)', ('legacy',json.dumps(reading()),1))
    c.commit(); c.close()
    store = Store(path)
    event = store.enqueue_event('A','events',{'event_id':'test','kind':'incident_opened'})
    batch = store.claim_batch('A',1)
    assert batch[0][0] == event
    assert store.claim_batch('B') == []
    assert store.claim_batch('A')[0][0] == 'legacy'
    assert store.claim_batch('A') == []
    store.recover_processing()
    assert store.counts() == {'pending':2}
    assert {r[0] for r in store.claim_batch('A')} == {'legacy',event}
    store.finish_batch([(event,0,None,False),('legacy',1,'temporary failure',False)])
    assert store.counts() == {'delivered':1,'pending':1}
    assert store.pending() is None


def test_second_bridge_cannot_own_same_database(tmp_path):
    with bridge_owner(tmp_path/'queue.sqlite'):
        with pytest.raises(RuntimeError, match='already owns'):
            with bridge_owner(tmp_path/'queue.sqlite'):
                pass


def test_simulator_gate_reduces_repetition_and_keeps_alarm_crossings():
    gate = TelemetryGate()
    assert gate.accept(reading(28.6),200,now=0)
    assert not gate.accept(reading(28.6),200,now=1)
    assert not gate.accept(reading(30),200,now=2)
    assert gate.accept(reading(65),200,now=2.1)
    assert gate.accept(reading(40),200,now=2.2)
    assert not gate.accept(reading(40),200,now=3)
    assert gate.accept(reading(40),200,now=18)


def test_alarm_dedup_stale_invalid_hysteresis_source_isolation_and_persistence(tmp_path):
    cfg = {'state_file':str(tmp_path/'queue.sqlite'), 'rescue':{'enabled':True}}
    factory = Factory()
    engine = Rescue(cfg,factory,lambda *args:None)
    hot = reading()
    engine.ingest(dict(hot,observed_at='2020-01-01T00:00:00Z'))
    engine.ingest(dict(hot,quality='invalid',value=None))
    assert not engine.incidents
    engine.ingest(hot); engine.ingest(hot)
    assert len(engine.incidents) == 1
    engine.tick()
    assert factory.unsafe(10,8) and not factory.route((4,8),(10,8))
    assert len(engine.tasks) == 1
    engine.ingest(reading(28.6,'simulation'))
    engine.ingest(reading(55))
    assert engine.incidents[0]['status'] == 'active'
    engine.ingest(reading(40)); engine.ingest(reading(39))
    engine.tick()
    assert engine.incidents[0]['status'] == 'resolved'
    assert not factory.unsafe(10,8)
    restored = Rescue(cfg,Factory(),lambda *args:None)
    assert restored.incidents[0]['status'] == 'resolved'
    assert restored.tasks[0]['status'] == 'cancelled'
    restored.ingest(hot)
    assert len(restored.incidents) == 1  # Old readings cannot reopen a resolved incident.


def test_clear_requires_two_samples_of_the_trigger_channel(tmp_path):
    engine=Rescue({'state_file':str(tmp_path/'queue.sqlite')},Factory(),lambda *args:None)
    engine.ingest(reading(80));engine.ingest(reading(40))
    for n in range(3):
        cold=reading(90);cold.update(sensor_type='co_raw',unit='mV')
        engine.ingest(cold)
    assert engine.incidents[0]['status']=='active'
    engine.ingest(reading(39))
    assert engine.incidents[0]['status']=='resolved'


def test_blocked_dispatch_and_manual_goal_cancellation_are_reported(tmp_path):
    factory = Factory()
    factory.walls = {(x,y) for x in range(factory.width) for y in range(factory.height)}
    engine = Rescue({'state_file':str(tmp_path/'queue.sqlite')},factory,lambda *args:None)
    engine.ingest(reading()); engine.tick()
    assert not engine.tasks and engine.events[-1]['kind'] == 'dispatch_blocked'
    factory.walls.clear(); engine.tick()
    assert engine.tasks
    engine.cancel_agent(engine.tasks[0]['agent_id'],'manual override')
    assert engine.tasks[0]['status'] == 'cancelled'


def test_rescue_demo_resets_only_simulated_incidents(tmp_path):
    cfg=load_config(Path(__file__).resolve().parents[1]/'config.example.json')
    cfg['state_file']=str(tmp_path/'queue.sqlite')
    demo=Demo(cfg)
    try:
        demo.rescue.ingest(reading(80,'physical'))
        demo.rescue.ingest(reading(80,'simulation'))
        demo.control({'action':'rescue_demo'})
        assert not demo.factory.paused and (10,8) in demo.factory.fires
        assert next(i for i in demo.rescue.incidents if i['source']=='physical')['status']=='active'
        assert next(i for i in demo.rescue.incidents if i['source']=='simulation')['status']=='resolved'
    finally:
        demo.close()


def test_slow_mqtt_publication_cannot_block_model_reads(tmp_path):
    cfg=load_config(Path(__file__).resolve().parents[1]/'config.example.json')
    cfg['state_file']=str(tmp_path/'queue.sqlite')
    demo=Demo(cfg)
    network_entered, release_network, publication_returned = threading.Event(), threading.Event(), threading.Event()
    class SlowClient:
        def publish(self,*args,**kwargs):
            network_entered.set()
            release_network.wait(3)
            return type('Result',(),{'rc':0})()
    demo.client=SlowClient(); demo.connected=True
    publisher=threading.Thread(target=demo.publish_loop,daemon=True);publisher.start()
    def issue_command():
        with demo.lock:
            demo.publish('rescuetwin-control/commands/agent-01',{'command_id':'test'})
            publication_returned.set()
    controller=threading.Thread(target=issue_command,daemon=True);controller.start()
    try:
        assert publication_returned.wait(.5), 'Network publication held the simulator lock'
        assert network_entered.wait(.5)
        assert demo.lock.acquire(timeout=.5), 'The dashboard cannot read the model while MQTT is busy'
        demo.lock.release()
        assert demo.snapshot()['paused']
    finally:
        release_network.set(); demo.stop.set();controller.join(3);publisher.join(3)


def test_actual_mqtt_sensor_to_command_receipt_completion_recovery_and_cse(live,tmp_path):
    cfg=copy.deepcopy(live)
    cfg['state_file']=str(tmp_path/'closed-loop.sqlite')
    bridge=Bridge(cfg)
    worker=threading.Thread(target=bridge.run,daemon=True);worker.start()
    demo=Demo(cfg,bridge)
    server=make_server(demo,0)
    http=threading.Thread(target=server.serve_forever,daemon=True);http.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        assert bridge.ready.wait(5)
        demo.start(); assert demo.mqtt_ready.wait(5)
        hot=reading()
        publish(cfg['mqtt'],hot,cfg['devices']);publish(cfg['mqtt'],hot,cfg['devices'])
        wait_for(lambda: any(t['status']=='accepted' for t in demo.snapshot()['rescue']['tasks']))
        assert len(demo.snapshot()['rescue']['incidents'])==1
        assert demo.snapshot()['paused']  # Receiving an alarm must not silently resume the simulation.
        incident=demo.snapshot()['rescue']['incidents'][0]
        assert requests.post(url+'/api/control',json={'action':'ack_incident','incident_id':incident['incident_id']}).status_code==200
        assert requests.post(url+'/api/control',json={'action':'resume'}).status_code==200
        wait_for(lambda:any(t['status']=='completed' for t in demo.snapshot()['rescue']['tasks']))
        snapshot=demo.snapshot()
        kinds={e['kind'] for e in snapshot['rescue']['events']}
        assert {'incident_opened','task_dispatched','task_accepted','task_moving','task_completed'} <= kinds
        task=snapshot['rescue']['tasks'][0]
        assert task['feedback']['executor']=='local_simulation' and task['observation_to_completion_ms']>=0
        assert snapshot['rescue']['incidents'][0]['status']=='active'
        client=Cse(cfg['cse']['A'],'CRescueControl')
        wait_for(lambda:any(json.loads(c['con'])['kind']=='task_completed' for c in client.entries('/rescue-control/events')))
        publish(cfg['mqtt'],reading(40),cfg['devices'])
        time.sleep(.01)
        publish(cfg['mqtt'],reading(39),cfg['devices'])
        wait_for(lambda:demo.snapshot()['rescue']['incidents'][0]['status']=='resolved')
        assert demo.snapshot()['observed_hazard_cells']==[]
    finally:
        server.shutdown();server.server_close();http.join(5)
        demo.close();bridge.stop.set();worker.join(20)
        assert not worker.is_alive()


def test_backlog_replays_all_records_without_loss(live,tmp_path):
    cfg=copy.deepcopy(live);cfg['state_file']=str(tmp_path/'backlog.sqlite')
    store=Store(cfg['state_file'])
    rows=[]
    for i in range(240):
        row=reading(28.6,source='simulation')
        if i%2:row.update(zone_id='B',device_id='node-03')
        row['observed_at']=datetime.fromtimestamp(time.time()-1000+i/1000,timezone.utc).isoformat()
        rows.append(row)
    store.enqueue_many(rows)
    assert store.counts()=={'pending':240}
    bridge=Bridge(cfg,store);worker=threading.Thread(target=bridge.run,daemon=True)
    started=time.monotonic();worker.start()
    try:
        wait_for(lambda:store.counts().get('delivered')==240,timeout=50)
        assert store.counts()=={'delivered':240}
        assert Cse(cfg['cse']['A'],'CNode01').latest('/node-01/temperature')==rows[-2]
        assert Cse(cfg['cse']['B'],'CNode03').latest('/node-03/temperature')==rows[-1]
        print(f'Replayed 240 records in {time.monotonic()-started:.2f}s')
    finally:
        bridge.stop.set();worker.join(20)
