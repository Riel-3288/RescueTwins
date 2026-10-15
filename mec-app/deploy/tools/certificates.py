"""Generate a private demo CA and distinct device identities. Never commit the output."""
import argparse
import ipaddress
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def private_key(path, key):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.chmod(0o600)


def generate(output, additional_hosts=()):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "ca.key").exists():
        raise ValueError("CA exists; choose a new output directory to avoid replacing credentials")
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RescueTwin demo CA")])
    ca = (x509.CertificateBuilder().subject_name(issuer).issuer_name(issuer).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True).sign(ca_key, hashes.SHA256()))
    private_key(output / "ca.key", ca_key)
    (output / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    identities = {"broker": ["mqtt", "broker"], "cse-a": ["cse-a"], "cse-b": ["cse-b"], "cse-in": ["cse-in"],
                  "bridge": [], "node-01": [], "node-02": [], "node-03": []}
    for name, hosts in identities.items():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=30))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True))
        builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        builder = builder.add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
        if hosts:
            sans = [x509.DNSName(h) for h in [*hosts, "localhost", *additional_hosts]]
            sans.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
            builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if hosts else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        cert = builder.sign(ca_key, hashes.SHA256())
        private_key(output / (name + ".key"), key)
        (output / (name + ".crt")).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    for zone in ("a", "b", "in"):
        path = output / (zone + ".token")
        path.write_text(secrets.token_urlsafe(32))
        path.chmod(0o600)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="certs")
    parser.add_argument("--hostname", action="append", default=[])
    args = parser.parse_args()
    generate(args.output, args.hostname)
    print("Generated demo credentials. Deliver each node only its own key/cert and ca.crt.")
