import json
from types import SimpleNamespace

import pytest

from rescuetwin import native_read


def config():
    return dict(host='127.0.0.1', port=1883, originator='CReader', cse_id='id-mn-a',
                target='/id-mn-a/cse-mn-a/node-01/temperature/la', timeout_s=.01)


class Client:
    def __init__(self, status=2000, reject=False, reply=True, content=None):
        self.status, self.reject, self.reply = status, reject, reply
        self.content = content if content is not None else {'value': 26, 'unit': 'degC'}
        self.closed = False
        self.subscribed = False

    def subscribe(self, topic, qos):
        self.topic = topic
        self.subscribed = True
        self.on_subscribe(self, None, 1, [SimpleNamespace(is_failure=self.reject)], None)
        return 0, 1

    def publish(self, topic, payload, qos, retain):
        assert self.subscribed
        request = json.loads(payload)
        assert request['op'] == 2 and 'ty' not in request and 'pc' not in request
        assert retain is False
        self.request = request
        def deliver(rqi, retained=False):
            response = {'rqi': rqi, 'rsc': self.status,
                        'pc': {'m2m:cin': {'con': json.dumps(self.content)}}}
            self.on_message(self, None, SimpleNamespace(topic=self.topic, retain=retained,
                                                       payload=json.dumps(response).encode()))
        deliver('unrelated-request')
        deliver(request['rqi'], True)
        if self.reply:
            deliver(request['rqi'])
        return SimpleNamespace(rc=0)

    def disconnect(self):
        self.closed = True

    def loop_stop(self):
        pass


def wire(monkeypatch, client):
    monkeypatch.setattr(native_read, 'make_client', lambda *a, **k: client)
    monkeypatch.setattr(native_read, 'connect', lambda *a: None)


def test_retrieve_decodes_content_and_is_read_only(monkeypatch):
    client = Client()
    wire(monkeypatch, client)
    result = native_read.retrieve_latest(config())
    assert result['complete'] and result['rsc'] == 2000
    assert result['content'] == {'value': 26, 'unit': 'degC'}
    assert result['cin']['con'] == json.dumps(result['content'])
    assert client.closed


@pytest.mark.parametrize('client,message', [
    (Client(status=4103), 'rsc=4103'),
    (Client(reject=True), 'subscription'),
    (Client(reply=False), 'timed out'),
])
def test_rejects_denial_and_unrelated_or_retained_replies(monkeypatch, client, message):
    wire(monkeypatch, client)
    with pytest.raises(RuntimeError, match=message):
        native_read.retrieve_latest(config())
    assert client.closed


def test_credentials_required_before_connect(monkeypatch):
    cfg = dict(config(), username='reader', password_env='TEST_READ_PASSWORD')
    monkeypatch.delenv('TEST_READ_PASSWORD', raising=False)
    with pytest.raises(ValueError, match='password environment'):
        native_read.retrieve_latest(cfg)


def test_preview_does_not_connect_and_failure_replaces_old_report(monkeypatch, tmp_path):
    path, output = tmp_path/'config.json', tmp_path/'result.json'
    path.write_text(json.dumps(config()))
    monkeypatch.setattr(native_read, 'retrieve_latest', lambda cfg: pytest.fail('preview connected'))
    monkeypatch.setattr('sys.argv', ['native_read', '--config', str(path), '--output', str(output)])
    assert native_read.main() == 0
    assert not json.loads(output.read_text())['complete']
    path.write_text('{}')
    assert native_read.main() == 1
    assert json.loads(output.read_text())['error'] == 'ValueError'


def test_real_mqtt_retrieves_latest_cin(live):
    from rescuetwin.mqtt import native_cin
    value = {'device_id': 'node-01', 'sensor_type': 'temperature', 'value': 27.5, 'unit': 'degC'}
    native_cin(live['mqtt'], 'CNode01', 'id-mn-a', '/id-mn-a/cse-mn-a/node-01/temperature', value)
    cfg = dict(live['mqtt'], originator='CNode01', cse_id='id-mn-a',
               target='/id-mn-a/cse-mn-a/node-01/temperature/la')
    result = native_read.retrieve_latest(cfg)
    assert result['complete'] and result['content'] == value
