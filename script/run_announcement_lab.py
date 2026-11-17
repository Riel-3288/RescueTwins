"""Run an isolated ACME IN/MN pair; --check tests it and shuts down the owned pair."""
import argparse
import configparser
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import requests

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / 'acme-announcement-lab'
TEMPLATES = PROJECT / 'cse-config' / 'announcement-lab'


def config(role):
    folder = RUNTIME / role
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'acme.ini'
    if not target.exists():
        text = (TEMPLATES / (role + '.ini.example')).read_text(encoding='utf-8')
        target.write_text(text.replace('CHANGE_ME_LOCAL_SECRET', secrets.token_hex(24)), encoding='utf-8')
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(target, encoding='utf-8')
    expected_id = 'id-annc-lab-' + role
    expected_port = '8010' if role == 'in' else '8011'
    if (parser.get('basic.config', 'cseID') != expected_id
            or parser.get('basic.config', 'httpPort') != expected_port
            or parser.get('basic.config', 'networkInterface') != '127.0.0.1'):
        raise RuntimeError('Existing lab config differs; not overwritten: ' + str(target))
    return folder


def wait_ready(session, url, child):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('ACME exited during startup; inspect the lab log files.')
        try:
            response = session.get(url, headers={'X-M2M-Origin': 'CAdmin',
                                   'X-M2M-RI': secrets.token_hex(8), 'X-M2M-RVI': '4'}, timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(.3)
    raise RuntimeError('ACME startup timed out; inspect the lab log files.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Run the test, then stop both owned CSEs')
    args = parser.parse_args()
    for port in (8010, 8011):
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'Port {port} is already occupied. No process was stopped.')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    # Ignore this entire runtime directory, including generated secrets and TinyDB files.
    ignore = RUNTIME / '.gitignore'
    if not ignore.exists():
        ignore.write_text('*\n', encoding='utf-8')
    children, logs = [], []
    session = requests.Session()
    session.trust_env = False
    try:
        for role, port in (('in', 8010), ('mn', 8011)):
            folder = config(role)
            log = (folder / 'runtime.log').open('a', encoding='utf-8')
            logs.append(log)
            child = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'acmecse',
                '--base-directory', str(folder)], cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            children.append((role, port, child))
            wait_ready(session, f'http://127.0.0.1:{port}/~/id-annc-lab-{role}/annc-lab-{role}', child)
            print(f'ACME {role.upper()} ready: http://127.0.0.1:{port}/webui', flush=True)
        if args.check:
            return subprocess.call([sys.executable, str(PROJECT / 'script' / 'acme_announcement_smoke_test.py')], cwd=PROJECT)
        print('Keep this terminal open. Ctrl+C stops only this lab pair.', flush=True)
        while all(child.poll() is None for _, _, child in children):
            time.sleep(1)
        raise RuntimeError('A lab process exited; inspect its runtime.log.')
    except KeyboardInterrupt:
        return 0
    finally:
        # Shut down MN first so it can deregister while IN remains reachable.
        for role, port, child in reversed(children):
            if child.poll() is None:
                try:
                    session.get(f'http://127.0.0.1:{port}/__mgmt__/shutdown', timeout=3)
                except requests.RequestException:
                    pass
                try:
                    child.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait(timeout=5)
        session.close()
        for log in logs:
            log.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, configparser.Error) as error:
        sys.exit('LAB ERROR: ' + str(error))
