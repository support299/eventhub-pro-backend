"""Verify GoHighLevel webhook signatures (Ed25519 and legacy RSA)."""
from __future__ import annotations

import base64

GHL_ED25519_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAi2HR1srL4o18O8BRa7gVJY7G7bupbN3H9AwJrHCDiOg=
-----END PUBLIC KEY-----"""

GHL_RSA_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAokvo/r9tVgcfZ5DysOSC
Frm602qYV0MaAiNnX9O8KxMbiyRKWeL9JpCpVpt4XHIcBOK4u3cLSqJGOLaPuXw6
dO0t6Q/ZVdAV5Phz+ZtzPL16iCGeK9po6D6JHBpbi989mmzMryUnQJezlYJ3DVfB
csedpinheNnyYeFXolrJvcsjDtfAeRx5ByHQmTnSdFUzuAnC9/GepgLT9SM4nCpv
uxmZMxrJt5Rw+VUaQ9B8JSvbMPpez4peKaJPZHBbU3OdeCVx5klVXXZQGNHOs8gF
3kvoV5rTnXV0IknLBXlcKKAQLZcY/Q9rG6Ifi9c+5vqlvHPCUJFT5XUGG5RKgOK
UJ062fRtN+rLYZUV+BjafxQauvC8wSWeYja63VSUruvmNj8xkx2zE/Juc+yjLjTX
pIocmaiFeAO6fUtNjDeFVkhf5LNb59vECyrHD2SQIrhgXpO4Q3dVNA5rw576PwTz
Nh/AMfHKIjE4xQA1SZuYJmNnmVZLIZBlQAF9Ntd03rfadZ+yDiOXCCs9FkHibELh
CHULgCsnuDJHcrGNd5/Ddm5hxGQ0ASitgHeMZ0kcIOwKDOzOU53lDza6/Y09T7s
YJPQe7z0cvj7aE4B+Ax1ZoZGPzpJlZtGXCsu9aTEGEnKzmsFqwcSsnw3JB31IGKA
ykT1hhTiaCeIY/OwwwNUY2yvcCAwEAAQ==
-----END PUBLIC KEY-----"""


def verify_ghl_webhook(payload: bytes, ghl_sig: str | None, legacy_sig: str | None) -> bool:
    if ghl_sig and ghl_sig not in ("", "N/A"):
        return _verify_ed25519(payload, ghl_sig)
    if legacy_sig and legacy_sig not in ("", "N/A"):
        return _verify_rsa(payload, legacy_sig)
    return False


def _verify_ed25519(payload: bytes, signature_b64: str) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        key = load_pem_public_key(GHL_ED25519_PUBLIC_KEY_PEM.encode())
        if not isinstance(key, Ed25519PublicKey):
            return False
        key.verify(base64.b64decode(signature_b64), payload)
        return True
    except Exception:
        return False


def _verify_rsa(payload: bytes, signature_b64: str) -> bool:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        key = load_pem_public_key(GHL_RSA_PUBLIC_KEY_PEM.encode())
        key.verify(
            base64.b64decode(signature_b64),
            payload,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False
