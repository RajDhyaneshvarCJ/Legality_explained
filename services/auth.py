import os
import hmac
import hashlib
import time
from fastapi import Request, HTTPException

APP_PASSWORD = os.getenv("APP_PASSWORD")
# secret used to sign the session cookie so it can't be forged by guessing.
# falls back to the password itself if unset, but you should set this
# separately in production (any random string works).
COOKIE_SECRET = os.getenv("COOKIE_SECRET", APP_PASSWORD or "")
COOKIE_NAME = "legal_explainer_session"
SESSION_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _sign(value: str) -> str:
    return hmac.new(COOKIE_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def make_session_cookie() -> str:
    expiry = str(int(time.time()) + SESSION_SECONDS)
    return f"{expiry}.{_sign(expiry)}"


def is_valid_session_cookie(cookie_value: str | None) -> bool:
    if not cookie_value or "." not in cookie_value:
        return False
    expiry, _, sig = cookie_value.partition(".")
    if not hmac.compare_digest(_sign(expiry), sig):
        return False
    try:
        return int(expiry) > time.time()
    except ValueError:
        return False


def check_password(candidate: str) -> bool:
    if not APP_PASSWORD:
        return False
    return hmac.compare_digest(candidate, APP_PASSWORD)


def require_auth(request: Request):
    # used as a fastapi dependency on every protected route. if APP_PASSWORD
    # isn't set, auth is off entirely (e.g. local dev) — same behavior as before.
    if not APP_PASSWORD:
        return
    cookie = request.cookies.get(COOKIE_NAME)
    if not is_valid_session_cookie(cookie):
        raise HTTPException(status_code=401, detail="authentication required")
