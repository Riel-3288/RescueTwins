"""Repair the stored ACME MN address while the MN is stopped."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default='acme-zone-a/data')
    args = parser.parse_args()
    with socket.socket() as check:
        check.settimeout(2)
        if check.connect_ex(('127.0.0.1', 8001)) == 0:
            raise RuntimeError('MN is still running on port 8001. Stop it with Ctrl+C first.')
    files = list(Path(args.data_dir).resolve().glob('resources-*.json'))
    if len(files) != 1:
        raise RuntimeError('Expected exactly one resources-*.json in the MN data directory.')
    target = files[0]
    data = json.loads(target.read_text(encoding='utf-8-sig'))
    resources = data.get('resources')
    if not isinstance(resources, dict):
        raise RuntimeError('Unexpected database structure; nothing changed.')
    bases = [r for r in resources.values() if r.get('ty') == 5]
    if len(bases) != 1 or bases[0].get('ri') != 'id-mn-zone-a':
        raise RuntimeError('This is not the expected Zone A MN database; nothing changed.')
    base = bases[0]
    old = base.get('poa')
    base['poa'] = ['http://host.docker.internal:8001']
    base['lt'] = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S,%f')
    backup = target.with_name(target.name + '.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
    shutil.copy2(target, backup)
    fd, temporary = tempfile.mkstemp(prefix='poa-fix-', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=True)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Updated MN poa:', old, '->', base['poa'])
    print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('FIX FAILED: ' + str(error))
