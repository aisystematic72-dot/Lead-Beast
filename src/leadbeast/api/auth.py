"""Single operator login plus an API token for scripts. Roles restrict approve vs view (M23)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from leadbeast.core.config import get_settings
from leadbeast.core.db import get_session
from leadbeast.db import models as m

COOKIE = "lb_session"
ROLE_RANK = {"viewer": 0, "approver": 1, "operator": 2}


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def new_api_token() -> tuple[str, str]:
    t = "lb_" + secrets.token_urlsafe(32)
    return t, token_hash(t)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key.get_secret_value(), salt="session")


def make_session_cookie(username: str) -> str:
    return _serializer().dumps({"u": username})


@dataclass(frozen=True)
class User:
    username: str
    role: str

    def can(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


async def current_user(request: Request, s: AsyncSession = Depends(get_session)) -> User:
    st = get_settings()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = auth.split(" ", 1)[1].strip()
        if st.api_token_hash and hmac.compare_digest(token_hash(tok), st.api_token_hash):
            return User("api", "operator")
        raise HTTPException(401, "invalid token")
    cookie = request.cookies.get(COOKIE)
    if cookie:
        try:
            data = _serializer().loads(cookie, max_age=60 * 60 * 24 * 7)
        except BadSignature as e:
            raise HTTPException(401, "session expired") from e
        u = await s.scalar(select(m.AppUser).where(m.AppUser.username == data["u"]))
        if u is not None:
            return User(u.username, u.role)
    if st.environment == "test":
        return User("test", "operator")
    raise HTTPException(401, "login required")


def require(role: str):  # type: ignore[no-untyped-def]
    async def dep(user: User = Depends(current_user)) -> User:
        if not user.can(role):
            raise HTTPException(403, f"needs role {role}")
        return user

    return dep
