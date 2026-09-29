from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass

from fleet_ops.errors import DomainError

_SESSION_SECONDS = 8 * 60 * 60
_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_SECONDS = 15 * 60


@dataclass(frozen=True)
class Session:
    token: str
    expires_at: float


class AuthManager:
    def __init__(self, admin_password: str) -> None:
        if len(admin_password) < 12:
            raise ValueError("FLEET_OPS_ADMIN_PASSWORD must be at least 12 characters.")
        self._admin_password = admin_password
        self._sessions: dict[str, Session] = {}
        self._failed_attempts: dict[str, tuple[int, float]] = {}

    def login(self, password: str, client_id: str) -> Session:
        now = time.monotonic()
        attempts, locked_until = self._failed_attempts.get(client_id, (0, 0))
        if locked_until > now:
            raise DomainError(
                "login_rate_limited",
                "Too many unsuccessful sign-in attempts. Try again later.",
                429,
            )
        if not hmac.compare_digest(password.encode("utf-8"), self._admin_password.encode("utf-8")):
            attempts += 1
            if attempts >= _MAX_LOGIN_ATTEMPTS:
                self._failed_attempts[client_id] = (0, now + _LOCKOUT_SECONDS)
                raise DomainError(
                    "login_rate_limited",
                    "Too many unsuccessful sign-in attempts. Try again later.",
                    429,
                )
            self._failed_attempts[client_id] = (attempts, 0)
            raise DomainError("invalid_credentials", "Password is incorrect.", 401)
        self._failed_attempts.pop(client_id, None)
        token = secrets.token_urlsafe(32)
        session = Session(token, now + _SESSION_SECONDS)
        self._sessions[token] = session
        return session

    def authenticate(self, token: str | None) -> Session:
        if not token:
            raise DomainError("authentication_required", "Sign in to access this resource.", 401)
        session = self._sessions.get(token)
        if session is None:
            raise DomainError("authentication_required", "Session is invalid. Sign in again.", 401)
        if session.expires_at <= time.monotonic():
            self._sessions.pop(token, None)
            raise DomainError("session_expired", "Session expired. Sign in again.", 401)
        return session

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
