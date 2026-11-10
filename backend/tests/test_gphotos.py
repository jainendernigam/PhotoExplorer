"""Google Photos sign-in and Picker import against a local fake of Google's
endpoints (no network, no real account)."""
from __future__ import annotations

import io
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from photoexplorer.config import Settings
from photoexplorer.db import Database
from photoexplorer.gphotos import secret_store
from photoexplorer.gphotos.importer import Importer
from photoexplorer.gphotos.oauth import GoogleAuth, GoogleError
from photoexplorer.scanner.manager import ScanManager


def _jpeg(color, exif_date=None):
    b = io.BytesIO()
    exif = Image.Exif()
    if exif_date:
        exif.get_ifd(0x8769)[0x9003] = exif_date
    Image.new("RGB", (320, 240), color).save(b, "JPEG", exif=exif)
    return b.getvalue()


class FakeGoogle:
    def __init__(self):
        self.polls = 0
        self.revoked = []
        self.deleted_sessions = []
        self.photos = {"m1": _jpeg((200, 30, 30), "2016:05:01 09:00:00"), "m2": _jpeg((30, 200, 30))}
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authed(self):
                return self.headers.get("Authorization") == "Bearer AT-1"

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                form = dict(urllib.parse.parse_qsl(body))
                if self.path == "/token":
                    if form.get("grant_type") == "authorization_code" and form.get("code") == "CODE" \
                            and form.get("code_verifier") and form.get("client_secret") == "sec":
                        return self._send(200, {"access_token": "AT-1", "expires_in": 3600, "refresh_token": "RT-1"})
                    if form.get("grant_type") == "refresh_token" and form.get("refresh_token") == "RT-1":
                        return self._send(200, {"access_token": "AT-1", "expires_in": 3600})
                    return self._send(400, {"error": "invalid_grant"})
                if self.path == "/revoke":
                    fake.revoked.append(form.get("token"))
                    return self._send(200, {})
                if self.path == "/v1/sessions" and self._authed():
                    return self._send(200, {"id": "S1", "pickerUri": "https://picker.example/S1",
                                            "pollingConfig": {"pollInterval": "0.1s", "timeoutIn": "30s"}})
                self._send(401, {"error": "unauthenticated"})

            def do_GET(self):
                if not self._authed():
                    return self._send(401, {"error": "unauthenticated"})
                if self.path == "/v1/sessions/S1":
                    fake.polls += 1
                    return self._send(200, {"id": "S1", "mediaItemsSet": fake.polls >= 3})
                if self.path.startswith("/v1/mediaItems?sessionId=S1"):
                    base = f"http://127.0.0.1:{fake.port}/media"
                    return self._send(200, {"mediaItems": [
                        {"id": "m1", "createTime": "2016-05-01T08:00:00Z", "type": "PHOTO",
                         "mediaFile": {"baseUrl": f"{base}/m1", "mimeType": "image/jpeg", "filename": "IMG_0001.jpg"}},
                        {"id": "m2", "createTime": "2019-12-24T18:30:00Z", "type": "PHOTO",
                         "mediaFile": {"baseUrl": f"{base}/m2", "mimeType": "image/jpeg", "filename": "IMG_0001.jpg"}},
                    ]})
                if self.path.startswith("/media/") and self.path.endswith("=d"):
                    return self._send(200, fake.photos[self.path[7:-2]], "image/jpeg")
                self._send(404, {})

            def do_DELETE(self):
                fake.deleted_sessions.append(self.path)
                self._send(200, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


def _browser_consents(url: str):
    """Plays the user: Google redirects back to our loopback with a code."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["scope"][0].endswith("photospicker.mediaitems.readonly")
    redirect = q["redirect_uri"][0]
    assert redirect.startswith("http://127.0.0.1:")
    threading.Thread(target=lambda: urllib.request.urlopen(
        f"{redirect}/?code=CODE&state={q['state'][0]}").read(), daemon=True).start()


@pytest.fixture
def setup(tmp_path):
    g = FakeGoogle()
    s = Settings.load(tmp_path / "data", workers=1, faces_enabled=False)
    db = Database(s.db_path)
    auth = GoogleAuth(s.data_dir, open_browser=_browser_consents,
                      AUTH_URL=f"{g.base}/auth", TOKEN_URL=f"{g.base}/token", REVOKE_URL=f"{g.base}/revoke")
    yield g, s, db, auth, tmp_path
    g.server.shutdown()


def _wait(pred, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_sign_in_store_refresh_and_disconnect(setup):
    g, s, db, auth, _ = setup
    with pytest.raises(GoogleError):
        auth.start_login()                                   # no client id yet
    auth.set_client("123-abc.apps.googleusercontent.com", "sec")
    auth.start_login()
    assert _wait(lambda: auth.connected and auth.login_state == "idle"), auth.status()
    raw = (s.data_dir / "google" / "token.bin").read_bytes()
    assert b"RT-1" not in raw                                # encrypted at rest (DPAPI on Windows)
    assert json.loads(secret_store.load(s.data_dir / "google" / "token.bin"))["refresh_token"] == "RT-1"
    auth._access = None
    assert auth.access_token() == "AT-1"                     # refresh flow
    auth.disconnect()
    assert not auth.connected and g.revoked == ["RT-1"]


def test_picker_import_downloads_scans_and_tags(setup):
    g, s, db, auth, tmp = setup
    auth.set_client("123-abc.apps.googleusercontent.com", "sec")
    auth.start_login()
    assert _wait(lambda: auth.connected)
    opened = []
    imp = Importer(db, s, auth, ScanManager(db, s), open_browser=opened.append, api=f"{g.base}/v1")
    dest = tmp / "Google import"
    imp.start(str(dest))
    assert opened == ["https://picker.example/S1/autoclose"]
    assert _wait(lambda: imp.state["state"] in ("done", "error", "cancelled"), 60), imp.state
    assert imp.state["state"] == "done", imp.state
    assert imp.state["downloaded"] == 2 and g.polls >= 3 and g.deleted_sessions == ["/v1/sessions/S1"]
    # Organised by Google's date; same file name twice does not overwrite.
    assert (dest / "2016" / "05" / "IMG_0001.jpg").is_file() and (dest / "2019" / "12" / "IMG_0001.jpg").is_file()
    rows = db.conn.execute("SELECT filename, origin, remote_id, date_source FROM media ORDER BY remote_id").fetchall()
    assert [(r["origin"], r["remote_id"]) for r in rows] == [("gphotos", "m1"), ("gphotos", "m2")]
    assert rows[0]["date_source"] == "exif" and rows[1]["date_source"] == "mtime"   # Google's date kept as file date
    assert db.conn.execute("SELECT label FROM sources").fetchone()[0] == "Google Photos"

    # Picking the same photos again downloads nothing.
    g.polls = 0
    imp.start(str(dest))
    assert _wait(lambda: imp.state["state"] in ("done", "error"), 60) and imp.state["state"] == "done", imp.state
    assert imp.state["downloaded"] == 0 and imp.state["skipped"] == 2
