"""Probe announced-container support without modifying existing resources."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:7599/Mobius')
    parser.add_argument('--origin', default='/id-mn-zone-a')
    parser.add_argument('--report', default='docs/announcement-support-report.json')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    session = requests.Session()
    session.trust_env = False
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'target': base,
              'result': 'not_verified', 'scope': 'compatibility probe, not a full announcement test'}
    probe_name = 'platform-annc-probe-' + uuid.uuid4().hex[:12]
    created = False

    def call(method, url, body=None, ty=None):
        headers = {'X-M2M-Origin': args.origin, 'X-M2M-RI': uuid.uuid4().hex,
                   'X-M2M-RVI': '4', 'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json' + (f';ty={ty}' if ty else '')
        response = session.request(method, url, headers=headers, json=body, timeout=10)
        try:
            content = response.json()
        except ValueError:
            content = {'response': response.text[:500]}
        return response, content

    try:
        response, content = call('GET', base)
        if response.status_code != 200 or 'm2m:cb' not in content:
            raise RuntimeError(f'Cannot inspect CSEBase: HTTP {response.status_code}: {content}')
        cse = content['m2m:cb']
        report['cse_id'] = cse.get('csi')
        report['supported_resource_types'] = cse.get('srt', [])
        report['container_announced_type'] = 10003
        print('CSE:', report['cse_id'])
        print('Supported types:', report['supported_resource_types'])
        response, content = call('POST', base, {'m2m:cntA': {
            'rn': probe_name, 'lnk': '/id-mn-zone-a/mn-zone-a/' + probe_name,
            'lbl': ['RescueTwin-announcement-compatibility-probe']}}, 10003)
        created = response.status_code == 201
        report['create_probe'] = {'http_status': response.status_code,
                                  'rsc': response.headers.get('X-M2M-RSC'), 'response': content}
        if created:
            report['result'] = 'create_supported_full_announcement_not_tested'
        elif 10003 not in report['supported_resource_types'] and response.status_code in (400, 405, 501):
            report['result'] = 'blocked_unsupported'
        else:
            report['result'] = 'probe_rejected_needs_investigation'
        print('Create containerAnnc:', response.status_code, content)
        print('Result:', report['result'])
    except Exception as error:
        report['error'] = str(error)
        report['result'] = 'error'
        raise
    finally:
        if created:
            response, _ = call('DELETE', base + '/' + probe_name)
            report['probe_cleanup_http_status'] = response.status_code
            if response.status_code not in (200, 404):
                report['result'] = 'cleanup_failed'
        session.close()
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print('Report:', output.resolve())
    return 2 if report['result'] == 'blocked_unsupported' else 0 if created else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, requests.RequestException) as error:
        sys.exit('CHECK FAILED: ' + str(error))
