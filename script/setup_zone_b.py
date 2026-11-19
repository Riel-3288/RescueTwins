"""Start a separate Zone B MN and verify routing using isolated test resources."""
import argparse
import configparser
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid

import requests

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'acme-zone-b'
BASE = 'http://127.0.0.1:8002/~/id-mn-zone-b/mn-zone-b'
REMOTE = 'http://127.0.0.1:7599/~/id-mn-zone-b/mn-zone-b'
AE_NAME, ORIGIN = 'zone-b-connection-test', 'CZoneBConnectionTest'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true', help='Start MN-B in the background if its port is free')
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    # Generated credentials and persistent database must never enter Git.
    (RUNTIME / '.gitignore').write_text('*\n', encoding='utf-8')
    target = RUNTIME / 'acme.ini'
    if not target.exists():
        template = (ROOT / 'cse-config' / 'zone-b' / 'acme.ini.example').read_text(encoding='utf-8')
        target.write_text(template.replace('CHANGE_ME_LOCAL_SECRET', secrets.token_hex(24)), encoding='utf-8')
    config = configparser.ConfigParser(interpolation=None)
    config.read(target, encoding='utf-8')
    expected = {'cseType': 'MN', 'cseID': 'id-mn-zone-b', 'cseName': 'mn-zone-b', 'httpPort': '8002',
                'registrarCseID': 'Mobius4', 'registrarCseName': 'Mobius', 'registrarCsePort': '7599'}
    for key, value in expected.items():
        if config.get('basic.config', key) != value:
            raise RuntimeError(f'Existing config differs at {key}; not overwritten.')

    session = requests.Session()
    session.trust_env = False

    def call(method, base=BASE, path='', origin='CAdmin', ty=None, body=None):
        headers = {'X-M2M-Origin': origin, 'X-M2M-RI': uuid.uuid4().hex,
                   'X-M2M-RVI': '4', 'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json' + (f';ty={ty}' if ty else '')
        response = session.request(method, base + path, headers=headers, json=body, timeout=6)
        try:
            data = response.json()
        except ValueError:
            data = {'response': response.text[:300]}
        return response, data

    def require(response, data, label, expected_status=200):
        if response.status_code != expected_status:
            raise RuntimeError(f'{label}: HTTP {response.status_code}: {data}')
        rsc = response.headers.get('X-M2M-RSC')
        if rsc and not 2000 <= int(rsc) < 3000:
            raise RuntimeError(f'{label}: oneM2M {rsc}: {data}')

    with socket.socket() as sock:
        sock.settimeout(1)
        occupied = sock.connect_ex(('127.0.0.1', 8002)) == 0
    if not occupied:
        if not args.start:
            raise RuntimeError('MN-B is not running. Use --start, or start ACME with --base-directory .\\acme-zone-b.')
        with (RUNTIME / 'runtime.log').open('a', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'acmecse', '--headless',
                '--base-directory', str(RUNTIME)], cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        (RUNTIME / 'process.json').write_text(json.dumps({'pid': child.pid, 'port': 8002}), encoding='utf-8')
        deadline = time.monotonic() + 40
        while True:
            if child.poll() is not None:
                raise RuntimeError('MN-B exited; inspect acme-zone-b/runtime.log.')
            try:
                response, data = call('GET')
                if response.status_code == 200:
                    break
            except requests.RequestException:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('MN-B startup timed out; inspect its runtime.log.')
            time.sleep(.5)
    response, data = call('GET')
    require(response, data, 'Read MN-B CSEBase')
    cse = data.get('m2m:cb', {})
    if cse.get('csi') != '/id-mn-zone-b' or cse.get('rn') != 'mn-zone-b':
        raise RuntimeError('Port 8002 is not the expected MN-B; no resources provisioned.')
    print('PASS: independent MN-B ready on port 8002.', flush=True)

    response, data = call('GET', path='/' + AE_NAME)
    if response.status_code == 404:
        response, data = call('POST', origin=ORIGIN, ty=2, body={'m2m:ae': {
            'rn': AE_NAME, 'api': 'NRescueTwin.zoneB.connectionTest', 'rr': False,
            'srv': ['4'], 'lbl': ['RescueTwin', 'zone-B', 'connection-test']}})
        require(response, data, 'Create isolated test AE', 201)
    else:
        require(response, data, 'Read existing isolated test AE')
    if data.get('m2m:ae', {}).get('aei') != ORIGIN:
        raise RuntimeError('Test AE identity differs; no existing resource overwritten.')
    policy = {'pv': {'acr': [{'acor': [ORIGIN], 'acop': 3}, {'acor': ['CAdmin'], 'acop': 63}]},
              'pvs': {'acr': [{'acor': ['CAdmin'], 'acop': 63}]}}
    response, data = call('GET', path='/' + AE_NAME + '/test-policy-v1')
    if response.status_code == 404:
        response, data = call('POST', path='/' + AE_NAME, ty=1,
                              body={'m2m:acp': {'rn': 'test-policy-v1', **policy}})
        require(response, data, 'Create test ACP', 201)
    else:
        require(response, data, 'Read test ACP')
    acp = data.get('m2m:acp', {})
    if any(acp.get(key) != value for key, value in policy.items()):
        raise RuntimeError('Existing test ACP differs; not overwritten.')
    path = '/' + AE_NAME + '/_connection_test'
    response, data = call('GET', path=path)
    if response.status_code == 404:
        response, data = call('POST', path='/' + AE_NAME, ty=3,
            body={'m2m:cnt': {'rn': '_connection_test', 'mni': 20, 'acpi': [acp['ri']]}})
        require(response, data, 'Create test container', 201)
    else:
        require(response, data, 'Read test container')
    container = data.get('m2m:cnt', {})
    if container.get('mni') != 20 or container.get('acpi') != [acp['ri']]:
        raise RuntimeError('Existing test container differs; not overwritten.')
    sample = {'zone_id': 'B', 'source': 'simulation', 'purpose': 'cross-CSE connectivity test',
              'event_id': uuid.uuid4().hex, 'observed_at': datetime.now(timezone.utc).isoformat()}
    response, data = call('POST', path=path, origin=ORIGIN, ty=4,
                          body={'m2m:cin': {'con': json.dumps(sample)}})
    require(response, data, 'Create test record', 201)
    instance_name = data['m2m:cin']['rn']
    response, data = call('GET', path=path + '/' + instance_name, origin=ORIGIN)
    require(response, data, 'Read test record directly')
    if json.loads(data['m2m:cin']['con']) != sample:
        raise RuntimeError('Direct read-back differs.')
    print('PASS: isolated test record written and read on MN-B.', flush=True)
    deadline = time.monotonic() + 80
    while True:
        try:
            response, data = call('GET', base=REMOTE, path=path + '/' + instance_name,
                                  origin='/id-mn-zone-b/' + ORIGIN)
            if response.status_code == 200:
                break
        except requests.RequestException:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Mobius forwarding not ready; inspect registration and Docker-to-host connectivity.')
        time.sleep(2)
    require(response, data, 'Read MN-B record through Mobius')
    if json.loads(data['m2m:cin']['con']) != sample:
        raise RuntimeError('Cross-CSE payload differs.')
    print('PASS: same MN-B record retrieved through Mobius.', flush=True)
    report = {'result': 'passed', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'cse_id': '/id-mn-zone-b', 'cse_name': 'mn-zone-b', 'http_port': 8002,
              'direct_base': BASE, 'via_in_base': REMOTE,
              'read_originator': '/id-mn-zone-b/' + ORIGIN,
              'test_resource': path, 'event_id': sample['event_id'],
              'checks': ['MN-B identity', 'isolated ACP-protected test write/read', 'cross-CSE exact record retrieval'],
              'scope': 'Same-computer Zone B platform instance; no physical sensors, handover or MQTT verified.'}
    (ROOT / 'docs' / 'zone-b-test-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('MN-B CHECK PASSED. Test AE is not a physical sensor node; MN-A resources unchanged.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, requests.RequestException, configparser.Error) as error:
        sys.exit('ZONE B ERROR: ' + str(error))
