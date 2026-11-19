"""Read-only checks for team endpoints; no registration, upload or scenario edits."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from .mec import Mec
from .onem2m import Cse


def check_team(config):
    def check(item):
        start = time.perf_counter()
        name, spec = item
        client = Cse(config['cse'][spec['endpoint']], spec['originator'])
        try:
            data = client.call('GET', spec['path'])
            cin = data.get('m2m:cin')
            return {'name': name, 'ok': True, 'resource': cin.get('rn') if cin else None,
                    'contains_con': bool(cin and 'con' in cin),
                    'elapsed_ms': round((time.perf_counter()-start)*1000)}
        except Exception as exc:
            return {'name': name, 'ok': False, 'error': type(exc).__name__, 'detail': str(exc)}
        finally:
            client.session.close()
    checks = list(config.get('read_checks', {}).items())
    results = list(ThreadPoolExecutor(max_workers=4).map(check, checks)) if checks else []
    mec = Mec(config['mec'], True)
    try:
        data = mec.locations()
        users = data.get('userList', {}).get('user', [])
        results.append({'name': 'MEC013 Location', 'ok': True,
                        'addresses': [u['address'] for u in users],
                        'agent_mapping_configured': bool(config.get('agents'))})
    except Exception as exc:
        results.append({'name': 'MEC013 Location', 'ok': False, 'error': type(exc).__name__, 'detail': str(exc)})
    finally:
        mec.session.close()
    return {'mode': 'read_only', 'checks': results, 'complete': bool(checks) and all(r['ok'] for r in results),
            'note': 'No AE registration or writes performed. Reachability does not grant independent application permissions.'}
