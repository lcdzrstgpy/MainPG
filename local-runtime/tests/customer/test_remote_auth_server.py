from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from wh_local.customer.auth_server import create_auth_app
from wh_local.customer.db_store import SQLiteCustomerSessionStore
from wh_local.customer.local_session import LocalSessionService
from wh_local.customer.remote_client import normalize_login_response


class RemoteCustomerAuthServerTest(unittest.TestCase):
    def test_remote_login_token_is_hashed_and_can_create_local_session(self) -> None:
        db_path = Path(tempfile.mkdtemp(prefix="wh_remote_auth_test_")) / "auth.sqlite3"
        client = TestClient(create_auth_app(db_path))

        register_response = client.post(
            "/api/customer/register",
            json={
                "username": "remote_admin",
                "email": "remote_admin@example.com",
                "password": "Secret123!",
                "role": "admin",
                "workspace_code": "wh_remote",
                "workspace_name": "远端账号工作区",
            },
        )
        self.assertEqual(register_response.status_code, 200, register_response.text)

        login_response = client.post(
            "/api/customer/login",
            json={"username": "remote_admin", "password": "Secret123!"},
        )
        self.assertEqual(login_response.status_code, 200, login_response.text)
        payload = login_response.json()
        self.assertTrue(payload["token"].startswith("wh_auth_"))

        remote_customer = normalize_login_response(payload)
        self.assertEqual(remote_customer.username, "remote_admin")
        self.assertEqual(remote_customer.role, "admin")
        self.assertEqual(remote_customer.workspace_code, "wh_remote")
        self.assertTrue(remote_customer.remote_token.startswith("wh_auth_"))

        local_sessions = LocalSessionService(SQLiteCustomerSessionStore(db_path))
        local_session = local_sessions.login_customer(remote_customer)
        self.assertTrue(local_session.token.startswith("wh_local_"))
        self.assertEqual(local_sessions.me(local_session.token)["workspace_code"], "wh_remote")

        me_response = client.get(
            "/api/customer/me",
            headers={"Authorization": f"Bearer {payload['token']}"},
        )
        self.assertEqual(me_response.status_code, 200, me_response.text)
        self.assertEqual(me_response.json()["account"]["username"], "remote_admin")

        logout_response = client.post(
            "/api/customer/logout",
            headers={"Authorization": f"Bearer {payload['token']}"},
        )
        self.assertEqual(logout_response.status_code, 200, logout_response.text)
        self.assertEqual(
            client.get("/api/customer/me", headers={"Authorization": f"Bearer {payload['token']}"}).status_code,
            401,
        )

        with sqlite3.connect(db_path) as conn:
            remote_token_hash = conn.execute("SELECT token_hash FROM auth_platform_sessions").fetchone()[0]
            local_token_hash = conn.execute("SELECT token_hash FROM customer_sessions").fetchone()[0]
        self.assertEqual(len(remote_token_hash), 64)
        self.assertEqual(len(local_token_hash), 64)
        self.assertNotIn(payload["token"], {remote_token_hash, local_token_hash})

    def test_forgot_and_reset_password_flow(self) -> None:
        db_path = Path(tempfile.mkdtemp(prefix="wh_remote_auth_reset_test_")) / "auth.sqlite3"
        client = TestClient(create_auth_app(db_path))

        self.assertEqual(
            client.post(
                "/api/customer/register",
                json={
                    "username": "reset_user",
                    "email": "reset_user@example.com",
                    "password": "OldSecret123!",
                    "role": "operator",
                },
            ).status_code,
            200,
        )
        old_login = client.post("/api/customer/login", json={"username": "reset_user", "password": "OldSecret123!"})
        self.assertEqual(old_login.status_code, 200, old_login.text)
        old_token = old_login.json()["token"]

        forgot_response = client.post("/api/customer/forgot-password", json={"username": "reset_user"})
        self.assertEqual(forgot_response.status_code, 200, forgot_response.text)
        reset_token = forgot_response.json()["raw"]["reset_token"]
        self.assertTrue(reset_token.startswith("wh_reset_"))

        with sqlite3.connect(db_path) as conn:
            token_hash = conn.execute("SELECT token_hash FROM auth_password_reset_tokens").fetchone()[0]
        self.assertEqual(len(token_hash), 64)
        self.assertNotEqual(token_hash, reset_token)

        reset_response = client.post(
            "/api/customer/reset-password",
            json={"reset_token": reset_token, "new_password": "NewSecret123!"},
        )
        self.assertEqual(reset_response.status_code, 200, reset_response.text)

        self.assertEqual(
            client.get("/api/customer/me", headers={"Authorization": f"Bearer {old_token}"}).status_code,
            401,
        )
        self.assertEqual(
            client.post("/api/customer/login", json={"username": "reset_user", "password": "OldSecret123!"}).status_code,
            403,
        )
        self.assertEqual(
            client.post("/api/customer/login", json={"username": "reset_user", "password": "NewSecret123!"}).status_code,
            200,
        )
        self.assertEqual(
            client.post(
                "/api/customer/reset-password",
                json={"reset_token": reset_token, "new_password": "AnotherSecret123!"},
            ).status_code,
            403,
        )

    def test_change_password_requires_current_password(self) -> None:
        db_path = Path(tempfile.mkdtemp(prefix="wh_remote_auth_change_test_")) / "auth.sqlite3"
        client = TestClient(create_auth_app(db_path))

        self.assertEqual(
            client.post(
                "/api/customer/register",
                json={
                    "username": "change_user",
                    "email": "change_user@example.com",
                    "password": "Before123!",
                    "role": "admin",
                },
            ).status_code,
            200,
        )
        self.assertEqual(
            client.post(
                "/api/customer/change-password",
                json={"username": "change_user", "current_password": "Wrong123!", "new_password": "After123!"},
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                "/api/customer/change-password",
                json={"username": "change_user", "current_password": "Before123!", "new_password": "After123!"},
            ).status_code,
            200,
        )
        self.assertEqual(
            client.post("/api/customer/login", json={"username": "change_user", "password": "Before123!"}).status_code,
            403,
        )
        self.assertEqual(
            client.post("/api/customer/login", json={"username": "change_user", "password": "After123!"}).status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
