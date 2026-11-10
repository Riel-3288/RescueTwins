import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from rescuetwin.core import load_config,validate_reading
from rescuetwin.native import native_plan,native_upload

ROOT=Path(__file__).resolve().parents[1]


def row(**change):
    return dict(device_id='node-01',zone_id='A',sensor_type='smoke_raw',value=648.5,
                unit='mV',source='physical',quality='valid',observed_at='2026-11-09T04:00:00Z',**change)


def test_mv_contract_accepts_voltage_not_adc_counts():
    cfg=load_config(ROOT/'config.team.example.json')
    validate_reading(row(),cfg['devices'])
    validate_reading(dict(row(),value=5000.25),cfg['devices'])
    for changes in [{'unit':'adc'},{'value':-1},{'value':True},{'value':float('inf')}]:
        with pytest.raises(ValueError):validate_reading(dict(row(),**changes),cfg['devices'])


def test_file_preview_has_exact_team_topics_and_preserves_payload(tmp_path):
    cfg=load_config(ROOT/'config.team.example.json')
    p=tmp_path/'reading.jsonl';p.write_text(json.dumps(row())+'\n')
    plan=native_plan(cfg,'A',p)
    assert plan['request_topic']=='/oneM2M/req/CNode01/id-mn-zone-a/json'
    assert plan['response_topic']=='/oneM2M/resp/CNode01/id-mn-zone-a/json'
    assert plan['events'][0]['target']=='/id-mn-zone-a/mn-zone-a/node-01/smoke_raw'
    assert plan['events'][0]['reading']==row()
    sender=Mock()
    assert not native_upload(cfg,plan,sender=sender)['sent']
    sender.assert_not_called()


def test_invalid_batch_prevents_writes_and_credentials_are_required(tmp_path,monkeypatch):
    cfg=load_config(ROOT/'config.team.example.json')
    p=tmp_path/'bad.json';p.write_text(json.dumps([row(),dict(row(),device_id='other')]))
    plan=native_plan(cfg,'A',p)
    sender=Mock()
    with pytest.raises(ValueError):native_upload(cfg,plan,send=True,sender=sender)
    monkeypatch.delenv('TEAM_NODE01_MQTT_PASSWORD',raising=False)
    with pytest.raises(ValueError,match='password environment'):
        native_upload(cfg,plan,skip_invalid=True,send=True,sender=sender)
    sender.assert_not_called()


def test_creation_requires_rsc_2001_and_reports_partial_failure(tmp_path,monkeypatch):
    cfg=load_config(ROOT/'config.team.example.json')
    p=tmp_path/'values.json';p.write_text(json.dumps([row(),dict(row(),value=700)]))
    plan=native_plan(cfg,'A',p)
    monkeypatch.setenv('TEAM_NODE01_MQTT_PASSWORD','fixture-only')
    send=Mock(side_effect=[{'rqi':'one','rsc':2001},{'rqi':'two','rsc':2000}])
    result=native_upload(cfg,plan,send=True,sender=send)
    assert not result['complete'] and result['events'][0]['created']
    assert result['events'][1]['created'] is False


def test_real_native_mv_upload_and_readback(live,tmp_path):
    from rescuetwin.onem2m import Cse
    cfg=copy.deepcopy(live)
    cfg['native_mqtt']={'A':dict(cfg['mqtt'],device_id='node-01',originator='CNode01',
                       cse_id='id-mn-a',cse_name='cse-mn-a',allow_upload=True)}
    p=tmp_path/'mv.json';p.write_text(json.dumps(row()))
    result=native_upload(cfg,native_plan(cfg,'A',p),send=True)
    assert result['complete'] and result['events'][0]['rsc']==2001
    assert Cse(cfg['cse']['A'],'CNode01').latest('/node-01/smoke_raw')==row()
