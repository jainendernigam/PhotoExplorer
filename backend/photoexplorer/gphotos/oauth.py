"""Google sign-in for a desktop app: OAuth 2.0 authorization code + PKCE with
a loopback redirect (http://127.0.0.1:<random port>), as Google recommends
for installed apps. The user signs in in their normal browser; this app
never sees the password.

Needs a Google Cloud "OAuth client ID" of type *Desktop app* (free), with the
Photos Picker API enabled. Only the read-only Picker scope is requested.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

from . import secret_store

SCOPE = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
LOGIN_TIMEOUT_S = 300

_DONE_PAGE = (b"<html><body style='font-family:sans-serif;padding:3em'>"
              b"<h2>Signed in to Google Photos</h2><p>You can close this tab and go back to Photo Explorer.</p>"
              b"</body></html>")


class GoogleError(Exception):
    pass


def http_json(url: str, data: dict | None = None, token: str | None = None, method: str | None = None,
              form: bool = False, timeout: int = 30) -> dict:
    body, headers = None, {}
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise GoogleError(f"Google returned {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise GoogleError(f"Could not reach Google: {e.reason}") from None
    return json.loads(raw) if raw.strip() else {}


class GoogleAuth:
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    REVOKE_URL = "https://oauth2.googleapis.com/revoke"

    def __init__(self, data_dir: Path, open_browser: Callable[[str], object] = webbrowser.open, **urls):
        self.dir = data_dir / "google"
        self.open_browser = open_browser
        for k, v in urls.items():          # tests point these at a fake server
            setattr(self, k, v)
        self._access: tuple[str, float] | None = None
        self._lock = threading.Lock()
        self.login_state = "idle"          # idle | waiting | error
        self.login_error: str | None = None
        self._server: HTTPServer | None = None

    # ------------------------------------------------------------ client id

    def client(self) -> dict | None:
        raw = secret_store.load(self.dir / "client.bin")
        return json.loads(raw) if raw else None

    def set_client(self, client_id: str, client_secret: str) -> None:
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id or not client_secret:
            raise GoogleError("Enter both the client ID and the client secret")
        secret_store.save(self.dir / "client.bin",
                          json.dumps({"client_id": client_id, "client_secret": client_secret}).encode())

    # ------------------------------------------------------------ status

    @property
    def connected(self) -> bool:
        return (self.dir / "token.bin").is_file()

    def status(self) -> dict:
        c = self.client()
        return {"configured": c is not None,
                "client_id": c["client_id"] if c else None,
                "connected": self.connected,
                "login_state": self.login_state,
                "login_error": self.login_error}

    # ------------------------------------------------------------ sign-in

    def start_login(self) -> str:
        c = self.client()
        if not c:
            raise GoogleError("Add your Google OAuth client ID first")
        self._stop_server()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        auth = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [""])[0] != state:
                    self.send_response(400); self.end_headers(); return
                if "error" in q:
                    auth.login_state, auth.login_error = "error", q["error"][0]
                else:
                    try:
                        auth._exchange(c, q.get("code", [""])[0], redirect, verifier)
                        auth.login_state, auth.login_error = "idle", None
                    except GoogleError as e:
                        auth.login_state, auth.login_error = "error", str(e)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(_DONE_PAGE if auth.login_state == "idle" else
                                 b"<html><body>Sign-in failed. Go back to Photo Explorer.</body></html>")
                threading.Thread(target=auth._stop_server, daemon=True).start()

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        redirect = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True, name="google-login").start()
        timer = threading.Timer(LOGIN_TIMEOUT_S, self._timeout)
        timer.daemon = True        # never keeps the engine alive on exit
        timer.start()
        self.login_state, self.login_error = "waiting", None
        url = self.AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": SCOPE, "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
        self.open_browser(url)
        return url

    def _timeout(self) -> None:
        if self.login_state == "waiting":
            self.login_state, self.login_error = "error", "Sign-in timed out"
            self._stop_server()

    def _stop_server(self) -> None:
        s, self._server = self._server, None
        if s:
            s.shutdown()
            s.server_close()

    def _exchange(self, c: dict, code: str, redirect: str, verifier: str) -> None:
        tok = http_json(self.TOKEN_URL, {
            "client_id": c["client_id"], "client_secret": c["client_secret"], "code": code,
            "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect}, form=True)
        if "refresh_token" not in tok:
            raise GoogleError("Google did not return a refresh token")
        secret_store.save(self.dir / "token.bin", json.dumps({"refresh_token": tok["refresh_token"]}).encode())
        with self._lock:
            self._access = (tok["access_token"], time.time() + float(tok.get("expires_in", 3600)))

    # ------------------------------------------------------------ tokens

    def access_token(self) -> str:
        with self._lock:
            if self._access and self._access[1] - time.time() > 60:
                return self._access[0]
            raw = secret_store.load(self.dir / "token.bin")
            c = self.client()
            if not raw or not c:
                raise GoogleError("Not signed in to Google Photos")
            tok = http_json(self.TOKEN_URL, {
                "client_id": c["client_id"], "client_secret": c["client_secret"],
                "refresh_token": json.loads(raw)["refresh_token"], "grant_type": "refresh_token"}, form=True)
            self._access = (tok["access_token"], time.time() + float(tok.get("expires_in", 3600)))
            return self._access[0]

    def disconnect(self) -> None:
        raw = secret_store.load(self.dir / "token.bin")
        if raw:
            try:
                http_json(self.REVOKE_URL, {"token": json.loads(raw)["refresh_token"]}, form=True)
            except GoogleError:
                pass       # already revoked or offline: forget it locally anyway
        (self.dir / "token.bin").unlink(missing_ok=True)
        with self._lock:
            self._access = None
