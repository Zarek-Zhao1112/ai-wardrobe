"""电脑与手机共用的配对发现协议。"""

import hmac

DISCOVERY_PATH = "/pairing/discovery"


def discovery_proof(token: str, nonce: str, host: str, port: int, state: str) -> str:
    message = f"wardrobe-discovery-v1\n{nonce}\n{host}:{port}\n{state}"
    return hmac.digest(token.encode("ascii"), message.encode("ascii"), "sha256").hex()
