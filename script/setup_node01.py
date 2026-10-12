"""Create the first RescueTwin sensor AE and containers on an existing ACME CSE."""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone

import requests

SENSORS = {
    'temperature': 'degC', 'humidity': 'percent', 'pressure': 'hPa',
    'smoke_raw': 'adc', 'co_raw': 'adc', 'presence': 'boolean',
    'presence_distance': 'm', 'thermal_frame': 'degC', 'status': None,
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000/~/id-in/cse-in')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    session = requests.Session()
    session.trust_env = False

    def call(method, path='', origin='CAdmin', ty=None, body=None):
        headers = {'X-M2M-Origin': origin, 'X-M2M-RI': uuid.uuid4().hex,
                   'X-M2M-RVI': '4', 'Accept': 'application/json',
                   'Content-Type': 'application/json' + (f';ty={ty}' if ty else '')}
        r = session.request(method, base + path, headers=headers, json=body, timeout=10)
        try:
            data = r.json()
        except ValueError:
            data = {'response': r.text}
        return r, data

    def require(r, data, statuses, label):
        rsc = r.headers.get('X-M2M-RSC')
        if r.status_code not in statuses or (rsc and not 2000 <= int(rsc) < 3000):
            raise RuntimeError(f'{label}: HTTP {r.status_code}, oneM2M {rsc}: {data}')

    r, data = call('GET')
    require(r, data, (200,), 'Read CSEBase')

    r, data = call('GET', '/node-01')
    if r.status_code == 404:
        r, data = call('POST', origin='CNode01', ty=2, body={
            'm2m:ae': {'rn': 'node-01', 'api': 'NRescueTwin.sensor', 'rr': False,
                       'srv': ['4'], 'lbl': ['RescueTwin', 'sensor-node', 'zone-A']}})
        require(r, data, (201,), 'Register node-01')
        print('Created AE node-01')
    else:
        require(r, data, (200,), 'Read existing node-01')
        print('Found existing AE node-01')
    ae = data.get('m2m:ae', {})
    origin = ae.get('aei')
    if not origin or ae.get('rn') != 'node-01':
        raise RuntimeError('node-01 is not a valid AE; stopped without replacing it.')

    expected_pv = {'acr': [{'acor': [origin], 'acop': 3},
                           {'acor': ['CAdmin'], 'acop': 63}]}
    expected_pvs = {'acr': [{'acor': ['CAdmin'], 'acop': 63}]}
    policy_path = '/node-01/sensor-policy-v1'
    r, data = call('GET', policy_path)
    if r.status_code == 404:
        r, data = call('POST', '/node-01', ty=1, body={'m2m:acp': {
            'rn': 'sensor-policy-v1', 'pv': expected_pv, 'pvs': expected_pvs}})
        require(r, data, (201,), 'Create sensor ACP')
    else:
        require(r, data, (200,), 'Read existing sensor ACP')
    policy = data.get('m2m:acp', {})
    if policy.get('pv') != expected_pv or policy.get('pvs') != expected_pvs:
        raise RuntimeError('Existing sensor-policy-v1 differs; stopped without overwriting it.')
    policy_id = policy['ri']

    for name in list(SENSORS) + (['_connection_test'] if args.verify else []):
        path = '/node-01/' + name
        r, data = call('GET', path)
        if r.status_code == 404:
            r, data = call('POST', '/node-01', ty=3, body={'m2m:cnt': {
                'rn': name, 'mni': 5 if name == '_connection_test' else 100,
                'acpi': [policy_id]}})
            require(r, data, (201,), f'Create {name}')
            print('Created container:', name)
        else:
            require(r, data, (200,), f'Read {name}')
            actual_acpi = data.get('m2m:cnt', {}).get('acpi', [])
            # ACME normalizes local ACP references to their unstructured RI.
            if not any(ref in (policy_id, policy['ri']) for ref in actual_acpi):
                raise RuntimeError(f'{name}: existing ACP differs; stopped without overwriting it.')
            print('Found container:', name)

    if args.verify:
        sample = {'device_id': 'node-01', 'source': 'simulation',
                  'event_id': uuid.uuid4().hex, 'value': 28.6,
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        r, data = call('POST', '/node-01/_connection_test', origin=origin, ty=4,
                       body={'m2m:cin': {'con': json.dumps(sample)}})
        require(r, data, (201,), 'Test node upload')
        r, data = call('GET', '/node-01/_connection_test/la', origin=origin)
        require(r, data, (200,), 'Read back node upload')
        if json.loads(data['m2m:cin']['con']) != sample:
            raise RuntimeError('Read-back data differs from uploaded data')
        r, data = call('GET', '/node-01/_connection_test/la', origin='CNotAllowed')
        if r.status_code != 403:
            raise RuntimeError(f'Unexpected unauthorized read result: {r.status_code}')
        r, data = call('PUT', '/node-01/_connection_test', origin=origin,
                       body={'m2m:cnt': {'mni': 99}})
        if r.status_code != 403:
            raise RuntimeError(f'Unexpected unauthorized container update result: {r.status_code}')
        print('Verified: node upload/read-back; unauthorized read/update denied.')

    print('\nAE Originator:', origin)
    print('CSE base URL:', base)
    print('Upload method: POST; Content-Type: application/json;ty=4')
    for name in SENSORS:
        print(name + ': ' + base + '/node-01/' + name)
    print('Real sensor containers remain empty; verification uses _connection_test only.')

if __name__ == '__main__':
    try:
        main()
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
        print('SETUP FAILED:', exc, file=sys.stderr)
        sys.exit(1)
