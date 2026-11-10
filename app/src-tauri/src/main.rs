// Desktop shell: opens the window and runs the Python engine as a child
// process. The engine prints {"event":"ready","port":..,"token":..} on its
// first stdout line; the UI fetches that through the `backend_info` command.
// The engine watches our PID (--parent-pid), so it exits if we crash.
//
// "Open with Photo Explorer": a picture's path may come as the first argument.
// If the app is already running (data/instance.json names its engine), the
// path is handed to that engine and this copy exits; otherwise the UI picks it
// up with `startup_file`.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, RunEvent, State};

mod setup;

#[derive(Clone, Serialize, Deserialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

#[derive(Default)]
struct Backend {
    info: Mutex<Option<BackendInfo>>,
    error: Mutex<Option<String>>,
    child: Mutex<Option<Child>>,
    /// Starter download: the engine files still have to be fetched (see setup.rs).
    needs_setup: Mutex<bool>,
    setup: Mutex<setup::Status>,
    /// A picture passed on the command line ("Open with").
    startup_file: Mutex<Option<String>>,
}

#[tauri::command]
fn startup_file(state: State<'_, Backend>) -> Option<String> {
    state.startup_file.lock().unwrap().take()
}

#[derive(Serialize, Deserialize)]
struct Instance {
    pid: u32,
    port: u16,
    token: String,
}

fn instance_file() -> PathBuf {
    app_root().join("data").join("instance.json")
}

/// A picture path from the command line, if any.
fn file_arg() -> Option<String> {
    std::env::args().skip(1).find(|a| !a.starts_with('-') && Path::new(a).is_file())
}

/// Hands `path` to an already running copy. True if it took it.
fn hand_to_running(path: &str) -> bool {
    let Ok(text) = std::fs::read_to_string(instance_file()) else { return false };
    let Ok(inst) = serde_json::from_str::<Instance>(&text) else { return false };
    if inst.pid == std::process::id() {
        return false;
    }
    let url = format!("http://127.0.0.1:{}/api/opened", inst.port);
    ureq::post(&url)
        .timeout(std::time::Duration::from_secs(3))
        .set("X-PE-Token", &inst.token)
        .send_json(serde_json::json!({ "path": path }))
        .is_ok()
}

#[tauri::command]
fn backend_info(state: State<'_, Backend>) -> Result<BackendInfo, String> {
    if let Some(i) = state.info.lock().unwrap().clone() {
        return Ok(i);
    }
    if let Some(e) = state.error.lock().unwrap().clone() {
        return Err(e);
    }
    if *state.needs_setup.lock().unwrap() {
        return Err("setup".into());
    }
    Err("starting".into())
}

/// Starts the first-run download (starter zip); poll `setup_status`.
#[tauri::command]
fn setup_start(app: AppHandle, state: State<'_, Backend>) {
    {
        let st = state.setup.lock().unwrap();
        if st.state == "downloading" || st.state == "unpacking" {
            return;
        }
    }
    std::thread::spawn(move || {
        let state = app.state::<Backend>();
        match setup::run(&app_root(), &state.setup) {
            Ok(replaced) => {
                *state.needs_setup.lock().unwrap() = false;
                let mut s = state.setup.lock().unwrap();
                s.state = "done".into();
                s.restart = replaced;
                s.message = if replaced { "Restart to finish".into() } else { "Ready".into() };
                drop(s);
                if !replaced {
                    start_backend(&app);
                }
            }
            Err(e) => {
                let mut s = state.setup.lock().unwrap();
                s.state = "error".into();
                s.message = e;
            }
        }
    });
}

#[tauri::command]
fn setup_status(state: State<'_, Backend>) -> setup::Status {
    state.setup.lock().unwrap().clone()
}

/// Opens the (new) exe and closes this one.
#[tauri::command]
fn restart_app(app: AppHandle) {
    if let Ok(exe) = std::env::current_exe() {
        let _ = Command::new(exe).current_dir(app_root()).spawn();
    }
    app.exit(0);
}

/// Closes the app (used by "Restart to update"; the exit handler stops the engine).
#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

/// Folder holding backend/, runtime/ and data/: next to the exe when
/// installed, the repository root in debug builds.
fn app_root() -> PathBuf {
    if let Ok(p) = std::env::var("PHOTOEXPLORER_ROOT") {
        return PathBuf::from(p);
    }
    if cfg!(debug_assertions) {
        return Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    }
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn python_exe(root: &Path) -> PathBuf {
    if let Ok(p) = std::env::var("PE_PYTHON") {
        return PathBuf::from(p);
    }
    if cfg!(windows) {
        root.join("runtime").join("python").join("python.exe")
    } else {
        root.join("runtime").join("python").join("bin").join("python3")
    }
}

fn start_backend(app: &AppHandle) {
    let root = app_root();
    let python = python_exe(&root);
    let state = app.state::<Backend>();
    if setup::needed(&root, &python) {
        *state.needs_setup.lock().unwrap() = true;
        return;
    }

    let mut cmd = Command::new(&python);
    cmd.args(["-m", "photoexplorer", "serve", "--parent-pid"])
        .arg(std::process::id().to_string())
        .env("PYTHONPATH", root.join("backend"))
        .env("PHOTOEXPLORER_ROOT", &root)
        .env("PYTHONUNBUFFERED", "1")
        .env("PHOTOEXPLORER_EXE", std::env::current_exe().unwrap_or_default())
        .current_dir(&root)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            *state.error.lock().unwrap() =
                Some(format!("Could not start engine {}: {e}", python.display()));
            return;
        }
    };
    let stdout = child.stdout.take().expect("piped stdout");
    *state.child.lock().unwrap() = Some(child);

    let handle = app.clone();
    std::thread::spawn(move || {
        let state = handle.state::<Backend>();
        let mut lines = BufReader::new(stdout).lines();
        match lines.next() {
            Some(Ok(line)) => match serde_json::from_str::<BackendInfo>(&line) {
                Ok(info) => {
                    let inst = Instance { pid: std::process::id(), port: info.port, token: info.token.clone() };
                    if let Ok(t) = serde_json::to_string(&inst) {
                        let _ = std::fs::write(instance_file(), t);
                    }
                    *state.info.lock().unwrap() = Some(info)
                }
                Err(_) => *state.error.lock().unwrap() = Some(format!("Unexpected engine output: {line}")),
            },
            _ => *state.error.lock().unwrap() = Some("Engine exited during start-up".into()),
        }
        for _ in lines {} // keep draining so the engine never blocks on stdout
    });
}

/// Portable: in the installed/unzipped app, keep WebView2's own cache in
/// <app>/data/webview instead of %LOCALAPPDATA% (when that folder is writable).
fn keep_webview_data_portable() {
    if cfg!(debug_assertions) || std::env::var_os("WEBVIEW2_USER_DATA_FOLDER").is_some() {
        return;
    }
    let dir = app_root().join("data").join("webview");
    if std::fs::create_dir_all(&dir).is_ok() {
        std::env::set_var("WEBVIEW2_USER_DATA_FOLDER", &dir);
    }
}

fn main() {
    let opened = file_arg();
    if let Some(p) = &opened {
        if hand_to_running(p) {
            return; // the running copy shows it
        }
    }
    setup::clean_old_files(&app_root());
    keep_webview_data_portable();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend { startup_file: Mutex::new(opened), ..Default::default() })
        .invoke_handler(tauri::generate_handler![backend_info, quit_app, setup_start, setup_status, restart_app,
                                                 startup_file])
        .setup(|app| {
            start_backend(app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Photo Explorer")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut c) = app.state::<Backend>().child.lock().unwrap().take() {
                    let _ = c.kill();
                }
                // forget our instance record (only if it is still ours)
                if let Ok(t) = std::fs::read_to_string(instance_file()) {
                    if serde_json::from_str::<Instance>(&t).map(|i| i.pid == std::process::id()).unwrap_or(false) {
                        let _ = std::fs::remove_file(instance_file());
                    }
                }
            }
        });
}
