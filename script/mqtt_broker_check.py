"""Authenticated broker loopback check; no CSE or sensor data is changed."""
import argparse
import getpass
import secrets
import threading
import uuid

import paho.mqtt.client as mqtt


def check(host, port, username, password, expect_denied=False):
    connected = threading.Event()
    subscribed = threading.Event()
    received = threading.Event()
    result = {}
    token = uuid.uuid4().hex
    topic = 'rescuetwin/diagnostics/' + token
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id='rt-check-' + token[:12], protocol=mqtt.MQTTv311)
    client.username_pw_set(username, password)
    client.reconnect_delay_set(min_delay=10, max_delay=10)

    def on_connect(c, userdata, flags, reason_code, properties):
        result['connect'] = reason_code
        connected.set()

    def on_subscribe(c, userdata, mid, reasons, properties):
        result['subscribe_ok'] = bool(reasons) and all(not r.is_failure for r in reasons)
        subscribed.set()

    def on_message(c, userdata, message):
        if message.topic == topic and message.payload == token.encode():
            received.set()

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect(host, port, keepalive=20)
        client.loop_start()
        if not connected.wait(8):
            raise RuntimeError('No MQTT connection response within 8 seconds.')
        reason = result['connect']
        if expect_denied:
            if reason.is_failure:
                print('PASS: incorrect password rejected.')
                return
            raise RuntimeError('Incorrect password was accepted!')
        if reason.is_failure:
            raise RuntimeError(f'Login rejected: {reason}. Check the account and password.')
        print(f'PASS: authenticated as {username}.')
        rc, _ = client.subscribe(topic, qos=1)
        if rc != mqtt.MQTT_ERR_SUCCESS or not subscribed.wait(8) or not result.get('subscribe_ok'):
            raise RuntimeError('Broker did not accept the test subscription.')
        print('PASS: subscription acknowledged.')
        message = client.publish(topic, payload=token, qos=1, retain=False)
        message.wait_for_publish(timeout=8)
        if not message.is_published() or not received.wait(8):
            raise RuntimeError('Published message was not acknowledged and received correctly.')
        print('PASS: published message acknowledged and received unchanged.')
    finally:
        client.disconnect()
        client.loop_stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=1883)
    parser.add_argument('--username', default='node-01')
    args = parser.parse_args()
    password = getpass.getpass(f'MQTT password for {args.username}: ')
    if not password:
        raise RuntimeError('Password must not be empty.')
    check(args.host, args.port, args.username, password)
    check(args.host, args.port, args.username,
          'invalid-' + secrets.token_hex(24), expect_denied=True)
    print('BROKER CHECK PASSED. This does not yet verify oneM2M uploads into ACME.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit('CHECK FAILED: ' + str(error))
    except KeyboardInterrupt:
        raise SystemExit('Check cancelled.')
