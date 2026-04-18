"""Tests for admin authentication and user tracking."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from jenkins_job_insight import storage
from jenkins_job_insight.config import get_settings


@pytest.fixture
def _init_db(temp_db_path):
    """Initialize database with test path."""
    import asyncio

    with patch.object(storage, "DB_PATH", temp_db_path):
        asyncio.run(storage.init_db())
        yield


@pytest.fixture
def client(_init_db, temp_db_path):
    """Create a test client with admin key configured."""
    with patch.dict(
        os.environ,
        {
            "ADMIN_KEY": "test-admin-key-16chars",  # pragma: allowlist secret
            "SECURE_COOKIES": "false",
        },
    ):
        get_settings.cache_clear()
        with patch.object(storage, "DB_PATH", temp_db_path):
            storage.configure_admin_key(
                "test-admin-key-16chars"
            )  # pragma: allowlist secret
            from jenkins_job_insight.main import app

            with TestClient(app) as c:
                yield c
        get_settings.cache_clear()
        storage.configure_admin_key("")  # cleanup


def _admin_login(
    client,
    username="admin",
    api_key="test-admin-key-16chars",  # pragma: allowlist secret
):
    """Helper to login as admin and return cookies."""
    resp = client.post(
        "/api/auth/login", json={"username": username, "api_key": api_key}
    )
    assert resp.status_code == 200
    return resp.cookies


class TestAuthLogin:
    def test_admin_login_success(self, client):
        resp = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "api_key": "test-admin-key-16chars",  # pragma: allowlist secret
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["is_admin"] is True
        assert data["role"] == "admin"
        assert "jji_session" in resp.cookies

    def test_admin_login_wrong_key(self, client):
        resp = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "api_key": "wrong-key",  # pragma: allowlist secret
            },
        )
        assert resp.status_code == 401

    def test_admin_login_missing_fields(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin"})
        assert resp.status_code == 400

    def test_login_invalid_json(self, client):
        resp = client.post(
            "/api/auth/login",
            content="not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


class TestAuthMe:
    def test_me_as_admin(self, client):
        cookies = _admin_login(client)
        resp = client.get("/api/auth/me", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_admin"] is True
        assert data["role"] == "admin"

    def test_me_as_regular_user(self, client):
        resp = client.get("/api/auth/me", cookies={"jji_username": "testuser"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testuser"
        assert data["is_admin"] is False
        assert data["role"] == "user"

    def test_me_no_auth(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == ""
        assert data["is_admin"] is False


class TestAuthLogout:
    def test_logout(self, client):
        cookies = _admin_login(client)
        resp = client.post("/api/auth/logout", cookies=cookies)
        assert resp.status_code == 200
        # Session should be invalidated
        resp2 = client.get("/api/auth/me", cookies=cookies)
        data = resp2.json()
        assert data["is_admin"] is False


class TestAdminUsers:
    def test_create_admin_user(self, client):
        cookies = _admin_login(client)
        resp = client.post(
            "/api/admin/users", json={"username": "newadmin"}, cookies=cookies
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "newadmin"
        assert data["role"] == "admin"
        assert "api_key" in data

    def test_create_admin_requires_auth(self, client):
        resp = client.post("/api/admin/users", json={"username": "newadmin"})
        assert resp.status_code == 403

    def test_create_admin_regular_user_forbidden(self, client):
        resp = client.post(
            "/api/admin/users",
            json={"username": "newadmin"},
            cookies={"jji_username": "regular"},
        )
        assert resp.status_code == 403

    def test_list_users(self, client):
        cookies = _admin_login(client)
        resp = client.get("/api/admin/users", cookies=cookies)
        assert resp.status_code == 200
        assert "users" in resp.json()

    def test_list_users_requires_admin(self, client):
        resp = client.get("/api/admin/users", cookies={"jji_username": "regular"})
        assert resp.status_code == 403

    def test_delete_admin_user(self, client):
        cookies = _admin_login(client)
        # Create then delete
        client.post("/api/admin/users", json={"username": "todelete"}, cookies=cookies)
        resp = client.delete("/api/admin/users/todelete", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "todelete"

    def test_delete_self_forbidden(self, client):
        cookies = _admin_login(client)
        resp = client.delete("/api/admin/users/admin", cookies=cookies)
        assert resp.status_code == 400

    def test_delete_nonexistent(self, client):
        cookies = _admin_login(client)
        resp = client.delete("/api/admin/users/nonexistent", cookies=cookies)
        assert resp.status_code == 404

    def test_rotate_key(self, client):
        cookies = _admin_login(client)
        client.post(
            "/api/admin/users", json={"username": "rotateuser"}, cookies=cookies
        )
        resp = client.post("/api/admin/users/rotateuser/rotate-key", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "rotateuser"
        assert "new_api_key" in data


class TestDeleteJobAdminOnly:
    def test_delete_job_requires_admin(self, client):
        """Regular users cannot delete jobs."""
        resp = client.delete(
            "/results/fake-job-id", cookies={"jji_username": "regular"}
        )
        assert resp.status_code == 403

    def test_delete_job_no_auth(self, client):
        """Unauthenticated users cannot delete jobs."""
        resp = client.delete("/results/fake-job-id")
        assert resp.status_code == 403

    def test_delete_job_as_admin(self, client):
        """Admin can delete jobs."""
        cookies = _admin_login(client)
        # Will get 404 since job doesn't exist, but NOT 403
        resp = client.delete("/results/fake-job-id", cookies=cookies)
        assert resp.status_code == 404  # Not found, not forbidden


class TestBearerTokenAuth:
    def test_bearer_admin_key(self, client):
        """Bearer token with admin_key works."""
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer test-admin-key-16chars"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_admin"] is True

    def test_bearer_user_api_key(self, client):
        """Bearer token with user API key works."""
        # Create admin user via Bearer token (admin key)
        create_resp = client.post(
            "/api/admin/users",
            json={"username": "apiuser"},
            headers={"Authorization": "Bearer test-admin-key-16chars"},
        )
        assert create_resp.status_code == 200
        api_key = create_resp.json()["api_key"]
        # Use the created user's API key as Bearer token
        resp = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {api_key}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "apiuser"
        assert data["is_admin"] is True

    def test_bearer_invalid_key(self, client):
        """Bearer token with invalid key returns non-admin."""
        resp = client.get(
            "/api/auth/me", headers={"Authorization": "Bearer invalid-key"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_admin"] is False


class TestChangeUserRole:
    def test_promote_user_to_admin(self, client):
        cookies = _admin_login(client)
        # Create as admin first, demote to user, then promote back
        client.post(
            "/api/admin/users", json={"username": "promoteuser"}, cookies=cookies
        )
        client.put(
            "/api/admin/users/promoteuser/role",
            json={"role": "user"},
            cookies=cookies,
        )
        # Now promote from user to admin
        resp = client.put(
            "/api/admin/users/promoteuser/role",
            json={"role": "admin"},
            cookies=cookies,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "promoteuser"
        assert data["role"] == "admin"
        assert "api_key" in data  # API key generated on promotion

    def test_demote_admin_to_user(self, client):
        cookies = _admin_login(client)
        # Create admin user
        client.post("/api/admin/users", json={"username": "demoteme"}, cookies=cookies)
        # Demote to user
        resp = client.put(
            "/api/admin/users/demoteme/role",
            json={"role": "user"},
            cookies=cookies,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "user"
        assert "api_key" not in data  # No key for regular users

    def test_change_role_requires_admin(self, client):
        resp = client.put(
            "/api/admin/users/someone/role",
            json={"role": "admin"},
            cookies={"jji_username": "regular"},
        )
        assert resp.status_code == 403

    def test_change_role_cannot_change_self(self, client):
        cookies = _admin_login(client)
        resp = client.put(
            "/api/admin/users/admin/role",
            json={"role": "user"},
            cookies=cookies,
        )
        assert resp.status_code == 400

    def test_change_role_same_role(self, client):
        cookies = _admin_login(client)
        client.post(
            "/api/admin/users", json={"username": "alreadyadmin"}, cookies=cookies
        )
        resp = client.put(
            "/api/admin/users/alreadyadmin/role",
            json={"role": "admin"},
            cookies=cookies,
        )
        assert resp.status_code == 400

    def test_change_role_missing_role(self, client):
        cookies = _admin_login(client)
        resp = client.put(
            "/api/admin/users/someone/role",
            json={},
            cookies=cookies,
        )
        assert resp.status_code == 400

    def test_change_role_user_not_found(self, client):
        cookies = _admin_login(client)
        resp = client.put(
            "/api/admin/users/nonexistent/role",
            json={"role": "admin"},
            cookies=cookies,
        )
        assert resp.status_code == 404


class TestUserTokens:
    def test_save_and_get_tokens(self, client):
        """Tokens round-trip through encrypt/decrypt."""
        import time

        # Track a user first
        client.get("/api/dashboard", cookies={"jji_username": "tokenuser"})
        time.sleep(0.1)
        # Save tokens
        resp = client.put(
            "/api/user/tokens",
            json={
                "github_token": "ghp_test123",
                "jira_email": "a@b.com",
                "jira_token": "jira_tok",
            },
            cookies={"jji_username": "tokenuser"},
        )
        assert resp.status_code == 200
        # Get tokens back
        resp = client.get("/api/user/tokens", cookies={"jji_username": "tokenuser"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["github_token"] == "ghp_test123"
        assert data["jira_email"] == "a@b.com"
        assert data["jira_token"] == "jira_tok"

    def test_get_tokens_no_user(self, client):
        resp = client.get("/api/user/tokens")
        assert resp.status_code == 401

    def test_save_tokens_no_user(self, client):
        resp = client.put("/api/user/tokens", json={"github_token": "x"})
        assert resp.status_code == 401

    def test_get_tokens_nonexistent_user(self, client):
        """Non-tracked user gets empty tokens."""
        resp = client.get("/api/user/tokens", cookies={"jji_username": "ghost"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["github_token"] == ""

    def test_save_partial_tokens(self, client):
        """Can save just one token without affecting others."""
        import time

        client.get("/api/dashboard", cookies={"jji_username": "partial"})
        time.sleep(0.1)
        # Save only github_token
        client.put(
            "/api/user/tokens",
            json={"github_token": "ghp_only"},
            cookies={"jji_username": "partial"},
        )
        resp = client.get("/api/user/tokens", cookies={"jji_username": "partial"})
        data = resp.json()
        assert data["github_token"] == "ghp_only"
        assert data["jira_email"] == ""

    def test_tokens_encrypted_at_rest(self, client, temp_db_path):
        """Verify tokens are not stored as plaintext in the DB."""
        import asyncio
        import time

        import aiosqlite

        client.get("/api/dashboard", cookies={"jji_username": "enctest"})
        time.sleep(0.1)
        client.put(
            "/api/user/tokens",
            json={"github_token": "ghp_secret_value"},
            cookies={"jji_username": "enctest"},
        )

        # Read raw DB value
        async def check():
            async with aiosqlite.connect(temp_db_path) as db:
                cursor = await db.execute(
                    "SELECT github_token_enc FROM users WHERE username = 'enctest'"
                )
                row = await cursor.fetchone()
                assert row is not None
                raw = row[0]
                assert raw != "ghp_secret_value"  # Not plaintext
                assert raw.startswith("enc:")  # Encrypted

        asyncio.run(check())


class TestUserTracking:
    def test_regular_user_tracked(self, client):
        """Regular user activity is tracked in the users table."""
        import time

        # Make a request as a regular user
        client.get("/api/dashboard", cookies={"jji_username": "trackeduser"})
        # Give the fire-and-forget task a moment
        time.sleep(0.1)
        # Check the user was tracked
        cookies = _admin_login(client)
        resp = client.get("/api/admin/users", cookies=cookies)
        users = resp.json()["users"]
        usernames = [u["username"] for u in users]
        assert "trackeduser" in usernames
