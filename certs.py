"""A self-signed HTTPS certificate for using the journal from your phone on home Wi-Fi.

Phones only let a web page use the microphone over https, and https also keeps
your password and entries from being readable on the network. The certificate
is made once and kept in data/ (never committed). Because it's self-signed,
your phone shows a warning the first time; that's expected, and you accept it
once.
"""

import datetime
import ipaddress
import os
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def lan_ip() -> str:
    """This computer's address on the home network (no traffic is actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def local_name() -> str:
    name = socket.gethostname().split(".")[0]
    return f"{name}.local"


def ensure_cert(folder: str) -> tuple[str, str]:
    cert_path = os.path.join(folder, "phone-cert.pem")
    key_path = os.path.join(folder, "phone-key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path
    os.makedirs(folder, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Between sessions journal")])
    alt = [x509.DNSName(local_name()), x509.DNSName("localhost"),
           x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    ip = lan_ip()
    if ip != "127.0.0.1":
        alt.append(x509.IPAddress(ipaddress.ip_address(ip)))
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    os.chmod(key_path, 0o600)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
