# Photo Explorer: handover

For anyone (or any Claude Code session) who builds, changes or releases Photo Explorer.
Last updated 2026-11-10, release 0.9.4.

## What it is
A privacy-first, portable Windows photo organiser. Everything runs on the PC; photos never leave it.

| Part | Where | Tech |
|---|---|---|
| Desktop shell | `app/src-tauri` | Tauri 2, Rust (`stable-x86_64-pc-windows-gnu`) |
| Screens | `app/src` | React, Vite, Tailwind 3, TypeScript |
| Engine | `backend/photoexplorer` | Python 3.12, FastAPI, SQLite (migrations `db/schema_v*.sql`) |
| Tests | `backend/tests` | pytest; made-up photos and face fingerprints only |
| Content packs | `content/` | themes, music (music files are built, not stored) |

## Repositories
- **One public repo:** `jainendernigam/PhotoExplorer`.
  - Branch `source`: the full source (this file included). Work on a new branch from it; merge back when released.
  - Default branch: the README.
  - Releases: the in-app updater reads `releases/latest/download/manifest.json`, so releases must stay public.
- The repo is public: never commit secrets, personal photos, face data or private email addresses (commits use `jainendernigam@users.noreply.github.com`).

## Set up a PC to build
1. **Tools:** Node.js 24 + npm; Rust via rustup with `stable-x86_64-pc-windows-gnu` as default; MinGW-w64 GCC (WinLibs, `winget install BrechtSanders.WinLibs.POSIX.MSVCRT`); Git.
2. `cd app` then `npm install` (Tauri CLI 2.x comes with it).
3. `powershell -ExecutionPolicy Bypass -File scripts\setup_runtime.ps1` — portable Python 3.12.10 in `runtime\python` with `backend\requirements.txt` (includes onnxruntime; ArcFace and CLIP need it).
4. `powershell -ExecutionPolicy Bypass -File scripts\get_ffmpeg.ps1` — ffmpeg in `bin\`.
5. `runtime\python\python.exe -m photoexplorer models --download` (from `backend\`) — YuNet + SFace face models in `models\`.

`runtime\`, `bin\`, `models\`, `release\`, `data\` and `node_modules\` are not in git (see `.gitignore`).

## Run while developing
- Engine: from `backend\`, `set PHOTOEXPLORER_AUTOSCAN=0` then `..\runtime\python\python.exe -m photoexplorer --data-dir <a test data folder> serve --port 8765 --token dev`
- Screens: `cd app` then `npm run dev` (port 1420). `.claude/launch.json` has this as "ui".
- Tests: from `backend\`, `..\runtime\python\python.exe -m pytest -q` (111 pass, 2 skipped that need real faces or the CLIP model). `npx tsc --noEmit` in `app\` for the screens.
- Any script that starts a scan needs an `if __name__ == "__main__":` guard (worker processes re-import it).

## Release steps
1. Bump the version in 4 files: `backend/photoexplorer/__init__.py`, `app/src-tauri/tauri.conf.json`, `app/package.json`, `app/src-tauri/Cargo.toml`.
2. `powershell -ExecutionPolicy Bypass -File scripts\build_app.ps1 -Release`
3. `powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -StageRoot release\_build` — zip with `PhotoExplorer/` holding the exe, WebView2Loader.dll, README.txt, backend, runtime, bin, models.
4. `runtime\python\python.exe -I scripts\make_release.py --notes "..."` — writes `release\github\` (zip, manifest.json, music-1, themes-1 packs).
5. `runtime\python\python.exe -I scripts\publish_github.py` — creates the GitHub release. The token comes from `git credential fill`; never print or save it.
6. Verify: download `releases/latest/download/manifest.json` and the zip; compare with `release\github\manifest.json` and the zip's sha256.

## Rules from the owner
- **Ask before** creating repositories, pushing, or publishing a release. Every release needs the owner's explicit yes.
- Never use the owner's personal photos or face data for tests; face fingerprints are biometric data. Use made-up test data.
- New third-party downloads, crates or npm packages need the owner's approval.
- Don't work around the antivirus (K7).
- Labels in sentence case; respect the light/dark "Look" themes.

## Decisions so far
- Faces: YuNet detector; SFace built in; optional ArcFace (InsightFace, non-commercial licence) downloaded inside the app, never shipped. Grouping tuned on the owner's real library (values and evidence in `faces/arcface.py` `CLUSTER`).
- New faces join named people without regrouping; doubtful ones wait under People › Review.
- A photo is matched by path, and moved/renamed files are relinked (`scanner/moves.py`), so names, albums and favourites survive folder renames.
- Automatic scan 60 s after start, then every 10 min (`PHOTOEXPLORER_AUTOSCAN=0` turns it off).
- Search by content: CLIP ViT-B/32 (MIT), optional in-app download to `<data>/models/clip`.
- Places: offline Natural Earth map (`places/world.json`, public domain); search matches place names from GPS.
- Google Photos is the benchmark for how things behave (navigation, selection, smooth movement), not for its look.
- Sections once visited stay open in the background (`KEEP` in `App.tsx`; hidden ones read `useActive()` from `lib/active.ts`), so back/forward keeps the place.
- Each photo's main colour (`media.color`, schema v14) shows while its thumbnail loads; older photos are filled in the background (`scanner/colors.py`).
- UI: one top bar beside Look in every section; Create ▾ holds slideshow, video, collage and organize; Timeline lives inside Library.

## Done (recent)
- 0.9.0: memories strip, Places, Things, search by content, favourites, archive, notes, optional better faces.
- 0.9.3: back/forward between sections, Shift range preview, month tick, Ctrl+A, Delete to archive, Undo; colour placeholders and fade-in, date bubble, pinned month, Ctrl+scroll tile size, smooth paging keys, fades, reduced motion.
- 0.9.4: search date suggestions with arrow keys, photo grows out of its thumbnail, right-click menu, hover heart and silent video preview, drag photos onto an album, helpful empty pages.
- 0.9.2: face grouping fix, moved folders, automatic scan, Review, music for memories, place search, Library filters, new top bar and Create menu, person page, collapsible map, viewer info panel, window fits the taskbar.

## Pending
- Check 0.9.2-0.9.4 on the owner's PC: Review answers, top bar, window at 150% scaling and with the taskbar on the left, the mouse back button in the packaged app, video hover preview, closing does not shrink back to the thumbnail yet, how long the colour fill takes on 25k photos.
- Tune CLIP thresholds (search, categories, pets) on a real library.
- Not done from the 0.9.2 spec: an automatic "⋯" overflow menu on narrow windows; hidden face groups are remembered per PC only; Review shows a thumbnail, not the full viewer.
- Ideas: a larger offline gazetteer (GeoNames) for exact towns; Windows folder-change notifications for instant pick-up; code signing.

## Next step
Owner tests 0.9.4 on their PC and reports what to adjust.
