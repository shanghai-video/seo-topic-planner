"""Offline contract tests for the local DataForSEO OAuth connection.

Every test injects an HTTP transport, so no test contacts DataForSEO and no
credential is ever real.
"""

import base64
import hashlib
import json
import os
import stat
import tempfile
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from seo_topic_planner.errors import AuthError, StateError
from seo_topic_planner.oauth import (
    DataForSeoConnection,
    DataForSeoOAuth,
    LoopbackCallbackServer,
    OAuthTokens,
    TokenStore,
    pkce_pair,
)


def oauth_transport(record):
    def transport(url, body, headers):
        record.append((url, body))
        if url.endswith("/oauth/clients/register"):
            return 201, {"client_id": "client-xyz", "token_endpoint_auth_method": "none"}
        if url.endswith("/oauth/token"):
            return 200, {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "api",
            }
        return 404, {}

    return transport


class PkceTest(unittest.TestCase):
    def test_pkce_pair_is_s256_without_padding(self):
        verifier, challenge = pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        self.assertNotIn("=", verifier)
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        self.assertEqual(challenge, expected)
        self.assertNotEqual(pkce_pair()[0], verifier)


class TokenStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "nested" / "dataforseo-oauth.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_is_user_only_and_clearable(self):
        store = TokenStore(self.path)
        self.assertIsNone(store.load())
        store.save(
            OAuthTokens(
                access_token="a",
                refresh_token="r",
                expires_at=123.0,
                client_id="c",
                redirect_uri="http://127.0.0.1:1/callback",
            )
        )
        loaded = store.load()
        self.assertEqual(loaded.access_token, "a")
        self.assertEqual(loaded.client_id, "c")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertTrue(store.clear())
        self.assertIsNone(store.load())
        self.assertFalse(store.clear())

    def test_invalid_or_empty_file_reads_as_absent(self):
        store = TokenStore(self.path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(store.load())
        store.path.write_text(json.dumps({"access_token": ""}), encoding="utf-8")
        self.assertIsNone(store.load())


class OAuthClientTest(unittest.TestCase):
    def test_registers_public_client_and_builds_pkce_url(self):
        record = []
        oauth = DataForSeoOAuth(transport=oauth_transport(record))
        client_id = oauth.register_client(["http://127.0.0.1:8123/callback"])
        self.assertEqual(client_id, "client-xyz")
        register_body = record[0][1]
        self.assertEqual(register_body["token_endpoint_auth_method"], "none")
        self.assertEqual(register_body["scope"], "api")
        verifier, challenge = pkce_pair()
        url = oauth.authorization_url(
            client_id=client_id,
            redirect_uri="http://127.0.0.1:8123/callback",
            challenge=challenge,
            state="state-1",
        )
        params = dict(parse_qsl(urlparse(url).query))
        self.assertEqual(params["response_type"], "code")
        self.assertEqual(params["code_challenge"], challenge)
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertEqual(params["client_id"], "client-xyz")
        self.assertNotIn("client_secret", url)

    def test_exchange_and_refresh_parse_tokens(self):
        record = []
        oauth = DataForSeoOAuth(transport=oauth_transport(record))
        tokens = oauth.exchange_code(
            code="abc", verifier="v", client_id="client-xyz", redirect_uri="http://127.0.0.1:1/callback"
        )
        self.assertEqual(tokens.access_token, "access-1")
        self.assertEqual(tokens.refresh_token, "refresh-1")
        self.assertGreater(tokens.expires_at, 0)
        self.assertEqual(record[-1][1]["grant_type"], "authorization_code")
        self.assertEqual(record[-1][1]["code_verifier"], "v")
        refreshed = oauth.refresh(refresh_token="refresh-1", client_id="client-xyz")
        self.assertEqual(refreshed.access_token, "access-1")
        self.assertEqual(record[-1][1]["grant_type"], "refresh_token")

    def test_rejected_registration_raises_without_echoing_body(self):
        oauth = DataForSeoOAuth(
            transport=lambda url, body, headers: (400, {"error": "invalid_client_metadata", "secret": "leak"})
        )
        with self.assertRaises(AuthError) as ctx:
            oauth.register_client(["http://127.0.0.1:1/callback"])
        self.assertNotIn("leak", str(ctx.exception))


class ConnectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "dataforseo-oauth.json"

    def tearDown(self):
        self.temp.cleanup()

    def _connection(self, *, opener, record=None, clock=None):
        return DataForSeoConnection(
            TokenStore(self.path),
            oauth=DataForSeoOAuth(transport=oauth_transport(record if record is not None else [])),
            opener=opener,
            clock=clock or (lambda: 1_000.0),
        )

    @staticmethod
    def _approving_opener(code="test-code", state=None):
        def opener(url):
            params = dict(parse_qsl(urlparse(url).query))
            incoming_state = params["state"] if state is None else state
            with urllib.request.urlopen(
                f"{params['redirect_uri']}?code={code}&state={incoming_state}", timeout=5
            ) as response:
                response.read()
            return True

        return opener

    def test_status_is_auth_required_then_ready_without_network(self):
        connection = self._connection(opener=lambda url: True)
        self.assertEqual(connection.status(), {"provider": "dataforseo_mcp", "status": "AUTH_REQUIRED"})
        self.assertFalse(connection.is_connected())
        TokenStore(self.path).save(OAuthTokens(access_token="a", expires_at=10_000.0))
        self.assertEqual(connection.status(), {"provider": "dataforseo_mcp", "status": "READY"})

    def test_full_authorization_flow_stores_token_and_hides_it(self):
        record = []
        connection = self._connection(opener=self._approving_opener(), record=record)
        begun = connection.begin_authorization()
        self.assertEqual(begun["status"], "AWAITING_AUTHORIZATION")
        self.assertTrue(begun["browser_opened"])
        self.assertTrue(begun["authorization_url"].startswith("https://data.dataforseo.com/oauth/authorize?"))
        final = connection.finish_authorization(timeout_seconds=5)
        self.assertEqual(final, {"provider": "dataforseo_mcp", "status": "READY"})
        stored = TokenStore(self.path).load()
        self.assertEqual(stored.access_token, "access-1")
        self.assertEqual(stored.client_id, "client-xyz")
        # The access token must never appear in the tool-facing payloads.
        self.assertNotIn("access-1", json.dumps(begun))
        self.assertNotIn("access-1", json.dumps(final))
        self.assertNotIn("client_secret", json.dumps(begun))

    def test_state_mismatch_is_rejected(self):
        connection = self._connection(opener=self._approving_opener(state="attacker-state"))
        connection.begin_authorization()
        with self.assertRaises(AuthError):
            connection.finish_authorization(timeout_seconds=5)
        self.assertIsNone(TokenStore(self.path).load())

    def test_timeout_keeps_pending_then_disconnect_clears(self):
        connection = self._connection(opener=lambda url: True)
        connection.begin_authorization()
        pending = connection.finish_authorization(timeout_seconds=0.1)
        self.assertEqual(pending["status"], "AWAITING_AUTHORIZATION")
        connection.disconnect()
        with self.assertRaises(StateError):
            connection.finish_authorization(timeout_seconds=0.1)

    def test_finish_without_start_raises(self):
        connection = self._connection(opener=lambda url: True)
        with self.assertRaises(StateError):
            connection.finish_authorization(timeout_seconds=0.1)

    def test_expired_token_refreshes_and_missing_refresh_requires_reconnect(self):
        store = TokenStore(self.path)
        store.save(
            OAuthTokens(
                access_token="old", refresh_token="refresh-1", expires_at=10.0, client_id="client-xyz"
            )
        )
        connection = self._connection(opener=lambda url: True, clock=lambda: 10_000.0)
        self.assertEqual(connection.access_token(), "access-1")
        self.assertEqual(store.load().refresh_token, "refresh-1")
        store.save(OAuthTokens(access_token="old", expires_at=10.0))
        with self.assertRaises(AuthError):
            connection.access_token()

    def test_expired_without_refresh_and_status_stays_ready_for_refreshable(self):
        store = TokenStore(self.path)
        store.save(OAuthTokens(access_token="old", refresh_token="refresh-1", expires_at=10.0, client_id="c"))
        connection = self._connection(opener=lambda url: True, clock=lambda: 10_000.0)
        # READY without network: the refresh happens lazily on first use.
        self.assertEqual(connection.status()["status"], "READY")

    def test_access_token_without_connection_raises(self):
        connection = self._connection(opener=lambda url: True)
        with self.assertRaises(AuthError):
            connection.access_token()


class LoopbackServerTest(unittest.TestCase):
    def test_captures_first_callback_and_stops(self):
        server = LoopbackCallbackServer()
        server.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/callback?code=c&state=s", timeout=5
            ) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(server.wait(timeout=2), {"code": "c", "state": "s"})
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
