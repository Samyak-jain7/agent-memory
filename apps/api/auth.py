import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from uuid import UUID

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    actor_id: UUID


def principal_from_headers(
    authorization: str = Header(alias="Authorization"),
) -> Principal:
    try:
        scheme, token = authorization.split(" ", 1)
        encoded, signature = token.split(".", 1)
        secret = os.environ["MEMORY_AUTH_SECRET"].encode()
        expected = hmac.new(secret, encoded.encode(), hashlib.sha256).hexdigest()
        if scheme.lower() != "bearer" or not hmac.compare_digest(signature, expected):
            raise ValueError
        tenant_id, actor_id = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode().split(":")
        return Principal(tenant_id=UUID(tenant_id), actor_id=UUID(actor_id))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail={"code": "invalid_credentials"}) from None


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "resource_not_found"})
