"""Mint a dev JWT for local testing, dependency-free.

Signed HS256 with JWT_SECRET, carrying the `id` claim the tools read via
TokenClaim("id").

    JWT_SECRET=secret uv run python scripts/dev_token.py [user_id]
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def make_token(
    secret: str,
    user_id: str = "dev-user",
    ttl: int = 86_400,
) -> str:

    header = {"alg": "HS256", "typ": "JWT"}

    now = int(time.time())
    payload = {"id": user_id, "sub": user_id, "iat": now, "exp": now + ttl}

    signing_input = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + b"."
        + _b64(json.dumps(payload, separators=(",", ":")).encode())
    )

    signature = hmac.new(
        secret.encode(), signing_input, hashlib.sha256
    ).digest()

    return (signing_input + b"." + _b64(signature)).decode()


if __name__ == "__main__":

    secret = os.environ.get("JWT_SECRET")

    if not secret:
        sys.exit("Set JWT_SECRET in the environment first.")

    user_id = sys.argv[1] if len(sys.argv) > 1 else "dev-user"

    print(make_token(secret, user_id))
