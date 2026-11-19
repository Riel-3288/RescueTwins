"""Verify standard announcements on a separate ACME-to-ACME lab, not Mobius."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
from urllib.parse import urlencode

from agent_http_adapter import CSEClient

IN_BASE = 'http://127.0.0.1:8010/~/id-annc-lab-in/annc-lab-in'
MN_BASE = 'http://127.0.0.1:8011/~/id-annc-lab-mn/annc-lab-mn'
IN_CSI = '/id-annc-lab-in'
MN_CSI = '/id-annc-lab-mn'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default='docs/acme-announcement-test-report.json')
    args = parser.parse_args()
    source, target = CSEClient(MN_BASE), CSEClient(IN_BASE)
    run_id = uuid.uuid4().hex
    label = 'annc-test-' + run_id[:12]
    report = {'run_id': run_id, 'started_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'isolated ACME MN to ACME IN; not Mobius', 'source': MN_BASE,
              'target': IN_BASE, 'result': 'failed', 'checks': [], 'evidence': {}}

    def passed(name):
        report['checks'].append({'check': name, 'result': 'passed'})
        print('PASS:', name, flush=True)

    def discover(ty, labels=None):
        params = {'fu': 1, 'ty': ty}
        if labels:
            params['lbl'] = labels
        return target.require('GET', '?' + urlencode(params)).get('m2m:uril', [])

    def read_discovered(uri):
        # Discovery may return CSE-relative, SP-relative or absolute identifiers.
        if uri.startswith('//'):
            url = 'http://127.0.0.1:8010/_' + uri
        elif uri.startswith('/'):
            url = 'http://127.0.0.1:8010/~' + uri
        else:
            url = 'http://127.0.0.1:8010/' + uri
        response = target.session.get(url, headers={'X-M2M-Origin': 'CAdmin',
            'X-M2M-RI': uuid.uuid4().hex, 'X-M2M-RVI': '4'}, timeout=10)
        response.raise_for_status()
        return next(value for key, value in response.json().items() if key.startswith('m2m:'))

    def poll(check, description, timeout=25):
        deadline, last = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            result = check()
            if result:
                return result
            time.sleep(.25)
        raise RuntimeError('Timed out: ' + description)

    def announcement(ty, original_ri, expected_labels=None):
        for uri in discover(ty, label):
            value = read_discovered(uri)
            if value.get('lnk') == MN_CSI + '/' + original_ri:
                if expected_labels is None or set(expected_labels).issubset(value.get('lbl', [])):
                    return {'uri': uri, 'resource': value}
        return None

    try:
        def registered():
            for uri in discover(16):
                if read_discovered(uri).get('csi') == MN_CSI:
                    return True
            return False
        poll(registered, 'MN registration')
        passed('Isolated ACME MN registered with isolated ACME IN')
        name = 'annc-map-' + run_id[:12]
        attrs = {'rn': name, 'lbl': [label, 'phase-1'], 'mni': 10,
                 'at': [IN_CSI], 'aa': ['lbl']}
        original = source.require('POST', '', 'CAdmin', {'m2m:cnt': attrs}, 3, (201,))['m2m:cnt']
        found = poll(lambda: announcement(10003, original['ri'], [label, 'phase-1']), 'container announcement')
        report['evidence']['container_before'] = found
        if found['resource']['ty'] != 10003:
            raise RuntimeError('Target is not a containerAnnc resource.')
        passed('Container announced as type 10003 and discovered by label on IN')
        source.require('PUT', '/' + name, 'CAdmin', {'m2m:cnt': {'lbl': [label, 'phase-2']}})
        updated = poll(lambda: announcement(10003, original['ri'], [label, 'phase-2']), 'announced label update')
        if 'phase-1' in updated['resource'].get('lbl', []):
            raise RuntimeError('Old announced label was not removed.')
        report['evidence']['container_after'] = updated
        passed('Updating source labels synchronized the announced attributes on IN')
        for phase in (1, 2):
            payload = {'source': 'simulation', 'run_id': run_id, 'phase': phase,
                       'zone_id': 'lab', 'map_summary': {'hazard_cells': phase}}
            record = source.require('POST', '/' + name, 'CAdmin', {'m2m:cin': {
                'rn': 'summary-' + str(phase), 'con': json.dumps(payload),
                'lbl': [label], 'at': [IN_CSI], 'aa': ['con', 'lbl']}}, 4, (201,))['m2m:cin']
            announced = poll(lambda: announcement(10004, record['ri']), 'contentInstance announcement')
            if announced['resource'].get('ty') != 10004 or json.loads(announced['resource']['con']) != payload:
                raise RuntimeError('Announced content differs from source record.')
            report['evidence']['record_phase_' + str(phase)] = announced
            passed(f'Explicitly announced map-summary record {phase} discovered and read on IN (type 10004)')
        report['result'] = 'passed'
        print('ACME ANNOUNCEMENT TEST PASSED. Mobius announcement remains unsupported.')
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        source.session.close()
        target.session.close()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print('Report:', output.resolve(), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('TEST FAILED: ' + str(error))
