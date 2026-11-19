"""Verify oneM2M MQTT CREATE/RETRIEVE and HTTP retrieval through Mobius."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import queue
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler
import uuid

import paho.mqtt.client as mqtt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=1883)
    parser.add_argument('--username', default='node-01')
    parser.add_argument('--mobius-url', default='http://127.0.0.1:7599')
    args = parser.parse_args()
    password = getpass.getpass(f'MQTT password for {args.username}: ')
    if not password:
        raise RuntimeError('Password must not be empty.')
    request_topic = '/oneM2M/req/CNode01/id-mn-zone-a/json'
    response_topic = '/oneM2M/resp/CNode01/id-mn-zone-a/json'
    connected, subscribed = threading.Event(), threading.Event()
    state, responses = {}, queue.Queue()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id='rt-onem2m-' + uuid.uuid4().hex[:12], protocol=mqtt.MQTTv311)
    client.username_pw_set(args.username, password)

    def on_connect(c, userdata, flags, reason, properties):
        state['connect'] = reason
        connected.set()

    def on_subscribe(c, userdata, mid, reasons, properties):
        state['subscribed'] = bool(reasons) and all(not r.is_failure for r in reasons)
        subscribed.set()

    def on_message(c, userdata, message):
        try:
            data = json.loads(message.payload)
            if isinstance(data, dict):
                responses.put(data.get('m2m:rsp', data))
        except (ValueError, UnicodeError):
            pass

    def exchange(operation, target, expected, content=None, resource_type=None):
        request_id = uuid.uuid4().hex
        body = {'op': operation, 'to': target, 'fr': 'CNode01', 'rqi': request_id, 'rvi': '4'}
        if content is not None:
            body['pc'] = content
        if resource_type is not None:
            body['ty'] = resource_type
        published = client.publish(request_topic, json.dumps(body), qos=1, retain=False)
        published.wait_for_publish(timeout=8)
        if not published.is_published():
            raise RuntimeError('Broker did not acknowledge the publish.')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                response = responses.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if response.get('rqi') != request_id:
                continue
            if response.get('rsc') != expected:
                raise RuntimeError(f'ACME response: {json.dumps(response, ensure_ascii=True)}')
            return response
        raise RuntimeError('No matching ACME response. Check MN MQTT connection, topics and broker logs.')

    client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
    try:
        client.connect(args.host, args.port, keepalive=30)
        client.loop_start()
        if not connected.wait(8):
            raise RuntimeError('No broker connection response.')
        if state['connect'].is_failure:
            raise RuntimeError(f'Broker login rejected: {state["connect"]}')
        print('PASS: broker login.')
        rc, _ = client.subscribe(response_topic, qos=1)
        if rc != mqtt.MQTT_ERR_SUCCESS or not subscribed.wait(8) or not state.get('subscribed'):
            raise RuntimeError('Response subscription rejected or timed out.')
        sample = {'device_id': 'node-01', 'zone_id': 'A', 'sensor_type': 'temperature',
                  'value': 28.6, 'unit': 'degC', 'source': 'simulation', 'quality': 'valid',
                  'observed_at': datetime.now(timezone.utc).isoformat(),
                  'event_id': uuid.uuid4().hex, 'transport': 'mqtt'}
        response = exchange(1, '/id-mn-zone-a/mn-zone-a/node-01/_connection_test', 2001,
                            {'m2m:cin': {'con': json.dumps(sample)}}, 4)
        instance = response['pc']['m2m:cin']
        ri = instance['ri']
        print('PASS: ACME created test record over MQTT (rsc 2001).')
        target = '/id-mn-zone-a/' + ri
        response = exchange(2, target, 2000)
        if json.loads(response['pc']['m2m:cin']['con']) != sample:
            raise RuntimeError('MQTT read-back differs from uploaded data.')
        print('PASS: same record read back over MQTT (rsc 2000).')
        req = Request(args.mobius_url.rstrip('/') + '/~' + target, headers={
            'X-M2M-Origin': '/id-mn-zone-a/CNode01', 'X-M2M-RI': uuid.uuid4().hex,
            'X-M2M-RVI': '4', 'Accept': 'application/json'})
        try:
            with build_opener(ProxyHandler({})).open(req, timeout=10) as result:
                returned = json.load(result)['m2m:cin']
            if returned['ri'] != ri or json.loads(returned['con']) != sample:
                raise RuntimeError('Mobius returned a different record.')
        except Exception as error:
            raise RuntimeError('MQTT write/read passed, but Mobius verification failed: ' + str(error)) from error
        print('PASS: same MQTT-uploaded record retrieved through Mobius over HTTP.')
        print('MQTT SMOKE TEST PASSED. Only _connection_test was written; sensor containers unchanged.')
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit('TEST FAILED: ' + str(error))
    except KeyboardInterrupt:
        raise SystemExit('Test cancelled.')
