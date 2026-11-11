import copy
import json
import threading
from pathlib import Path

import pytest
import requests

from rescuetwin.core import load_config
from rescuetwin.recordings import RecordedMission, parse_records, route, sariel_grid

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path):
    value = load_config(ROOT/'config.team.example.json')
    value['state_file'] = str(tmp_path/'bridge.sqlite')
    return value


def log(step=0, x=1, y=1, kind='position', device='drone-A'):
    return {'kind':kind,'device_id':device,'step':step,'x':x,'y':y,
            'zone_id':'AB'[x//25], 'source':'simulation',
            'observed_at':f'2026-11-08T19:41:{21+step:02}Z'}


def test_json_jsonl_single_and_wrapped_input():
    row={'device_id':'node-01'}
    for raw in [json.dumps(row),json.dumps([row]),json.dumps(row)+'\n'+json.dumps(row),
                json.dumps({'m2m:cin':{'con':json.dumps(row)}})]:
        assert parse_records(raw)[0] == row
    for raw in ['{"x":1,"x":2}', '[NaN]', '[]', '[1]', '{"x":1}\nBAD', ' '* (4*1024*1024+1)]:
        with pytest.raises((ValueError, TypeError)):
            parse_records(raw)


def test_latest_team_files_keep_all_zones_and_original_units(cfg):
    m=RecordedMission(cfg)
    m.load_defaults()
    assert m.report['rows']>0 and m.report['compatible']==m.report['rows']
    assert {r['unit'] for r in m.sensors}=={'mV'}
    assert {r['zone_id'] for r in m.agent_log if 'zone_id' in r}=={'A','B'}
    assert len(m.frame()['agents'])==2
    assert m.grid['width']==50 and m.grid['height']==30
    assert not m.frame()['victims']
    m.seek(m.snapshot()['max_step'])
    assert len(m.snapshot()['robot_task_suggestions'])==2
    assert m.frame()['unclassified_hazards']
    assert not m.frame()['hazards']
    assert not m.snapshot()['sensor_report']['cse_written']


def test_no_future_state_or_victim_leaks_into_plan(cfg):
    m=RecordedMission(cfg)
    m.upload('agents',json.dumps([log(),log(5,7,3,kind='victim_found'),log(6,8,3)]))
    assert m.frame()['agents'][0]['x']==1
    assert not m.snapshot()['robot_task_suggestions']
    p=m.plan('drone-A')
    assert p['goal']!=[7,3]
    m.seek(5)
    assert len(m.snapshot()['robot_task_suggestions'])==1
    assert m.plan('drone-A')['goal']==[7,3]


def test_bad_agent_upload_is_atomic(cfg):
    m=RecordedMission(cfg)
    m.upload('agents',json.dumps(log()))
    for row in [dict(log(),x=99),log(x=25,y=1),dict(log(),zone_id='B'),dict(log(),step=-1)]:
        with pytest.raises(ValueError):m.upload('agents',json.dumps([row]))
        assert len(m.agent_log)==1 and m.agent_log[0]['x']==1
    with pytest.raises(ValueError):m.upload('agents',json.dumps([log(),log()]))


def test_round_trip_reserve_and_replanning_invalidate_execution(cfg):
    m=RecordedMission(cfg)
    m.upload('agents',json.dumps([log(),log(1,2,1)]))
    p=m.plan('drone-A',[7,3])
    assert p['status']=='planned'
    simulation=m.simulate('drone-A',p['plan_id'])['simulation']
    assert simulation['final_position']==[1,1]
    assert simulation['battery_remaining_units']>=10
    assert not simulation['real_flight'] and p['hardware_dispatched'] is False
    for a,b in zip(simulation['route'],simulation['route'][1:]):
        assert abs(a[0]-b[0])+abs(a[1]-b[1])==1
    m.seek(1)
    with pytest.raises(ValueError):m.simulate('drone-A',p['plan_id'])
    assert m.plan('drone-A',[8,17],battery=12)['status']=='return_home'
    assert m.plan('drone-A',[8,17],battery=0)['status']=='blocked'


def test_route_detours_and_rejects_hazardous_start_or_goal():
    g=sariel_grid()
    p=route(g,(24,1),(26,1))
    assert len(p)>3 and all(list(v) not in g['walls'] for v in p)
    assert not route(g,(1,1),(2,1),{(1,1)})
    assert not route(g,(1,1),(2,1),{(2,1)})


def test_hazard_keeps_units_and_blocks_route(cfg):
    m=RecordedMission(cfg)
    m.upload('agents',json.dumps([dict(log(x=4,y=3,kind='node',device='node-01')),log()]))
    reading={'device_id':'node-01','zone_id':'A','sensor_type':'temperature','value':70,
             'unit':'degC','source':'physical','quality':'valid','observed_at':log()['observed_at']}
    m.upload('sensors',json.dumps(reading))
    assert m.plan('drone-A',[4,3])['status']=='blocked'
    raw=copy.deepcopy(reading)
    m.upload('sensors',json.dumps(dict(reading,unit='mV')))
    assert not m.frame()['hazards'] and m.frame()['unclassified_hazards']
    assert reading==raw


def test_team_identity_never_enables_writes_or_fallback_token(cfg,monkeypatch):
    from rescuetwin.onem2m import Cse
    from rescuetwin.__main__ import provision,replay
    from rescuetwin.mqtt import Bridge
    monkeypatch.setenv('CSE_TOKEN','unrelated-token')
    assert Cse(cfg['cse']['A'],'CNode01').token is None
    for operation in [lambda:provision(cfg),lambda:replay(cfg,{}),lambda:Bridge(cfg)]:
        with pytest.raises(ValueError):operation()
    assert cfg['mec']['app_instance_id']==''
    assert cfg['agents']=={}


def test_upload_seek_plan_and_export_via_actual_http(cfg):
    from rescuetwin.demo import Demo,make_server
    demo=Demo(cfg)
    server=make_server(demo,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        r=requests.post(base+'/api/upload/agents',data=json.dumps([log(),log(1,2,1)]),timeout=5)
        assert r.status_code==200 and r.json()['agent_rows']==2
        assert requests.post(base+'/api/control',json={'action':'drone_plan','drone_id':'drone-A','goal':[7,3]},timeout=5).status_code==200
        exported=requests.get(base+'/api/recorded/export.json',timeout=5).json()
        assert exported['plans'][0]['goal']==[7,3]
        assert requests.post(base+'/api/upload/agents',data='{"kind":NaN}',timeout=5).status_code==400
        assert requests.post(base+'/api/upload/sensors',data='{}',headers={'Origin':'http://evil.example'},timeout=5).status_code==403
        assert requests.post(base+'/api/control',json={'action':'recorded_seek','step':1},timeout=5).status_code==200
        assert not requests.get(base+'/api/recorded/export.json',timeout=5).json()['plans']
    finally:
        server.shutdown();server.server_close();thread.join(3)
        demo.client.disconnect();demo.rescue.store.db.close()


def test_inconsistent_replay_timestamps_are_rejected(cfg):
    m=RecordedMission(cfg)
    for rows in [[log(),dict(log(1),observed_at=log()['observed_at'])],
                 [log(),dict(log(device='drone-B'),observed_at=log(1)['observed_at'])]]:
        with pytest.raises(ValueError):m.upload('agents',json.dumps(rows))
