"""Publishes release\\github\\ (from make_release.py) as a GitHub release.

    runtime\\python\\python.exe scripts\\publish_github.py [--repo jainendernigam/PhotoExplorer]

* Uses the GitHub sign-in already stored by Git Credential Manager (the same
  one `git push` uses); the token is never printed or saved.
* Creates the repository (public, README only - no source code) if missing.
* Creates release v<version> as the latest release and uploads every file in
  release\\github\\. Refuses if that release already exists.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "release" / "github"
API = "https://api.github.com"

README = """# Photo Explorer

A private, offline desktop app to organise, de-duplicate and enjoy large photo
libraries: face recognition, timelines by person and age, duplicates and
look-alikes, slideshows and collages. Everything runs on your own PC; photos
are never uploaded.

This repository holds the **releases** only. The app's *Update software* page
reads the newest release here to offer new versions, themes and music.

**Download:** see [Releases](../../releases/latest) - unzip
`PhotoExplorer-<version>-windows-portable.zip` anywhere and run
`PhotoExplorer.exe` (Windows 10/11, no installation or admin rights).

The music in the music packs is original, made for Photo Explorer.
"""


def token() -> str:
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, timeout=120)
    for line in out.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    sys.exit("No GitHub sign-in found in Git Credential Manager (sign in with a git push first).")


class GitHub:
    def __init__(self, tok: str):
        self.h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "PhotoExplorer-release"}

    def call(self, method: str, url: str, body=None, data=None, ctype=None):
        headers = dict(self.h)
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(url if url.startswith("http") else API + url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"message": raw[:300].decode(errors="replace")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="jainendernigam/PhotoExplorer")
    a = ap.parse_args()
    owner, name = a.repo.split("/")
    manifest = json.loads((FILES / "manifest.json").read_text(encoding="utf-8"))
    version = (manifest.get("app") or {}).get("version") or sys.exit("manifest.json has no app version")
    notes = manifest["app"].get("notes", "")

    gh = GitHub(token())
    st, me = gh.call("GET", "/user")
    if st != 200:
        sys.exit(f"GitHub sign-in did not work ({st}): {me.get('message')}")
    if me["login"].lower() != owner.lower():
        sys.exit(f"Signed in as {me['login']}, not {owner}")

    st, repo = gh.call("GET", f"/repos/{owner}/{name}")
    if st == 404:
        st, repo = gh.call("POST", "/user/repos", {"name": name, "private": False, "auto_init": True,
                           "description": "Photo Explorer: private, offline photo organiser - releases and updates",
                           "has_wiki": False, "has_projects": False})
        if st != 201:
            sys.exit(f"Could not create the repository ({st}): {repo.get('message')}")
        print(f"Created public repository {repo['html_url']}")
        st, cur = gh.call("GET", f"/repos/{owner}/{name}/contents/README.md")
        body = {"message": "Describe the releases", "content": base64.b64encode(README.encode()).decode()}
        if st == 200:
            body["sha"] = cur["sha"]
        gh.call("PUT", f"/repos/{owner}/{name}/contents/README.md", body)
    elif st != 200:
        sys.exit(f"Could not read the repository ({st}): {repo.get('message')}")

    tag = f"v{version}"
    st, _ = gh.call("GET", f"/repos/{owner}/{name}/releases/tags/{tag}")
    if st == 200:
        sys.exit(f"Release {tag} already exists; raise the version first.")
    st, rel = gh.call("POST", f"/repos/{owner}/{name}/releases",
                      {"tag_name": tag, "name": f"Photo Explorer {version}", "body": notes, "make_latest": "true"})
    if st != 201:
        sys.exit(f"Could not create the release ({st}): {rel.get('message')}")
    upload = rel["upload_url"].split("{")[0]
    for f in sorted(FILES.iterdir(), key=lambda p: p.name != "manifest.json"):
        ctype = "application/json" if f.suffix == ".json" else "application/zip"
        print(f"Uploading {f.name} ({f.stat().st_size / 1e6:.1f} MB)...", flush=True)
        st, asset = gh.call("POST", f"{upload}?name={f.name}", data=f.read_bytes(), ctype=ctype)
        if st != 201:
            sys.exit(f"Upload of {f.name} failed ({st}): {asset.get('message')}")
    print(f"Published {rel['html_url']}")


if __name__ == "__main__":
    main()
