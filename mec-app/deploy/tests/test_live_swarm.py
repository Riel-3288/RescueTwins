import json
import subprocess
import sys
import threading
import time
from pathlib import Path
import pytest
import requests
from rescuetwin.simulator import Factory
from rescuetwin.swarm_service import make_server,Fusion
from rescuetwin.layout import live_spec

@pytest.mark.parametrize('count',[6,8])
def test_live_fleet_explores_and_fuses_observations_without_collision(count):
    f=Factory(config={'drone_count':count});assert (f.width,f.height,f.zone_x)==(80,40,40)
    assert len(f.agents)==count and {a['zone'] for a in f.agents}=={'A','B'}
    initial={a['id']:(a['x'],a['y']) for a in f.agents};f.command({'action':'resume'})
    for _ in range(45):
        old={a['id']:(a['x'],a['y']) for a in f.agents};f.step()
        assert not f.paused,f.swarm_error
        positions={(a['x'],a['y']) for a in f.agents}
        assert len(positions)==count
        for a in f.agents:
            p=a['x'],a['y']
            assert p not in f.walls and not f.unsafe(*p)
            assert p not in {p for d,p in old.items() if d!=a['id']}
    snapshot=f.snapshot();assert snapshot['swarm']['tick']==15
    assert len(f.fusion.missions[f._mission]['reports'])==count
    assert len(f.observation_counts)==count and snapshot['swarm']['shared_cells']>count*25
    assert all((a['x'],a['y'])!=initial[a['id']] for a in f.agents)
    assert any(0<v<1 for v in snapshot['pheromone'])
    assert all(a['goal'] not in ([48,8],[48,22]) for a in f.agents)

def test_live_http_fusion_and_manual_override_then_resume():
    server=make_server(port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        f=Factory(config={'drone_count':6,'swarm_url':f'http://127.0.0.1:{server.server_port}'})
        a=f.agents[0];f.command({'action':'goal','agent_id':a['id'],'x':6,'y':8});f.command({'action':'resume'})
        for _ in range(9):f.step()
        assert not f.paused,f.swarm_error
        assert (a['x'],a['y'])==(6,8) and a['status']=='arrived'
        assert not f.fusion.missions # No embedded fallback; reports went to the HTTP server.
        assert f.snapshot()['swarm']['transport']=='http'
        f.command({'action':'resume_swarm'})
        for _ in range(6):f.step()
        assert a['mode']=='swarm' and not a['_manual']
    finally:server.shutdown();server.server_close();thread.join(3)

def test_fusion_failure_stops_fleet_and_map_change_requires_new_mission(monkeypatch):
    f=Factory();old=[(a['x'],a['y']) for a in f.agents]
    def fail():raise RuntimeError('Fusion unavailable')
    monkeypatch.setattr(f,'_sync_swarm',fail);f.command({'action':'resume'})
    for _ in range(3):f.step()
    assert f.paused and 'unavailable' in f.swarm_error and old==[(a['x'],a['y']) for a in f.agents]
    spec=live_spec({'drone_count':6});fusion=Fusion()
    report=dict(mission_id='live-map',drone_id=spec.drone_ids[0],sequence=0,tick=0,map=spec.payload(),position={'x':4,'y':8},cells=[dict(x=4,y=8,hazard=0)])
    assert fusion.report(report)['width']==80
    changed=json.loads(json.dumps(report));changed['map']['zone_x']=30
    with pytest.raises(ValueError):fusion.report(changed)
    assert fusion.missions['live-map']['map']['zone_x']==40

def test_geometry_boundary_configuration_and_invalid_registry():
    f=Factory(config={'width':90,'height':45,'zone_x':35,'drone_count':8})
    assert f.geofence.config['boundary_x_m']==35*f.cell_m
    assert f.spec.zone(34)=='A' and f.spec.zone(35)=='B'
    assert f.snapshot()['zone_x']==35
    for n in [0,True,17]:
        with pytest.raises(ValueError):Factory(config={'drone_count':n})


def test_live_local_inspection_feedback_needs_no_broker(tmp_path):
    from rescuetwin.core import load_config,utcnow
    from rescuetwin.demo import Demo
    cfg=load_config(Path(__file__).resolve().parents[1]/'config.example.json');cfg['state_file']=str(tmp_path/'state.sqlite')
    demo=Demo(cfg);demo.live_only=True;demo.factory.command({'action':'resume'})
    try:
        demo.rescue.ingest(dict(device_id='node-01',zone_id='A',sensor_type='temperature',value=80,
            unit='degC',source='physical',quality='valid',observed_at=utcnow()))
        for _ in range(45):
            demo.rescue.tick();demo.factory.step();demo.executor.tick()
            if any(t['status']=='completed' for t in demo.rescue.tasks):break
        assert any(t['status']=='completed' for t in demo.rescue.tasks)
        assert not demo.connected and not demo.factory.swarm_error
    finally:demo.close()


def test_live_dashboard_samples_current_sensor_values_by_default(tmp_path):
    from conftest import free_port

    root = Path(__file__).resolve().parents[1]
    port = free_port()
    url = f'http://127.0.0.1:{port}'
    # Use the same CLI and team profile as the Dashboard container. Isolate its database.
    launch = 'from pathlib import Path; import sys; from rescuetwin import demo; demo.ROOT = Path(sys.argv.pop(1)); demo.main()'
    process = subprocess.Popen([
        sys.executable, '-c', launch, str(tmp_path), '--live-swarm', '--drone-count', '2',
        '--config', str(root / 'config.team.example.json'), '--port', str(port),
    ], cwd=root)

    def wait_for_state(predicate):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert process.poll() is None, 'Dashboard stopped unexpectedly'
            try:
                response = requests.get(url + '/api/state', timeout=2)
                response.raise_for_status()
                state = response.json()
                if predicate(state):
                    return state
            except requests.RequestException:
                pass
            time.sleep(0.1)
        pytest.fail('Dashboard did not report the expected sensor state')

    def node_readings(state):
        return {r['sensor_type']: r for r in state['readings'] if r['device_id'] == 'node-01'}

    try:
        before = wait_for_state(lambda state: len(state['readings']) == 18)
        baseline = node_readings(before)
        assert {r['source'] for r in before['readings']} == {'simulation'}
        assert before['sensor_input']['mode'] == 'generated'
        sensor = next(s for s in before['sensors'] if s['id'] == 'node-01')
        response = requests.post(url + '/api/control', json={'action': 'ignite', 'x': sensor['x'], 'y': sensor['y']}, timeout=5)
        response.raise_for_status()
        after = wait_for_state(lambda state: node_readings(state)['temperature']['value'] > baseline['temperature']['value'])
        readings = node_readings(after)
        assert readings['smoke_raw']['value'] > baseline['smoke_raw']['value']
        assert readings['temperature']['observed_at'] > baseline['temperature']['observed_at']
        assert after['sensor_input']['local_samples'] > before['sensor_input']['local_samples']
        assert after['mqtt']['received'] == 0 and not after['mqtt']['connected']
        assert {m['topic'] for m in after['messages']} == {'local-simulation'}
    finally:
        process.terminate()
        process.wait(timeout=10)
