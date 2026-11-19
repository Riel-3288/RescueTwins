"""Provision isolated test agents and verify ACPs, groups and notifications."""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import threading
import time
import uuid

from agent_http_adapter import AgentHTTPAdapter, CSEClient

LABEL = 'RescueTwin-platform-core-test-v1'
EDGE = ('platform-test-edge', 'CPlatformTestEdge')
VIEWER = ('platform-test-viewer', 'CPlatformTestViewer')
AGENTS = [('platform-test-agent-01', 'CPlatformTestAgent01'),
          ('platform-test-agent-02', 'CPlatformTestAgent02')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8001/~/id-mn-zone-a/mn-zone-a')
    parser.add_argument('--report', default='docs/platform-core-test-report.json')
    args = parser.parse_args()
    api = CSEClient(args.base_url)
    run_id = uuid.uuid4().hex
    outcomes, subs, adapters = [], [], []
    notifications = queue.Queue()
    verification = threading.Event()
    report = {'run_id': run_id, 'started_at': datetime.now(timezone.utc).isoformat(),
              'base_url': args.base_url, 'scope': 'isolated platform test resources; no real robot actions',
              'checks': outcomes, 'result': 'failed'}

    def passed(name):
        outcomes.append({'check': name, 'result': 'passed'})
        print('PASS:', name, flush=True)

    def resource(data):
        return next(value for key, value in data.items() if key.startswith('m2m:') and isinstance(value, dict))

    def ensure(parent, rn, ty, key, attrs, creator='CAdmin'):
        path = parent + '/' + rn
        response, data = api.call('GET', path)
        if response.status_code == 404:
            value = dict(attrs, rn=rn, lbl=[LABEL])
            return path, resource(api.require('POST', parent, creator, {key: value}, ty, (201,)))
        if response.status_code != 200:
            raise RuntimeError(f'Cannot inspect {path}: HTTP {response.status_code}: {data}')
        value = resource(data)
        if value.get('ty') != ty or LABEL not in value.get('lbl', []):
            raise RuntimeError(f'Unexpected existing resource {path}; not overwritten.')
        return path, value

    def policy(rn, entries):
        pv = {'acr': [{'acor': [origin], 'acop': permission} for origin, permission in entries]
                     + [{'acor': ['CAdmin'], 'acop': 63}]}
        pvs = {'acr': [{'acor': ['CAdmin'], 'acop': 63}]}
        _, value = ensure('/' + EDGE[0], rn, 1, 'm2m:acp', {'pv': pv, 'pvs': pvs})
        if value.get('pv') != pv or value.get('pvs') != pvs:
            raise RuntimeError('Existing test ACP differs: ' + rn)
        return value['ri']

    def container(parent, rn, policy_id):
        path, value = ensure(parent, rn, 3, 'm2m:cnt', {'mni': 20, 'acpi': [policy_id]})
        refs = value.get('acpi', [])
        if not any(ref == policy_id or ref.endswith('/' + policy_id) for ref in refs):
            raise RuntimeError('Existing test container has different access control: ' + path)
        return path, value

    def deny(method, path, origin, body=None, ty=None):
        response, data = api.call(method, path, origin, body, ty)
        if response.status_code != 403:
            raise RuntimeError(f'Expected denial: {method} {path}, got HTTP {response.status_code}: {data}')

    class Callback(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length < 100000:
                    self.send_error(400)
                    return
                data = json.loads(self.rfile.read(length))['m2m:sgn']
                if data.get('vrq'):
                    verification.set()
                elif data.get('nev', {}).get('net') == 3:
                    notifications.put(data)
                self.send_response(200)
                self.send_header('X-M2M-RSC', '2000')
                self.send_header('X-M2M-RI', self.headers.get('X-M2M-RI', ''))
                self.send_header('Content-Length', '0')
                self.end_headers()
            except (ValueError, KeyError):
                self.send_error(400)

        def log_message(self, *unused):
            pass

    callback = ThreadingHTTPServer(('127.0.0.1', 0), Callback)
    worker = threading.Thread(target=callback.serve_forever, daemon=True)
    worker.start()
    try:
        base = api.require('GET')['m2m:cb']
        csi = base['csi']
        for name, origin in [EDGE, VIEWER] + AGENTS:
            _, ae = ensure('', name, 2, 'm2m:ae', {'api': 'NRescueTwin.platformTest',
                           'rr': False, 'srv': ['4']}, origin)
            if ae.get('aei') != origin:
                raise RuntimeError('Unexpected test AE identity: ' + name)
        passed('Four isolated test AEs registered or reused')

        map_acp = policy('map-policy', [(EDGE[1], 3), (VIEWER[1], 2)] + [(o, 2) for _, o in AGENTS])
        group_acp = policy('group-policy', [(EDGE[1], 3)])
        map_path, _ = container('/' + EDGE[0], 'map', map_acp)
        command_paths, command_ids = [], []
        for name, origin in AGENTS:
            own = policy(name + '-own-policy', [(origin, 3), (EDGE[1], 2), (VIEWER[1], 2)])
            command = policy(name + '-command-policy', [(origin, 2), (EDGE[1], 3), (VIEWER[1], 2)])
            container('/' + name, 'observations', own)
            container('/' + name, 'bids', own)
            path, cnt = container('/' + name, 'commands', command)
            command_paths.append(path)
            command_ids.append(cnt['ri'])
            adapters.append(AgentHTTPAdapter(args.base_url, name, origin, map_path))
        passed('Separate observation, bid, command and shared-map policies provisioned')

        map_payload = {'run_id': run_id, 'schema_version': 'test-1', 'cells': [[0, 0, 0]], 'source': 'simulation'}
        api.require('POST', map_path, EDGE[1], {'m2m:cin': {'con': json.dumps(map_payload)}}, 4, (201,))
        for index, adapter in enumerate(adapters):
            adapter.post_observation({'run_id': run_id, 'agent_id': AGENTS[index][0],
                                      'cell': [index, 0], 'source': 'simulation'})
            adapter.post_bid({'run_id': run_id, 'task_id': 'test-task', 'cost': index + 1})
            if adapter.latest_map() != map_payload:
                raise RuntimeError('Agent could not read the shared map.')
        api.require('GET', map_path + '/la', VIEWER[1])
        passed('Agents upload observations/bids; agents and viewer read the shared map')

        sample = {'m2m:cin': {'con': json.dumps({'run_id': run_id, 'unauthorized_test': True})}}
        deny('POST', map_path, AGENTS[0][1], sample, 4)
        deny('POST', map_path, VIEWER[1], sample, 4)
        deny('POST', '/' + AGENTS[1][0] + '/observations', AGENTS[0][1], sample, 4)
        deny('POST', command_paths[0], AGENTS[0][1], sample, 4)
        deny('PUT', map_path, VIEWER[1], {'m2m:cnt': {'mni': 20}})
        deny('GET', map_path + '/la', 'CPlatformTestUnknown')
        passed('Six negative permission checks rejected with HTTP 403')

        group_path, group = ensure('/' + EDGE[0], 'team', 9, 'm2m:grp', {
            'mt': 3, 'mnm': 2, 'mid': [csi + '/' + ri for ri in command_ids], 'acpi': [group_acp]})
        actual_members = {member.rsplit('/', 1)[-1] for member in group.get('mid', [])}
        if actual_members != set(command_ids) or group.get('cnm') != 2:
            raise RuntimeError('Existing group member list differs; not overwritten.')
        passed('Command-container group contains both test agents')

        expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime('%Y%m%dT%H%M%S')
        for path in command_paths:
            rn = 'notify-' + run_id[:12]
            sub_path = path + '/' + rn
            # Remember the uniquely named resource before POST in case the response is interrupted.
            subs.append(sub_path)
            api.require('POST', path, 'CAdmin', {'m2m:sub': {'rn': rn,
                'lbl': [LABEL], 'et': expires, 'nu': [f'http://127.0.0.1:{callback.server_port}/notify'],
                'enc': {'net': [3]}, 'nct': 1}}, 23, (201,))
        if not verification.wait(3):
            raise RuntimeError('Subscription notification URI was not verified.')
        passed('Two temporary subscriptions created and callback verification received')

        for action in ('recall', 'regroup'):
            command = {'run_id': run_id, 'command_id': uuid.uuid4().hex,
                       'command': action, 'source': 'platform-test'}
            aggregate = api.require('POST', group_path + '/fopt', EDGE[1],
                {'m2m:cin': {'con': json.dumps(command)}}, 4, (200, 201))
            results = aggregate.get('m2m:agr', {}).get('m2m:rsp', [])
            if len(results) != 2 or any(item.get('rsc') != 2001 for item in results):
                raise RuntimeError('Not all fan-out members succeeded: ' + json.dumps(aggregate))
            for adapter in adapters:
                if adapter.latest_command() != command:
                    raise RuntimeError('A group member did not receive the exact command.')
            seen = set()
            deadline = time.monotonic() + 8
            while len(seen) < 2 and time.monotonic() < deadline:
                try:
                    notification = notifications.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                try:
                    cin = notification['nev']['rep']['m2m:cin']
                    if json.loads(cin['con']) == command:
                        seen.add(notification['sur'])
                except (KeyError, ValueError, TypeError):
                    pass
            if len(seen) != 2:
                raise RuntimeError('Did not receive both command notifications for ' + action)
            passed(f'One {action} fan-out reached both agents and produced both subscription notifications')
        deny('POST', group_path + '/fopt', VIEWER[1], sample, 4)
        passed('Viewer cannot issue group commands')
        report['result'] = 'passed'
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        cleanup_errors = []
        for path in subs:
            try:
                response, data = api.call('DELETE', path)
                if response.status_code not in (200, 404):
                    cleanup_errors.append(path + ': HTTP ' + str(response.status_code))
            except Exception as error:
                cleanup_errors.append(str(error))
        callback.shutdown()
        callback.server_close()
        for adapter in adapters:
            adapter.close()
        api.session.close()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['temporary_subscription_cleanup_errors'] = cleanup_errors
        if cleanup_errors:
            report['result'] = 'failed'
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print('Report:', output.resolve(), flush=True)
        if cleanup_errors:
            raise RuntimeError('Temporary subscription cleanup failed; see report. They expire within 15 minutes.')
    print('PLATFORM CORE TEST PASSED. Real simulator integration and production roles remain separate tasks.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('TEST FAILED: ' + str(error))
