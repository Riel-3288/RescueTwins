"""Read one latest CIN through oneM2M MQTT; never register or write resources."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
import uuid

import paho.mqtt.client as mqtt

from .core import canonical, utcnow
from .mqtt import connect, make_client


def read_plan(config):
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object')
    for key in ('host', 'originator', 'cse_id', 'target'):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip() or 'REPLACE' in value:
            raise ValueError(f'Configure {key} with the value confirmed by the CSE owner')
    originator, cse_id = config['originator'], config['cse_id'].strip('/')
    if any(char in originator + cse_id for char in '/+#') or not cse_id:
        raise ValueError('Use MQTT topic-safe originator and cse_id supplied by the CSE owner')
    if not config['target'].startswith('/') or not config['target'].endswith('/la'):
        raise ValueError('target must be the full latest-CIN resource path ending in /la')
    port = config.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('port must be an integer from 1 to 65535')
    timeout = config.get('timeout_s', 10)
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise ValueError('timeout_s must be greater than 0 and at most 120')
    return {'mode': 'read_only', 'host': config['host'], 'port': port,
            'target': config['target'],
            'request_topic': f'/oneM2M/req/{originator}/{cse_id}/json',
            'response_topic': f'/oneM2M/resp/{originator}/{cse_id}/json'}


def retrieve_latest(config):
    plan = read_plan(config)
    if config.get('username') and not os.getenv(config.get('password_env', 'MQTT_PASSWORD')):
        raise ValueError('Set the configured MQTT password environment variable privately')
    request_id = uuid.uuid4().hex
    timeout = config.get('timeout_s', 10)
    client = make_client(config, 'mec-read-' + uuid.uuid4().hex, clean_session=True)
    subscribed, replied = threading.Event(), threading.Event()
    failures, responses = [], []

    def on_subscribe(c, userdata, mid, reasons, properties):
        failures.extend(reason for reason in reasons if reason.is_failure)
        subscribed.set()

    def on_message(c, userdata, message):
        if message.topic != plan['response_topic'] or message.retain or len(message.payload) > 65536:
            return
        try:
            response = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(response, dict) and response.get('rqi') == request_id:
            responses.append(response)
            replied.set()

    client.on_subscribe, client.on_message = on_subscribe, on_message
    try:
        connect(client, config)
        rc, _ = client.subscribe(plan['response_topic'], qos=1)
        if rc != mqtt.MQTT_ERR_SUCCESS or not subscribed.wait(timeout) or failures:
            raise RuntimeError('MQTT response subscription rejected or timed out; check topic ACL')
        primitive = {'op': 2, 'fr': config['originator'], 'to': config['target'],
                     'rqi': request_id, 'rvi': config.get('rvi', '4')}
        published = client.publish(plan['request_topic'], canonical(primitive), qos=1, retain=False)
        if published.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError('MQTT read request could not be queued')
        if not replied.wait(timeout):
            raise RuntimeError('CSE response timed out; check request ACL, CSE MQTT connection and response routing')
        response = responses[0]
        if type(response.get('rsc')) is not int or response['rsc'] != 2000:
            raise RuntimeError(f"CSE read failed: rsc={response.get('rsc')}; check resource path and read ACP")
        pc = response.get('pc')
        cin = pc.get('m2m:cin') if isinstance(pc, dict) else None
        if not isinstance(cin, dict) or 'con' not in cin:
            raise RuntimeError('CSE returned success without a CIN containing con')
        content = cin['con']
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                pass
        return dict(plan, complete=True, received_at=utcnow(), rqi=request_id,
                    rsc=response['rsc'], cin=cin, content=content)
    finally:
        client.disconnect()
        client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--read', action='store_true', help='Connect and issue one RETRIEVE; default is offline preview')
    parser.add_argument('--output', default='runtime/mqtt-read-latest.json')
    args = parser.parse_args()
    result = {'mode': 'read_only', 'complete': False}
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        plan = read_plan(config)
        result.update(plan)
        if args.read:
            result = retrieve_latest(config)
        else:
            result['note'] = 'Offline preview only. Add --read to retrieve one latest record.'
    except (ValueError, KeyError, OSError, RuntimeError, TypeError) as exc:
        result.update(complete=False, error=type(exc).__name__, detail=str(exc))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    sys.exit(main())
