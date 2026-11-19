import copy
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
import requests

from rescuetwin.swarm_service import Fusion


def report(drone='drone-A', tick=0, cells=None):
    return dict(mission_id='test', drone_id=drone, sequence=tick, tick=tick,
                cells=cells if cells is not None else [dict(x=1,y=1,hazard=0.9)])


def test_merge_decay_duplicate_clear_and_mission_isolation():
    f = Fusion()
    f.report(report())
    state = f.report(report('drone-B',cells=[dict(x=45,y=28,hazard=0)]))
    assert len(state['pheromone']) == 2 and state['hazards'][0]['blocked']
    assert len(f.report(report())['pheromone']) == 2
    with pytest.raises(ValueError):
        f.report(report(cells=[dict(x=1,y=1,hazard=0)]))
    state = f.report(report(tick=1,cells=[dict(x=1,y=1,hazard=0)]))
    assert not state['hazards']
    assert next(c for c in state['pheromone'] if c['x']==45)['value'] == pytest.approx(.997)
    other = report(); other['mission_id']='other'
    assert len(f.report(other)['pheromone']) == 1
    with pytest.raises(ValueError):f.report(report())


def test_hazard_expires_and_bad_input_is_atomic():
    f = Fusion(); f.report(report())
    for tick in range(1,10):
        state=f.report(report('drone-B',tick,cells=[dict(x=45,y=28,hazard=0)]))
    assert not state['hazards'] and state['active_drones']==['drone-B']
    before=copy.deepcopy(f.missions)
    for changes in [dict(drone_id='drone-C'),dict(tick=True),dict(cells=[dict(x=50,y=0,hazard=0)]),
                    dict(cells=[dict(x=1,y=0,hazard=float('nan'))])]:
        bad=report();bad.update(changes)
        with pytest.raises(ValueError):f.report(bad)
        assert f.missions==before


@pytest.fixture
def service():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,SWARM_API_TOKEN='test-only-token')
    process=subprocess.Popen([sys.executable,'-m','rescuetwin.swarm_service','--port',str(port)],env=env,
                             stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    url=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            if process.poll() is not None:raise RuntimeError(process.stderr.read().decode())
            try:
                if requests.get(url+'/health',timeout=.2).status_code==200:break
            except requests.RequestException:pass
            time.sleep(.05)
        else:raise RuntimeError('Service did not start')
        yield url
    finally:
        process.terminate();process.wait(timeout=5);process.stderr.close()


def test_real_separate_process_and_drone_decision_loop(service,monkeypatch):
    assert requests.post(service+'/v1/reports',json=report(),timeout=3).status_code==401
    headers={'Authorization':'Bearer test-only-token'}
    assert requests.post(service+'/v1/reports',json=report(),headers=headers,timeout=3).status_code==200
    path=Path(__file__).resolve().parents[3]/'simulation'/'rescue_sim.py'
    spec=importlib.util.spec_from_file_location('swarm_sim_test',path)
    sim=importlib.util.module_from_spec(spec);spec.loader.exec_module(sim)
    monkeypatch.setenv('SWARM_MEC_URL',service);monkeypatch.setenv('SWARM_API_TOKEN','test-only-token')
    world=sim.World(1,'swarm',sim.load_series(),logging=True)
    for _ in range(15):world.step()
    assert (sim.W,sim.H)==(50,30) and len(world.agents)==2
    assert set(world.mec_snapshot['active_drones'])=={'drone-A','drone-B'}
    assert world.mec_snapshot['source']=='mec_swarm_fusion'
    assert all(not world.walls[a['pos']] and not world.blocked[a['pos']] for a in world.agents)
    assert {n['zone'] for n in sim.NODES}=={'A','B'}
    from rescuetwin.recordings import sariel_grid
    assert {tuple(p) for p in sariel_grid()['walls']}=={(int(c),int(r)) for r,c in zip(*sim.build_walls().nonzero())}
    monkeypatch.setenv('SWARM_API_TOKEN','wrong')
    with pytest.raises(Exception,match='401'):world.step()
