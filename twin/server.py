"""Local RescueTwin dashboard. Python 3.12+, standard library only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request
import uuid

ROOT = Path(__file__).resolve().parent
MOBIUS = os.getenv('TWIN_MOBIUS_URL', 'http://127.0.0.1:7599').rstrip('/')
MN = os.getenv('TWIN_MN_URL', 'http://127.0.0.1:8001/~/id-mn-zone-a/mn-zone-a').rstrip('/')
REMOTE = '/~/id-mn-zone-a/mn-zone-a/node-01'
AE_NAME, AE_ORIGIN = 'rescuetwin-web', 'CRescueTwinWeb'
SENSORS = {'temperature': ('Temperature', 'degC'), 'humidity': ('Humidity', 'percent'),
           'pressure': ('Pressure', 'hPa'), 'presence': ('Presence', 'boolean'),
           'smoke_raw': ('MQ-2 raw', 'adc'), 'co_raw': ('MQ-7 raw', 'adc')}


def request(method, url, origin, body=None, ty=None):
    headers = {'X-M2M-Origin': origin, 'X-M2M-RI': uuid.uuid4().hex,
               'X-M2M-RVI': '4', 'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json' + (f';ty={ty}' if ty else '')
    req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                  headers=headers, method=method)
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        try:
            data = json.load(error)
        except (ValueError, UnicodeError):
            data = {'m2m:dbg': f'HTTP {error.code}'}
        return error.code, data
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError('Cannot connect to the platform. Check Mobius, MN and the registered address.') from error


def checked(method, url, origin, body=None, ty=None):
    code, data = request(method, url, origin, body, ty)
    if code not in (200, 201):
        raise RuntimeError(f'HTTP {code}: {data.get("m2m:dbg", data)}')
    return data


def setup():
    """Register the web AE on IN and add a separate read-only ACP on MN."""
    url = MOBIUS + '/Mobius/' + AE_NAME
    code, data = request('GET', url, AE_ORIGIN)
    if code == 404:
        data = checked('POST', MOBIUS + '/Mobius', AE_ORIGIN, {'m2m:ae': {
            'rn': AE_NAME, 'api': 'NRescueTwin.web', 'rr': False, 'srv': ['4'],
            'lbl': ['RescueTwin', 'dashboard']}}, 2)
    elif code != 200:
        raise RuntimeError(f'Cannot inspect web AE: HTTP {code}: {data}')
    aei = data['m2m:ae']['aei']
    if aei != AE_ORIGIN:
        raise RuntimeError('Existing web AE has a different identity; nothing overwritten.')
    reader = '/Mobius4/' + aei
    admin = os.getenv('TWIN_MN_ADMIN', 'CAdmin')
    policy_url = MN + '/node-01/twin-reader-v1'
    pv = {'acr': [{'acor': [reader], 'acop': 2}, {'acor': [admin], 'acop': 63}]}
    pvs = {'acr': [{'acor': [admin], 'acop': 63}]}
    code, policy = request('GET', policy_url, admin)
    if code == 404:
        policy = checked('POST', MN + '/node-01', admin, {'m2m:acp': {
            'rn': 'twin-reader-v1', 'pv': pv, 'pvs': pvs}}, 1)
    elif code != 200:
        raise RuntimeError(f'Cannot inspect reader ACP: HTTP {code}: {policy}')
    acp = policy['m2m:acp']
    if acp['pv'] != pv or acp['pvs'] != pvs:
        raise RuntimeError('Existing twin-reader-v1 differs; nothing overwritten.')
    for name in SENSORS:
        url = MN + '/node-01/' + name
        container = checked('GET', url, admin)['m2m:cnt']
        refs = container.get('acpi', [])
        if acp['ri'] not in refs and '/id-mn-zone-a/' + acp['ri'] not in refs:
            # Preserve the existing device-upload ACP; add web read permission separately.
            checked('PUT', url, admin, {'m2m:cnt': {'acpi': refs + [acp['ri']]}})
    # Validate the exact forwarding route; an empty container is a legitimate 404.
    for name in SENSORS:
        url = MOBIUS + REMOTE + '/' + name + '/la'
        code, response = request('GET', url, reader)
        if code not in (200, 404):
            raise RuntimeError(f'Web read check failed for {name}: HTTP {code}: {response}')
    # A web app must not be able to change a container. The PUT uses its existing limit.
    url = MOBIUS + REMOTE + '/temperature'
    code, response = request('PUT', url, reader, {'m2m:cnt': {'mni': 100}})
    if code != 403:
        raise RuntimeError(f'Read-only check failed: expected 403, got {code}.')
    print('Setup OK: web AE on Mobius; separate read-only ACP; writes denied.')


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        if '-' in value[:10]:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(value.split(',')[0], '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def normalize(name, cin):
    content = cin.get('con')
    def reject_constant(value):
        raise ValueError('JSON must not contain NaN or Infinity')
    payload = json.loads(content, parse_constant=reject_constant) if isinstance(content, str) else content
    if not isinstance(payload, dict):
        raise ValueError('con must contain a JSON object')
    value = payload.get('value')
    valid_value = (isinstance(value, bool) if name == 'presence' else
                   isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value))
    if name in ('smoke_raw', 'co_raw'):
        valid_value = valid_value and isinstance(value, int) and value >= 0
    if name == 'humidity':
        valid_value = valid_value and 0 <= value <= 100
    valid = (valid_value and payload.get('quality') == 'valid'
             and payload.get('device_id') == 'node-01' and payload.get('zone_id') == 'A'
             and payload.get('sensor_type') == name and payload.get('unit') == SENSORS[name][1])
    observed = parse_time(payload.get('observed_at'))
    received = parse_time(cin.get('ct'))
    age = max(0, (datetime.now(timezone.utc) - received).total_seconds()) if received else None
    return {'sensor': name, 'state': 'valid' if valid else 'invalid', 'value': value if valid else None,
            'unit': SENSORS[name][1], 'source': payload.get('source', 'unknown'),
            'observed_at': observed.isoformat() if observed else None,
            'received_at': received.isoformat() if received else None, 'age_seconds': age,
            'stale': age is None or age > 60, 'resource_id': cin.get('ri'), 'payload': payload}


def reading(name):
    try:
        code, data = request('GET', MOBIUS + REMOTE + '/' + name + '/la', '/Mobius4/' + AE_ORIGIN)
        if code == 200:
            return normalize(name, data['m2m:cin'])
        if code == 404:
            return {'sensor': name, 'state': 'empty', 'message': 'No records yet, or resource not created'}
        if code == 403:
            return {'sensor': name, 'state': 'error', 'message': 'Read permission denied. Run --setup first.'}
        return {'sensor': name, 'state': 'error', 'message': f'Platform returned HTTP {code}: {data.get("m2m:dbg", "Request failed")}'}
    except (RuntimeError, ValueError, KeyError, TypeError) as error:
        return {'sensor': name, 'state': 'error', 'message': str(error)}


def snapshot():
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(reading, SENSORS))
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'node': 'node-01', 'zone': 'A',
            'route': 'Mobius IN → ACME MN → node-01', 'readings': values}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/snapshot':
            body = json.dumps(snapshot(), ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
        elif path in ('/', '/index.html', '/app.js', '/style.css', '/english.css'):
            file = ROOT / ('index.html' if path == '/' else path[1:])
            body = file.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if file.suffix == '.html' else
                             'text/javascript; charset=utf-8' if file.suffix == '.js' else 'text/css; charset=utf-8')
        else:
            self.send_error(404)
            return
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', action='store_true', help='Provision web AE and read-only access; then exit')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if args.setup:
        setup()
        return
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'RescueTwin: http://127.0.0.1:{args.port}', flush=True)
    print('Reads only through Mobius. Run with --setup once before first use.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, KeyError) as error:
        raise SystemExit('TWIN ERROR: ' + str(error))
