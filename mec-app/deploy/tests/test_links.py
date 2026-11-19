import json
from pathlib import Path
import pytest
from rescuetwin.core import Store,load_config
from rescuetwin.links import geo_to_grid,mapped_locations,RobotTasks
from rescuetwin.recordings import RecordedMission

ROOT=Path(__file__).resolve().parents[1]

def test_projection_corners_outside_and_demo_identity():
    cfg=load_config(ROOT/'config.team.example.json');b=cfg['mec_recorded_mapping']['bounds']
    assert geo_to_grid(b['north'],b['west'],b)==[0,0]
    assert geo_to_grid(b['south'],b['east'],b)==[49,29]
    for lat,lon in [(0,0),(True,7.43),(float('nan'),7.43)]:
        with pytest.raises(ValueError):geo_to_grid(lat,lon,b)
    payload={'userList':{'user':[{'address':'10.10.0.1','locationInfo':{'shape':2,'latitude':[43.735],'longitude':[7.423]}}]}}
    result=mapped_locations(cfg,payload)
    assert result['records'][0]['drone_id']=='drone-A' and result['records'][0]['ok']
    assert not result['records'][0]['used_for_dispatch']
    assert not result['records'][1]['ok']

def test_robot_actual_local_route_feedback_and_duplicates(tmp_path):
    cfg=load_config(ROOT/'config.team.example.json');m=RecordedMission(cfg);m.load_defaults();m.seek(m.snapshot()['max_step'])
    robots=RobotTasks(Store(tmp_path/'robot.sqlite'))
    task=robots.simulate(m.snapshot()['robot_task_suggestions'][0],m.frame(),m.grid)
    assert task['status']=='completed' and task['route'][-1]==task['goal']
    assert not task['hardware_dispatched']
    assert robots.feedback(task['feedback'])['status']=='completed'
    with pytest.raises(ValueError):robots.feedback(dict(task['feedback'],robot_id='other'))
    offered=robots.offer(m.snapshot()['robot_task_suggestions'][0],'robot-01')
    with pytest.raises(ValueError):robots.feedback(dict(offered,status='completed',sequence=0,position=offered['goal']))
    robots.invalidate('input changed')
    assert offered['status']=='cancelled'
