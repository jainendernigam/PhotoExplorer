"""Prepares the files for one GitHub release (Update software reads them).

    runtime\\python\\python.exe scripts\\make_release.py [--notes "What's new"] [--no-app]

Output in release\\github\\ - upload ALL of these files to a new GitHub
release (the newest release is what every copy of the app checks):

    manifest.json                                  what is available + checksums
    PhotoExplorer-<version>-windows-portable.zip   the app (from scripts\\package.ps1)
    <pack id>.zip                                  one per folder in content\\

Packs: every content\\<id>\\pack.json folder becomes <id>.zip. Raise a pack's
"version" in its pack.json when you change it, so installed copies update.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from photoexplorer import __version__  # noqa: E402
from photoexplorer.updates import PACK_EXTS  # noqa: E402

OUT = ROOT / "release" / "github"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="")
    ap.add_argument("--no-app", action="store_true", help="packs only (no new app version in this release)")
    a = ap.parse_args()

    tauri = json.loads((ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    if tauri != __version__:
        sys.exit(f"Version mismatch: tauri.conf.json says {tauri}, backend says {__version__}. Make them equal first.")

    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    manifest: dict = {"format": 1, "packs": []}

    if not a.no_app:
        zip_name = f"PhotoExplorer-{__version__}-windows-portable.zip"
        src = ROOT / "release" / zip_name
        if not src.is_file():
            sys.exit(f"{src} not found: run scripts\\package.ps1 first (or pass --no-app).")
        shutil.copy2(src, OUT / zip_name)
        manifest["app"] = {"version": __version__, "url": zip_name, "sha256": sha256(OUT / zip_name),
                           "size": (OUT / zip_name).stat().st_size, "notes": a.notes}
        starter = ROOT / "release" / f"PhotoExplorer-{__version__}-windows-starter.zip"
        if starter.is_file():               # small first download; not in the manifest
            shutil.copy2(starter, OUT / starter.name)

    for d in sorted((ROOT / "content").iterdir()):
        meta_path = d / "pack.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("id") != d.name:
            sys.exit(f"{meta_path}: id must equal the folder name ({d.name})")
        z = OUT / f"{d.name}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in PACK_EXTS:
                    zf.write(f, f.name)
        manifest["packs"].append({
            "id": meta["id"], "kind": meta["kind"], "version": meta.get("version", 1), "title": meta.get("title", d.name),
            "description": meta.get("description", ""), "items": len(meta.get("items", [])),
            "url": z.name, "sha256": sha256(z), "size": z.stat().st_size})
        print(f"pack {meta['id']} v{meta.get('version', 1)}: {z.stat().st_size / 1e6:.1f} MB")

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Release files ready in {OUT} - upload all of them to a new GitHub release.")


if __name__ == "__main__":
    main()
