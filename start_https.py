import datetime
import ipaddress
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from main import get_lan_ip

CERT_FILE = Path(__file__).parent / "cert.pem"
KEY_FILE = Path(__file__).parent / "key.pem"


def build_cert_for_current_ip(ip_str: str) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sans = [x509.DNSName("localhost")]
    try:
        sans.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
    except ValueError:
        pass

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WebersTankTesting")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .sign(key, hashes.SHA256())
    )
    KEY_FILE.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


if __name__ == "__main__":
    current_ip = get_lan_ip()
    build_cert_for_current_ip(current_ip)
    print(f"\nLaptop & Phone Live Camera URL: https://{current_ip}:8443\n")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8443,
        ssl_keyfile=str(KEY_FILE),
        ssl_certfile=str(CERT_FILE),
    )