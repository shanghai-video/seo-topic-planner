"""Local DataForSEO OAuth 2.1 connection owned by the Planner.

The official DataForSEO v3 MCP server is protected by OAuth 2.1
(``https://data.dataforseo.com``), advertising dynamic client registration,
public clients (``token_endpoint_auth_method=none``), authorization code with
PKCE (S256), and refresh tokens.  This module performs that flow from the local
Planner process so the plugin can use real DataForSEO data without asking a host
to inject a peer MCP's token and without persisting a DataForSEO API password.

Security boundaries:

* The authorization code, PKCE verifier, access token and refresh token never
  appear in MCP arguments, domain records, cache keys, errors, or logs.
* Tokens are stored in a user-only file (mode ``0600``) under the Planner data
  directory, never inside the plugin package or the repository.
* Only ``transport`` (HTTP) and the browser opener are injectable, so the whole
  flow is exercised offline in tests with no real network call.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from .errors import AuthError, StateError

DEFAULT_AUTHORIZATION_SERVER = "https://data.dataforseo.com"
DEFAULT_RESOURCE = "https://mcp.dataforseo.com/v3/mcp"
DEFAULT_SCOPES: tuple[str, ...] = ("api",)
DEFAULT_LEEWAY_SECONDS = 60.0

# (url, json_body, headers) -> (http_status, parsed_json)
JsonTransport = Callable[[str, dict[str, Any], Mapping[str, str]], "tuple[int, dict[str, Any]]"]


@dataclass
class OAuthTokens:
    """A user's DataForSEO authorization.  Never serialize outside ``TokenStore``."""

    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    scope: str = ""
    expires_at: float = 0.0
    client_id: str = ""
    redirect_uri: str = ""


class TokenStore:
    """User-only token persistence with atomic writes.

    The store is deliberately tiny: a single JSON object in a ``0700`` directory
    and a ``0600`` file.  It never enters version control because the default
    location is outside the repository.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> OAuthTokens | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or not raw.get("access_token"):
            return None
        return OAuthTokens(
            access_token=str(raw.get("access_token") or ""),
            refresh_token=str(raw.get("refresh_token") or ""),
            token_type=str(raw.get("token_type") or "Bearer"),
            scope=str(raw.get("scope") or ""),
            expires_at=float(raw.get("expires_at") or 0.0),
            client_id=str(raw.get("client_id") or ""),
            redirect_uri=str(raw.get("redirect_uri") or ""),
        )

    def save(self, tokens: OAuthTokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(tokens), handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, self.path)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    def clear(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise AuthError("could not remove the stored DataForSEO authorization") from exc


def pkce_pair() -> tuple[str, str]:
    """Return a high-entropy PKCE ``(verifier, S256 challenge)`` pair."""

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class DataForSeoOAuth:
    """Protocol-level OAuth 2.1 client for the DataForSEO official MCP server."""

    def __init__(
        self,
        *,
        authorization_server: str = DEFAULT_AUTHORIZATION_SERVER,
        resource: str = DEFAULT_RESOURCE,
        scopes: Sequence[str] = DEFAULT_SCOPES,
        timeout: float = 30.0,
        transport: JsonTransport | None = None,
    ) -> None:
        self.authorization_server = authorization_server.rstrip("/")
        self.resource = resource
        self.scopes = tuple(scopes)
        self._timeout = timeout
        self._transport = transport or self._http_post

    @property
    def registration_endpoint(self) -> str:
        return f"{self.authorization_server}/oauth/clients/register"

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.authorization_server}/oauth/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.authorization_server}/oauth/token"

    def register_client(self, redirect_uris: Sequence[str]) -> str:
        payload = {
            "client_name": "SEO Topic Planner",
            "redirect_uris": list(redirect_uris),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": " ".join(self.scopes),
            "application_type": "native",
        }
        parsed = self._post(self.registration_endpoint, payload)
        client_id = parsed.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise AuthError("DataForSEO did not return a client id for this installation")
        return client_id

    def authorization_url(self, *, client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.authorization_endpoint}?{query}"

    def exchange_code(self, *, code: str, verifier: str, client_id: str, redirect_uri: str) -> OAuthTokens:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
        return self._tokens_from(self._post(self.token_endpoint, payload))

    def refresh(self, *, refresh_token: str, client_id: str) -> OAuthTokens:
        payload = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
        return self._tokens_from(self._post(self.token_endpoint, payload))

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        status, parsed = self._transport(url, body, {"Accept": "application/json"})
        if status < 200 or status >= 300 or not isinstance(parsed, dict):
            raise AuthError("DataForSEO authorization server rejected the request")
        return parsed

    @staticmethod
    def _tokens_from(parsed: Mapping[str, Any]) -> OAuthTokens:
        access = parsed.get("access_token")
        if not isinstance(access, str) or not access:
            raise AuthError("DataForSEO did not return an access token")
        try:
            expires_in = float(parsed.get("expires_in") or 0.0)
        except (TypeError, ValueError):
            expires_in = 0.0
        return OAuthTokens(
            access_token=access,
            refresh_token=str(parsed.get("refresh_token") or ""),
            token_type=str(parsed.get("token_type") or "Bearer"),
            scope=str(parsed.get("scope") or ""),
            expires_at=time.time() + expires_in if expires_in > 0 else 0.0,
        )

    def _http_post(
        self, url: str, body: dict[str, Any], headers: Mapping[str, str]
    ) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            url,
            json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **dict(headers)},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # Read and discard the body; it may echo request parameters.
            with contextlib.suppress(Exception):
                exc.read()
            raise AuthError("DataForSEO authorization server rejected the request") from exc
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
            raise AuthError(
                f"DataForSEO authorization server is unreachable ({exc.__class__.__name__})"
            ) from exc
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        return status, parsed if isinstance(parsed, dict) else {}


class LoopbackCallbackServer:
    """Single-use loopback redirect listener for the authorization code.

    RFC 8252 native-app flow: bind ``127.0.0.1`` on an ephemeral port, hand the
    exact redirect URI to the authorization server, and capture only ``code``
    and ``state`` from the first GET.
    """

    def __init__(self, host: str = "127.0.0.1") -> None:
        self._host = host
        self._result: dict[str, str] | None = None
        self._event = threading.Event()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise StateError("the authorization callback listener is not running")
        return int(self._server.server_address[1])

    def start(self) -> None:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                params = dict(parse_qsl(urlparse(self.path).query))
                if outer._result is None:
                    outer._result = params
                    outer._event.set()
                body = (
                    b"<!doctype html><html><head><meta charset='utf-8'>"
                    b"<title>SEO Topic Planner</title></head><body>"
                    b"<h1>DataForSEO connection received</h1>"
                    b"<p>You can close this tab and return to SEO Topic Planner.</p>"
                    b"</body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # keep the stdio channel clean
                return

        self._server = HTTPServer((self._host, 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float) -> dict[str, str] | None:
        if not self._event.wait(timeout):
            return None
        return dict(self._result or {})

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


@dataclass
class PendingAuthorization:
    server: LoopbackCallbackServer
    client_id: str
    verifier: str
    state: str
    redirect_uri: str
    authorization_url: str
    created_at: float


class DataForSeoConnection:
    """Owns the Planner's DataForSEO authorization lifecycle.

    ``status()`` and ``is_connected()`` never make a network request.  A refresh
    happens lazily inside ``access_token()`` when the stored access token is at
    or near expiry.
    """

    def __init__(
        self,
        store: TokenStore,
        *,
        oauth: DataForSeoOAuth | None = None,
        opener: Callable[[str], bool] | None = None,
        server_factory: Callable[[], LoopbackCallbackServer] | None = None,
        pending_ttl: float = 900.0,
        leeway: float = DEFAULT_LEEWAY_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._oauth = oauth or DataForSeoOAuth()
        self._opener = opener or (lambda url: bool(webbrowser.open(url)))
        self._server_factory = server_factory or LoopbackCallbackServer
        self._pending_ttl = pending_ttl
        self._leeway = leeway
        self._clock = clock
        self._pending: PendingAuthorization | None = None

    def is_connected(self) -> bool:
        tokens = self._store.load()
        return bool(tokens and (tokens.access_token or tokens.refresh_token))

    def status(self) -> dict[str, str]:
        if not self.is_connected():
            return {"provider": "dataforseo_mcp", "status": "AUTH_REQUIRED"}
        return {"provider": "dataforseo_mcp", "status": "READY"}

    def access_token(self) -> str:
        tokens = self._store.load()
        if tokens is None:
            raise AuthError("DataForSEO authorization is not connected")
        if tokens.access_token and not self._expired(tokens):
            return tokens.access_token
        if not tokens.refresh_token:
            raise AuthError("DataForSEO authorization expired; reconnect is required")
        refreshed = self._oauth.refresh(refresh_token=tokens.refresh_token, client_id=tokens.client_id)
        refreshed.refresh_token = refreshed.refresh_token or tokens.refresh_token
        refreshed.client_id = tokens.client_id
        refreshed.redirect_uri = tokens.redirect_uri
        self._store.save(refreshed)
        return refreshed.access_token

    def _expired(self, tokens: OAuthTokens) -> bool:
        return tokens.expires_at > 0 and tokens.expires_at <= self._clock() + self._leeway

    def begin_authorization(self) -> dict[str, Any]:
        self._discard_pending()
        server = self._server_factory()
        server.start()
        try:
            redirect_uri = f"http://127.0.0.1:{server.port}/callback"
            client_id = self._oauth.register_client([redirect_uri])
            verifier, challenge = pkce_pair()
            state = secrets.token_urlsafe(24)
            url = self._oauth.authorization_url(
                client_id=client_id, redirect_uri=redirect_uri, challenge=challenge, state=state
            )
        except Exception:
            server.stop()
            raise
        self._pending = PendingAuthorization(
            server=server,
            client_id=client_id,
            verifier=verifier,
            state=state,
            redirect_uri=redirect_uri,
            authorization_url=url,
            created_at=self._clock(),
        )
        opened = False
        try:
            opened = bool(self._opener(url))
        except Exception:
            opened = False
        return {
            "provider": "dataforseo_mcp",
            "status": "AWAITING_AUTHORIZATION",
            "authorization_url": url,
            "browser_opened": opened,
            "expires_in_seconds": int(self._pending_ttl),
        }

    def finish_authorization(self, timeout_seconds: float = 300.0) -> dict[str, Any]:
        pending = self._pending
        if pending is None:
            raise StateError("no DataForSEO authorization is in progress")
        if self._clock() - pending.created_at > self._pending_ttl:
            self._discard_pending()
            raise AuthError("the DataForSEO authorization request expired; start again")
        params = pending.server.wait(timeout_seconds)
        if params is None:
            return {
                "provider": "dataforseo_mcp",
                "status": "AWAITING_AUTHORIZATION",
                "authorization_url": pending.authorization_url,
                "message": "No authorization callback was received yet.",
            }
        self._discard_pending()
        error = params.get("error")
        if error:
            raise AuthError(f"DataForSEO authorization was not granted ({str(error)[:40]})")
        if params.get("state") != pending.state:
            raise AuthError("DataForSEO authorization state mismatch")
        code = params.get("code")
        if not code:
            raise AuthError("DataForSEO authorization did not return a code")
        tokens = self._oauth.exchange_code(
            code=code,
            verifier=pending.verifier,
            client_id=pending.client_id,
            redirect_uri=pending.redirect_uri,
        )
        tokens.client_id = pending.client_id
        tokens.redirect_uri = pending.redirect_uri
        self._store.save(tokens)
        return self.status()

    def disconnect(self) -> bool:
        self._discard_pending()
        return self._store.clear()

    def _discard_pending(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.server.stop()
