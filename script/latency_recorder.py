"""Measure Zone A ACME notifications and retrieval through Mobius on one clock."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from statistics import mean
import sys
import threading
import time
import uuid

import requests

from agent_http_adapter import CSEClient, OneM2MError


ROOT = Path(__file__).resolve().parents[1]
CSE_PATH = '/~/id-mn-zone-a/mn-zone-a'
SENSORS = ('temperature', 'humidity', 'pressure', 'smoke_raw', 'co_raw', 'presence')
FIELDS = ('seq', 'sensor_type', 'ts_ms', 'recv_ms', 'path', 'latency_ms', 'cin_ri')


def epoch_ms():
    return time.time_ns() // 1_000_000


def statistics(values):
    return {'count': len(values), 'mean_ms': round(mean(values), 3) if values else None,
            'max_ms': max(values) if values else None}


class Recorder:
    def __init__(self, output, target, preflight):
        self.output = output
        self.target = target
        self.preflight = preflight
        self.active = False
        self.lock = threading.Lock()
        self.complete = threading.Event()
        self.valid_pairs = 0
        self.rows = {'edge': {}, 'cloud': {}}
        self.clock_issues = {'edge': 0, 'cloud': 0}
        self.skipped = {'edge': 0, 'cloud': 0}
        self.file = (output / 'samples.csv').open('w', encoding='utf-8', newline='')
        self.csv = csv.DictWriter(self.file, fieldnames=FIELDS)
        self.csv.writeheader()
        self.file.flush()

    def record(self, path, cin, recv_ms):
        with self.lock:
            if not self.active or cin.get('ri') in self.rows[path]:
                return
            try:
                payload = json.loads(cin['con']) if isinstance(cin['con'], str) else cin['con']
                seq, sent, sensor = payload['seq'], payload['ts_ms'], payload['sensor_type']
                expected_source = 'simulation' if self.preflight else 'physical'
                if (payload.get('device_id') != 'node-01' or payload.get('zone_id') != 'A'
                        or payload.get('source') != expected_source or sensor not in SENSORS
                        or type(seq) is not int or seq < 1 or type(sent) is not int or sent <= 0):
                    raise ValueError('Invalid measurement fields')
                row = {'seq': seq, 'sensor_type': sensor, 'ts_ms': sent, 'recv_ms': recv_ms,
                       'path': path, 'latency_ms': recv_ms - sent, 'cin_ri': cin['ri']}
            except (ValueError, KeyError, TypeError, AttributeError):
                self.skipped[path] += 1
                return
            self.rows[path][row['cin_ri']] = row
            self.csv.writerow(row)
            self.file.flush()
            if not self.clock_valid(row):
                self.clock_issues[path] += 1
            edge = self.rows['edge'].get(row['cin_ri'])
            cloud = self.rows['cloud'].get(row['cin_ri'])
            if edge and cloud and self.clock_valid(edge) and self.clock_valid(cloud):
                self.valid_pairs += 1
                if self.valid_pairs >= self.target:
                    self.complete.set()

    @staticmethod
    def clock_valid(row):
        # Flag negative and clearly implausible offsets; retain raw observations.
        return 0 <= row['latency_ms'] <= 300_000

    def finish(self, poll_interval, poll_errors):
        with self.lock:
            self.active = False
            self.file.close()
            pairs = []
            for cin_ri, edge in self.rows['edge'].items():
                cloud = self.rows['cloud'].get(cin_ri)
                if cloud:
                    pairs.append({'cin_ri': cin_ri, 'seq': edge['seq'], 'sensor_type': edge['sensor_type'],
                                  'ts_ms': edge['ts_ms'], 'edge_recv_ms': edge['recv_ms'],
                                  'cloud_recv_ms': cloud['recv_ms'],
                                  'cloud_minus_edge_ms': cloud['recv_ms'] - edge['recv_ms'],
                                  'clock_valid_both': self.clock_valid(edge) and self.clock_valid(cloud)})
            with (self.output / 'pairs.csv').open('w', encoding='utf-8', newline='') as handle:
                fields = ('cin_ri', 'seq', 'sensor_type', 'ts_ms', 'edge_recv_ms', 'cloud_recv_ms',
                          'cloud_minus_edge_ms', 'clock_valid_both')
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(pairs)
            report = {'mode': 'synthetic_preflight' if self.preflight else 'hardware',
                      'edge_method': 'MN-CSE subscription callback',
                      'cloud_method': 'IN-CSE forwarded latest-CIN polling',
                      'cloud_poll_interval_seconds': poll_interval,
                      'cloud_poll_errors': poll_errors, 'target_valid_pairs': self.target,
                      'valid_pairs': self.valid_pairs, 'matched_pairs': len(pairs),
                      'invalid_clock_samples': self.clock_issues, 'skipped_payloads': self.skipped,
                      'unmatched_received': {path: len(rows) - len(pairs) for path, rows in self.rows.items()},
                      'note': 'Cloud includes polling wait. Unmatched receipts alone do not establish data loss.'}
            for path, rows in self.rows.items():
                report[path] = statistics([row['latency_ms'] for row in rows.values() if self.clock_valid(row)])
            report['cloud_minus_edge'] = statistics([pair['cloud_minus_edge_ms'] for pair in pairs
                                                      if pair['clock_valid_both']])
            (self.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            return report


def callback_server(recorder, route):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != route:
                self.send_error(404)
                return
            self.connection.settimeout(5)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 100_000:
                    raise ValueError('Invalid body length')
                body = self.rfile.read(length)
                recv_ms = epoch_ms()
                signal = json.loads(body)['m2m:sgn']
                if signal.get('nev', {}).get('net') == 3:
                    recorder.record('edge', signal['nev']['rep']['m2m:cin'], recv_ms)
            except (ValueError, KeyError, TypeError, AttributeError, OSError):
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header('X-M2M-RSC', '2000')
            self.send_header('X-M2M-RI', self.headers.get('X-M2M-RI', ''))
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *unused):
            pass

    return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


def send_preflight():
    from paho.mqtt.publish import single

    credentials = json.loads((ROOT / '.mqtt-local/node-01-credentials.json').read_text(encoding='utf-8'))
    payload = {'device_id': 'node-01', 'zone_id': 'A', 'sensor_type': 'temperature', 'value': 22.9,
               'unit': 'degC', 'source': 'simulation', 'quality': 'valid', 'seq': 1, 'ts_ms': epoch_ms()}
    request = {'op': 1, 'to': '/id-mn-zone-a/mn-zone-a/node-01/_connection_test', 'fr': 'CNode01',
               'rqi': uuid.uuid4().hex, 'rvi': '4', 'ty': 4, 'pc': {'m2m:cin': {'con': json.dumps(payload)}}}
    single('/oneM2M/req/CNode01/id-mn-zone-a/json', json.dumps(request), qos=1, hostname='127.0.0.1',
           auth={'username': credentials['username'], 'password': credentials['password']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true', help='Send one synthetic MQTT record to _connection_test')
    parser.add_argument('--pairs', type=int, default=100)
    parser.add_argument('--duration', type=float, default=180, help='Maximum recording time in seconds')
    parser.add_argument('--poll-interval', type=float, default=.5, help='Target time between cloud polling sweeps')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if min(args.duration, args.poll_interval, args.pairs) <= 0:
        parser.error('duration, poll interval and pairs must be positive')
    run_id = uuid.uuid4().hex[:10]
    output = args.output or ROOT / 'acme-zone-a' / 'latency' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + run_id)
    output.mkdir(parents=True, exist_ok=False)
    recorder = Recorder(output, 1 if args.preflight else args.pairs, args.preflight)
    edge = CSEClient('http://127.0.0.1:8001' + CSE_PATH)
    cloud = CSEClient('http://127.0.0.1:7599' + CSE_PATH)
    route = '/edge/' + run_id
    server = callback_server(recorder, route)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    names = ('_connection_test',) if args.preflight else SENSORS
    containers = ['/node-01/' + name for name in names]
    subscriptions, baseline = [], {}
    poll_errors = 0

    def read_cloud(container):
        response = cloud.session.get(cloud.base_url + container + '/la', headers={
            'X-M2M-Origin': '/id-mn-zone-a/CNode01', 'X-M2M-RI': uuid.uuid4().hex,
            'X-M2M-RVI': '4', 'Accept': 'application/json'}, timeout=3)
        recv_ms = epoch_ms()
        if response.status_code == 404 and response.headers.get('X-M2M-RSC') == '4004':
            return None, recv_ms
        body = response.json()
        if response.status_code != 200 or 'm2m:cin' not in body:
            raise OneM2MError(f'Cloud read {container}: HTTP {response.status_code}, {body}')
        return body['m2m:cin'], recv_ms

    try:
        for container in containers:
            edge.require('GET', container)
            cin, _ = read_cloud(container)
            baseline[container] = cin['ri'] if cin else None
        expires = (datetime.now(timezone.utc) + timedelta(seconds=args.duration + 600)).strftime('%Y%m%dT%H%M%S')
        for container in containers:
            name = 'rt-latency-' + run_id
            edge.require('POST', container, body={'m2m:sub': {
                'rn': name, 'et': expires, 'nu': [f'http://127.0.0.1:{server.server_port}{route}'],
                'enc': {'net': [3]}, 'nct': 1}}, ty=23, statuses=(201,))
            subscriptions.append(container + '/' + name)
        recorder.active = True
        print('READY: edge callback and cloud polling. Output:', output, flush=True)
        if args.preflight:
            send_preflight()
        duration = min(args.duration, 20) if args.preflight else args.duration
        deadline = time.monotonic() + duration
        while not recorder.complete.is_set() and time.monotonic() < deadline:
            sweep = time.monotonic()
            for container in containers:
                try:
                    cin, received = read_cloud(container)
                    if cin and cin['ri'] != baseline[container]:
                        baseline[container] = cin['ri']
                        recorder.record('cloud', cin, received)
                except (OneM2MError, requests.RequestException) as error:
                    poll_errors += 1
                    if poll_errors == 1:
                        print('Cloud polling error:', error, file=sys.stderr, flush=True)
            recorder.complete.wait(max(0, args.poll_interval - (time.monotonic() - sweep)))
    except KeyboardInterrupt:
        print('Recording stopped; saving partial results.', flush=True)
    finally:
        report = recorder.finish(args.poll_interval, poll_errors)
        for path in subscriptions:
            try:
                edge.require('DELETE', path, statuses=(200, 204, 404))
            except (OneM2MError, requests.RequestException) as error:
                print('Temporary subscription cleanup:', path, error, file=sys.stderr)
        server.shutdown()
        server.server_close()
        edge.session.close()
        cloud.session.close()
        print(json.dumps(report, indent=2), flush=True)
    return 0 if report['valid_pairs'] >= recorder.target else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, OneM2MError, requests.RequestException) as error:
        sys.exit('LATENCY ERROR: ' + str(error))
