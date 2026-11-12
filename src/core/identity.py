"""
Signed requests between the console and the agent.

The agent acts on the role and tenant named in each request, so it must know
the request really came from the console (which authenticated the user) and was
not altered. The console signs the whole request with a key from Secrets
Manager; the agent verifies the signature and its age before doing anything.
Permission to invoke the runtime is therefore not enough to impersonate a user.
"""

import hashlib
import hmac
import json
import time

import config


class IdentityError(Exception):
    pass


def _canonical(payload: dict) -> bytes:
    body = {k: v for k, v in payload.items() if k != "auth"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


def _signature(key: str, timestamp: int, payload: dict) -> str:
    message = str(timestamp).encode() + b"." + _canonical(payload)
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


def sign(payload: dict, key: str = None, now: float = None) -> dict:
    """Return the payload with an ``auth`` stamp; unchanged if no key is configured."""
    key = config.secret("IDENTITY_SIGNING_KEY") if key is None else key
    if not key:
        return payload
    timestamp = int(now if now is not None else time.time())
    return {**payload, "auth": {"ts": timestamp, "sig": _signature(key, timestamp, payload)}}


def verify(payload: dict, key: str = None, now: float = None) -> None:
    """Raise IdentityError unless the payload carries a valid, recent signature."""
    key = config.secret("IDENTITY_SIGNING_KEY") if key is None else key
    if not key:
        # Only a local development process runs without a key.
        if config.ENVIRONMENT == "dev":
            return
        raise IdentityError("The agent has no identity signing key configured.")
    auth = payload.get("auth")
    if not isinstance(auth, dict) or not isinstance(auth.get("ts"), int) or not isinstance(auth.get("sig"), str):
        raise IdentityError("The request is not signed by the console.")
    age = (now if now is not None else time.time()) - auth["ts"]
    if not -60 <= age <= config.IDENTITY_MAX_AGE_SECONDS:
        raise IdentityError("The request signature has expired.")
    if not hmac.compare_digest(auth["sig"], _signature(key, auth["ts"], payload)):
        raise IdentityError("The request signature does not match its contents.")
