import base64
import uuid
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .base import GSTLookupConfigurationError


SUPPORTED_ALGORITHMS = {"SHA1": hashes.SHA1, "SHA256": hashes.SHA256}


def vayana_timestamp(now=None):
    current = (now or datetime.now().astimezone()).astimezone()
    return current.strftime("%Y%m%d%H%M%S%z")


def build_auth_token(*, cust_id, client_id, txn_id, timestamp, gstin, api_action="TP"):
    return f"v2.0:{cust_id}:{client_id}:{txn_id}:{timestamp}:{gstin}:{api_action}"


class VayanaAuthSigner:
    def __init__(self, private_key_path, algorithm):
        self.private_key_path = Path(private_key_path)
        self.algorithm = str(algorithm or "").upper()
        if self.algorithm not in SUPPORTED_ALGORITHMS:
            raise GSTLookupConfigurationError("Vayana signature algorithm is missing or unsupported")
        try:
            key = serialization.load_pem_private_key(self.private_key_path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise GSTLookupConfigurationError("Vayana private key is missing or invalid") from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise GSTLookupConfigurationError("Vayana private key must be an RSA private key")
        self.private_key = key

    def sign(self, token):
        signature = self.private_key.sign(token.encode("utf-8"), padding.PKCS1v15(), SUPPORTED_ALGORITHMS[self.algorithm]())
        return base64.b64encode(signature).decode("ascii")

    def headers(self, *, cust_id, client_id, auth_gstin, api_action="TP", now=None, txn_id=None):
        transaction_id = txn_id or uuid.uuid4().hex
        token = build_auth_token(cust_id=cust_id, client_id="" if cust_id else client_id,
                                 txn_id=transaction_id, timestamp=vayana_timestamp(now),
                                 gstin=auth_gstin, api_action=api_action)
        headers = {"Content-Type": "application/json", "X-Asp-Auth-Token": token,
                   "X-Asp-Auth-Signature": self.sign(token)}
        return headers
