"""Opaque Redis sessions, password hashing, and workspace authorization."""

import hashlib
import hmac
import json
import os
import secrets
from functools import lru_cache

from fastapi import HTTPException, Request
from sqlalchemy import select

from salma.db import session
from salma.platform_models import Business, Membership, User
from salma.settings import settings

COOKIE = "salma_session"
SESSION_TTL = 60 * 60 * 24


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password):
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12 to 256 characters")
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"scrypt${salt.hex()}${result.hex()}"


def verify_password(password, stored):
    try:
        kind, salt, expected = stored.split("$")
        if kind != "scrypt" or len(password) > 256:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except ValueError, TypeError:
        return False


@lru_cache
def redis_client():
    from redis import Redis

    return Redis.from_url(settings().redis_url, decode_responses=True)


def redis_call(method, *args, **kwargs):
    try:
        return getattr(redis_client(), method)(*args, **kwargs)
    except Exception as exc:
        raise HTTPException(503, "Session service unavailable; please retry") from exc


def rate_limit(key, maximum=15):
    key = "salma:rate:" + digest(key)
    count = redis_call("incr", key)
    if count == 1:
        redis_call("expire", key, 300)
    if count > maximum:
        raise HTTPException(429, "Too many attempts; retry in five minutes")


def issue_session(user_id, response):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    redis_call(
        "setex",
        "salma:session:" + digest(token),
        SESSION_TTL,
        json.dumps({"user_id": user_id, "csrf": csrf}),
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_TTL,
        httponly=True,
        secure=getattr(settings(), "session_secure", False),
        samesite="lax",
        path="/",
    )
    return csrf


def require_user(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HTTPException(401, "Sign in required")
    raw = redis_call("get", "salma:session:" + digest(token))
    if not raw:
        raise HTTPException(401, "Session expired; sign in again")
    data = json.loads(raw)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, data["csrf"]):
            raise HTTPException(403, "Invalid CSRF token")
    with session() as db:
        user = db.get(User, data["user_id"])
        if not user:
            raise HTTPException(401, "Account unavailable")
        db.expunge(user)
    request.state.csrf_token = data["csrf"]
    request.state.user = user
    return user


def require_business(request: Request, business_id: str):
    user = require_user(request)
    with session() as db:
        if user.role != "operator" and not db.get(Membership, (user.id, business_id)):
            raise HTTPException(404, "Workspace not found")
        business = db.get(Business, business_id)
        if not business:
            raise HTTPException(404, "Workspace not found")
        db.expunge(business)
        return business


def bootstrap_operator():
    email = os.environ.get("SALMA_OPERATOR_EMAIL", "").strip().lower()
    password = os.environ.get("SALMA_OPERATOR_PASSWORD", "")
    if not email or not password:
        raise SystemExit("Set SALMA_OPERATOR_EMAIL and SALMA_OPERATOR_PASSWORD")
    with session() as db:
        if db.scalar(select(User).where(User.email == email)):
            raise SystemExit("Account already exists; no changes made")
        db.add(User(email=email, password_hash=hash_password(password), role="operator"))
        db.commit()
    print("Operator account created")


if __name__ == "__main__":
    bootstrap_operator()


def require_operator(request: Request):
    user = require_user(request)
    if user.role != "operator":
        raise HTTPException(403, "Platform operator required")
    return user
