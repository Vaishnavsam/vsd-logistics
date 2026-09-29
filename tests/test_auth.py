import unittest
from unittest.mock import patch

from fleet_ops.auth import AuthManager
from fleet_ops.errors import DomainError


class AuthManagerTests(unittest.TestCase):
    def test_requires_a_long_configured_password(self) -> None:
        with self.assertRaises(ValueError):
            AuthManager("short")

    def test_session_expires(self) -> None:
        manager = AuthManager("a-secure-test-password")
        session = manager.login("a-secure-test-password", "127.0.0.1")
        with patch("fleet_ops.auth.time.monotonic", return_value=session.expires_at + 1):
            with self.assertRaises(DomainError) as raised:
                manager.authenticate(session.token)
        self.assertEqual(raised.exception.code, "session_expired")

    def test_logout_invalidates_session(self) -> None:
        manager = AuthManager("a-secure-test-password")
        session = manager.login("a-secure-test-password", "127.0.0.1")
        manager.logout(session.token)
        with self.assertRaises(DomainError) as raised:
            manager.authenticate(session.token)
        self.assertEqual(raised.exception.code, "authentication_required")


if __name__ == "__main__":
    unittest.main()
