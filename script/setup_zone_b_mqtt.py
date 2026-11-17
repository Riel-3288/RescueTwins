"""Enable Zone B MQTT with separate broker accounts and verify the full route."""
import configparser
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import requests

ROOT = Path(__file__).resolve().parents[1]
DOCKER = Path.home() / 'AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
RUNTIME = ROOT / 'acme-zone-b'
LOCAL = ROOT / '.mqtt-local'
BROKER = 'rescuetwin-zone-a-mqtt-broker-1'


def run(args, **kwargs):
    if isinstance(kwargs.get('input'), str):
        # Binary stdin avoids Windows translating LF to CRLF in broker passwords.
        kwargs['input'] = kwargs['input'].encode('utf-8')
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        # Do not include process arguments or stdin: they can contain credentials.
        raise RuntimeError('Command failed: ' + result.stderr.decode('utf-8', errors='replace')[-800:])
    return result.stdout.decode('utf-8', errors='replace')


def configure():
    target = RUNTIME / 'acme.ini'
    config = configparser.ConfigParser(interpolation=None)
    # ACME resolves camel-case template variables case-sensitively. Preserve
    # canonical spelling even when repairing a file written by ConfigParser.
    template = configparser.ConfigParser(interpolation=None)
    template.optionxform = str
    template.read(ROOT / 'cse-config/zone-b/acme.ini.example', encoding='utf-8')
    canonical = {key.lower(): key for section in template.sections() for key in template[section]}
    canonical['usetls'] = 'useTLS'
    config.optionxform = lambda key: canonical.get(key.lower(), key)
    config.read(target, encoding='utf-8')
    if config.get('basic.config', 'cseID') != 'id-mn-zone-b':
        raise RuntimeError('Expected existing Zone B config was not found.')
    credentials_file = LOCAL / 'zone-b-credentials.json'
    if credentials_file.exists():
        credentials = json.loads(credentials_file.read_text(encoding='utf-8'))
    else:
        existing_users = {line.split(':', 1)[0] for line in
                          (LOCAL / 'passwords').read_text(encoding='utf-8').splitlines()}
        if existing_users & {'acme-zone-b', 'zone-b-test'}:
            raise RuntimeError('Zone B broker account already exists without the local credentials file; not overwritten.')
        credentials = {'server_username': 'acme-zone-b', 'server_password': secrets.token_urlsafe(32),
                       'test_username': 'zone-b-test', 'test_password': secrets.token_urlsafe(32)}
        credentials_file.write_text(json.dumps(credentials, indent=2) + '\n', encoding='utf-8')
    if credentials['server_username'] != 'acme-zone-b' or credentials['test_username'] != 'zone-b-test':
        raise RuntimeError('Unexpected local broker usernames; no accounts changed.')

    password_file = LOCAL / 'passwords'
    backup = LOCAL / 'passwords.before-zone-b'
    if not backup.exists():
        shutil.copyfile(password_file, backup)
    edited = LOCAL / 'passwords.zone-b-edit'
    shutil.copyfile(password_file, edited)
    # Passwords are passed on stdin, never printed or placed in the host command line.
    shell = ('set -e; IFS= read -r pw; mosquitto_passwd -b /work/passwords.zone-b-edit acme-zone-b "$pw"; '
             'IFS= read -r pw; mosquitto_passwd -b /work/passwords.zone-b-edit zone-b-test "$pw"')
    run([str(DOCKER), 'run', '--rm', '-i', '--user', '0', '-v', str(LOCAL) + ':/work',
         '--entrypoint', 'sh', 'eclipse-mosquitto:2', '-c', shell],
        input=credentials['server_password'] + '\n' + credentials['test_password'] + '\n')
    # Preserve existing accounts and the bind-mounted file itself, then reload in place.
    before = dict(line.split(':', 1) for line in password_file.read_text(encoding='utf-8').splitlines() if line)
    after = dict(line.split(':', 1) for line in edited.read_text(encoding='utf-8').splitlines() if line)
    if any(after.get(user) != hashed for user, hashed in before.items() if user not in ('acme-zone-b', 'zone-b-test')):
        raise RuntimeError('Existing broker account changed unexpectedly; original password file was preserved.')
    password_file.write_bytes(edited.read_bytes())
    edited.unlink()
    # Docker Desktop can keep a bind-mounted file's old contents after the
    # password utility replaces it. Recreate the broker to remount it reliably.
    compose = DOCKER.parent.parent / 'cli-plugins/docker-compose.exe'
    run([str(compose), '-f', str(ROOT / 'cse-config/mqtt/compose.yaml'),
         'up', '-d', '--force-recreate', 'broker'])
    print('PASS: Zone B accounts added; pre-existing broker account hashes preserved.', flush=True)
    from mqtt_broker_check import check
    check('127.0.0.1', 1883, credentials['server_username'], credentials['server_password'])

    config['mqtt'] = {'enable': 'true', 'address': '127.0.0.1', 'port': '1883'}
    config['mqtt.security'] = {'username': credentials['server_username'],
                             'password': credentials['server_password'], 'useTLS': 'false'}
    with target.open('w', encoding='utf-8') as handle:
        config.write(handle)
    # Restart only the Zone B process recorded by its launcher; verify ownership first.
    process_file = RUNTIME / 'process.json'
    if process_file.exists():
        pid = int(json.loads(process_file.read_text(encoding='utf-8'))['pid'])
        ps = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
              "if ($p) { if ($p.CommandLine -notlike '*acmecse*' -or $p.CommandLine -notlike '*acme-zone-b*') "
              "{ throw 'Process is not the recorded Zone B CSE' }; "
              f"Stop-Process -Id {pid} -ErrorAction Stop; "
              f"Wait-Process -Id {pid} -Timeout 10 -ErrorAction SilentlyContinue }}; exit 0")
        run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', ps])
    # Launcher checks registration, resource ownership and cross-CSE HTTP routing again.
    result = run([sys.executable, '-X', 'utf8', str(ROOT / 'script/setup_zone_b.py'), '--start'])
    print(result.strip(), flush=True)
    return credentials


def verify(credentials):
    events, responses = threading.Event(), queue.Queue()
    subscription = threading.Event()
    state = {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id='rt-zone-b-check-' + uuid.uuid4().hex[:12], protocol=mqtt.MQTTv311)
    client.username_pw_set(credentials['test_username'], credentials['test_password'])
    request_topic = '/oneM2M/req/CZoneBConnectionTest/id-mn-zone-b/json'
    response_topic = '/oneM2M/resp/CZoneBConnectionTest/id-mn-zone-b/json'

    def connected(c, userdata, flags, reason, properties):
        state['connection'] = reason
        events.set()

    def subscribed(c, userdata, mid, reasons, properties):
        state['subscription'] = bool(reasons) and all(not r.is_failure for r in reasons)
        subscription.set()

    def message(c, userdata, msg):
        try:
            data = json.loads(msg.payload)
            responses.put(data.get('m2m:rsp', data))
        except (ValueError, UnicodeError, AttributeError):
            pass

    def exchange(op, target, expected, content=None):
        request_id = uuid.uuid4().hex
        primitive = {'op': op, 'to': target, 'fr': 'CZoneBConnectionTest', 'rqi': request_id, 'rvi': '4'}
        if content is not None:
            primitive.update(ty=4, pc=content)
        # A new MN may take a moment to finish MQTT subscription. Retry only with
        # a new exchange after a timeout; no sensor container is used by this check.
        sent = client.publish(request_topic, json.dumps(primitive), qos=1, retain=False)
        sent.wait_for_publish(timeout=8)
        if not sent.is_published():
            raise RuntimeError('MQTT publish not acknowledged.')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                reply = responses.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if reply.get('rqi') != request_id:
                continue
            if reply.get('rsc') != expected:
                raise RuntimeError('Unexpected oneM2M response: ' + json.dumps(reply))
            return reply['pc']['m2m:cin']
        raise RuntimeError('No matching MN-B MQTT response; inspect its broker connection log.')

    client.on_connect, client.on_subscribe, client.on_message = connected, subscribed, message
    try:
        client.connect('127.0.0.1', 1883, keepalive=30)
        client.loop_start()
        if not events.wait(8) or state['connection'].is_failure:
            raise RuntimeError('Zone B test broker login failed.')
        client.subscribe(response_topic, qos=1)
        if not subscription.wait(8) or not state.get('subscription'):
            raise RuntimeError('Response subscription failed.')
        print('PASS: separate Zone B test account logged in and subscribed.', flush=True)
        sample = {'zone_id': 'B', 'source': 'simulation', 'transport': 'mqtt',
                  'purpose': 'isolated MQTT connectivity test', 'event_id': uuid.uuid4().hex,
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        cin = exchange(1, '/id-mn-zone-b/mn-zone-b/zone-b-connection-test/_connection_test', 2001,
                       {'m2m:cin': {'con': json.dumps(sample)}})
        target = '/id-mn-zone-b/' + cin['ri']
        result = exchange(2, target, 2000)
        if result['ri'] != cin['ri'] or json.loads(result['con']) != sample:
            raise RuntimeError('MQTT read-back differs.')
        print('PASS: MN-B MQTT create (2001) and read-back (2000).', flush=True)
        session = requests.Session(); session.trust_env = False
        response = session.get('http://127.0.0.1:7599/~' + target, headers={
            'X-M2M-Origin': '/id-mn-zone-b/CZoneBConnectionTest', 'X-M2M-RI': uuid.uuid4().hex,
            'X-M2M-RVI': '4', 'Accept': 'application/json'}, timeout=10)
        response.raise_for_status()
        result = response.json()['m2m:cin']
        if result['ri'] != cin['ri'] or json.loads(result['con']) != sample:
            raise RuntimeError('Mobius returned a different record.')
        print('PASS: same MQTT-uploaded MN-B record retrieved through Mobius HTTP.', flush=True)
        # Reuse the existing negative-login test without exposing either password.
        from mqtt_broker_check import check
        check('127.0.0.1', 1883, credentials['test_username'],
              'invalid-' + secrets.token_hex(24), expect_denied=True)
        report = {'result': 'passed', 'checked_at': datetime.now(timezone.utc).isoformat(),
                  'cse_id': '/id-mn-zone-b', 'broker_port': 1883, 'tls': False,
                  'request_topic': request_topic, 'response_topic': response_topic,
                  'checks': ['test account login', 'MQTT CIN create 2001', 'MQTT retrieve 2000',
                             'same record through Mobius HTTP', 'incorrect password rejected'],
                  'event_id': sample['event_id'], 'scope': 'Zone B test container only; no physical device MQTT verified.'}
        (ROOT / 'docs/zone-b-mqtt-test-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print('ZONE B MQTT CHECK PASSED. Credentials remain in ignored local files.')
    finally:
        client.disconnect(); client.loop_stop()


if __name__ == '__main__':
    try:
        verify(configure())
    except (RuntimeError, OSError, ValueError, KeyError, requests.RequestException, configparser.Error) as error:
        sys.exit('ZONE B MQTT ERROR: ' + str(error))
