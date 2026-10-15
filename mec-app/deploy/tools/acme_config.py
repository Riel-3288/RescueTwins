"""Render an isolated ACME configuration; never edit teammate-owned cse-config."""
import argparse
import os
import secrets
import hmac
import hashlib
from pathlib import Path


def render(directory, kind, cse_id, name, port, host="127.0.0.1", tls=False,
           registrar_host="127.0.0.1", registrar_port=18000, broker_host=None, broker_port=18883,
           certificate_dir="/certs", token=None, remote=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    secret_file = directory / "secret.txt"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32), encoding="utf-8")
    secret = secret_file.read_text()
    text = f"""[basic.config]
cseType={kind}
cseID={cse_id}
cseName={name}
adminID=CAdmin
cseHost={host}
httpPort={port}
networkInterface=0.0.0.0
databaseType=tinydb
logLevel=warning
secret={secret}
"""
    if kind != "IN":
        text += f"registrarCseID=id-in\nregistrarCseName=cse-in\nregistrarCseHost={registrar_host}\nregistrarCsePort={registrar_port}\n"
    text += f"""

[cse]
enableRemoteCSE={'true' if remote else 'false'}

[cse.registration]
allowedCSROriginators=/id-mn-a,/id-mn-b,/id-in
checkInterval=5

[textui]
startWithTUI=false

[http]
address={'https' if tls else 'http'}://{host}:{port}
enableManagementEndpoint=false
enableStructureEndpoint=false
enableUpperTesterEndpoint=false

[http.security]
useTLS={'true' if tls else 'false'}
; ACME uses this flag to require incoming client certificates. HTTPS clients
; still verify the server with ca.crt; HTTP access here uses bearer token auth.
verifyCertificate={'false' if tls else 'true'}
"""
    if tls:
        if not token:
            raise ValueError("TLS deployment requires CSE_SERVER_TOKEN")
        certificate_dir = Path(certificate_dir)
        text += f"caCertificateFile={certificate_dir / 'tls.crt'}\ncaPrivateKeyFile={certificate_dir / 'tls.key'}\n"
        auth_file = directory / "http_token_auth.txt"
        auth_file.write_text(hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest() + "\n")
        text += f"enableTokenAuth=true\ntokenAuthFile={auth_file}\n"
    if broker_host:
        text += f"\n[mqtt]\nenable=true\naddress={broker_host}\nport={broker_port}\n"
    (directory / "acme.ini").write_text(text, encoding="utf-8")
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default=os.getenv("ACME_DIRECTORY", "/data"))
    parser.add_argument("--type", default=os.getenv("CSE_TYPE", "MN"))
    parser.add_argument("--id", default=os.getenv("CSE_ID", "id-mn-a"))
    parser.add_argument("--name", default=os.getenv("CSE_NAME", "cse-mn-a"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CSE_PORT", "18001")))
    args = parser.parse_args()
    render(args.directory, args.type, args.id, args.name, args.port,
           host=os.getenv("CSE_HOST", "127.0.0.1"), tls=os.getenv("CSE_TLS", "false") == "true",
           token=os.getenv("CSE_SERVER_TOKEN"), remote=os.getenv("CSE_REMOTE", "false") == "true",
           registrar_host=os.getenv("REGISTRAR_HOST", "127.0.0.1"), registrar_port=int(os.getenv("REGISTRAR_PORT", "18000")))
    os.execv(__import__("sys").executable, [__import__("sys").executable, "-X", "utf8", "-m", "acmecse", "--base-directory", args.directory, "--headless"])
