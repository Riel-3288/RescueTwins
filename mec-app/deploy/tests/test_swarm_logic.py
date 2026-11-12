import importlib.util
import threading
from pathlib import Path
import pytest
from rescuetwin.swarm_logic import Coordinator,walls,WIDTH,HEIGHT
from rescuetwin.swarm_service import Fusion,make_server

ROOT=Path(__file__).resolve().parents[1]

def test_auction_assigns_distinct_goals_and_prevents_swaps():
    positions={'drone-A':(1,1),'drone-B':(3,1)}
    c=Coordinator();result=c.plan(positions,{(1,1),(3,1)}, {})
    assert len(result['assignments'])==2
    assert len({tuple(a['goal']) for a in result['assignments'].values()})==2
    nexts=[tuple(a['position']) for a in result['actions'].values()]
    assert len(set(nexts))==2
    for drone,action in result['actions'].items():
        assert tuple(action['position']) not in {p for d,p in positions.items() if d!=drone}
        assert tuple(action['position']) not in walls()

def test_hazards_replanning_and_finished_map():
    c=Coordinator();pos={'drone-A':(1,1),'drone-B':(48,28)}
    r=c.plan(pos,set(),{(2,1):.9})
    assert r['actions']['drone-A']['position']!=[2,1]
    seen={(x,y) for x in range(WIDTH) for y in range(HEIGHT)}-walls()
    done=c.plan(pos,seen,{})
    assert done['unknown_cells']==0 and done['assignments']=={}
    assert done['actions']['drone-A']['position']==[1,1]

def test_real_service_coordinated_run_covers_map_without_collision(monkeypatch,tmp_path):
    path=ROOT/'tools'/'run_swarm.py'
    spec=importlib.util.spec_from_file_location('coordinated_runner_test',path)
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    server=make_server(port=0,token='coordinator-test');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('SWARM_MEC_URL',f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('SWARM_API_TOKEN','coordinator-test')
    try:
        sim=runner.load_simulation();w=sim.World(1,'swarm',sim.load_series(),logging=True)
        for _ in range(1200):
            if w.finished:break
            previous=[a['pos'] for a in w.agents];w.step();positions=[a['pos'] for a in w.agents]
            assert len(set(positions))==2
            assert all(not w.walls[p] and not w.blocked[p] for p in positions)
            assert not any(p==previous[j] for i,p in enumerate(positions) for j in range(2) if i!=j)
        assert w.finished,f'Coverage {w.coverage()} after {w.t} ticks'
        assert len(w.found)==len(w.victims)
        assert w.mec_snapshot['coordination']['policy']=='frontier_auction_v1'
        print('coordinated result',w.t,w.coverage(),len(w.found))
        import subprocess,sys,os,json
        result=subprocess.run([sys.executable,str(path),'--quick','--no-anim','--output-dir',str(tmp_path)],
                              env=dict(os.environ),capture_output=True,text=True,timeout=90)
        assert result.returncode==0,result.stderr
        assert (tmp_path/'sim_agent_log.jsonl').is_file()
        assert all(json.loads(line)['unit']=='mV' for line in (tmp_path/'sim_sensor_data.jsonl').read_text().splitlines())
        monkeypatch.setenv('SWARM_API_TOKEN','wrong')
        positions=[a['pos'] for a in w.agents]
        with pytest.raises(Exception,match='401'):w.step()
        assert [a['pos'] for a in w.agents]==positions
    finally:server.shutdown();server.server_close();thread.join(3)


def test_retry_is_stable_and_overlapping_report_is_atomic():
    import copy
    f=Fusion()
    def report(d,x):return dict(mission_id='retry',drone_id=d,tick=0,sequence=0,
                               cells=[dict(x=x,y=1,hazard=0)],position=dict(x=x,y=1))
    f.report(report('drone-A',1))
    state=f.report(report('drone-B',3))
    assert f.report(report('drone-B',3))['coordination']==state['coordination']
    before=copy.deepcopy(f.missions)
    bad=report('drone-B',1);bad.update(tick=1,sequence=1)
    first=report('drone-A',1);first.update(tick=1,sequence=1)
    f.report(first);before=copy.deepcopy(f.missions)
    with pytest.raises(ValueError):f.report(bad)
    assert f.missions==before
