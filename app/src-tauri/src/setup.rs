//! First-run setup for the small "starter" download (just PhotoExplorer.exe):
//! fetch the newest full app zip from the project's GitHub release, check its
//! SHA-256 against manifest.json, and unpack the engine, Python, FFmpeg and
//! face models next to the exe. Later updates go through Update software.
use std::fs;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use sha2::{Digest, Sha256};

pub const MANIFEST_URL: &str =
    "https://github.com/jainendernigam/PhotoExplorer/releases/latest/download/manifest.json";

/// PE_UPDATE_URL (also read by the engine) points setup at a test server.
fn manifest_url() -> String {
    std::env::var("PE_UPDATE_URL").unwrap_or_else(|_| MANIFEST_URL.to_string())
}

#[derive(Clone, Serialize, Default)]
pub struct Status {
    pub state: String, // idle | downloading | unpacking | done | error
    pub message: String,
    pub bytes: u64,
    pub total: u64,
    /// The download had a different PhotoExplorer.exe: restart to use it.
    pub restart: bool,
}

/// True when this folder has only the exe (the starter download).
pub fn needed(root: &Path, python: &Path) -> bool {
    !python.exists() || !root.join("backend").join("photoexplorer").join("__init__.py").exists()
}

fn old_name(p: &Path) -> PathBuf {
    let mut s = p.as_os_str().to_owned();
    s.push(".old");
    PathBuf::from(s)
}

/// Leftovers from a setup that replaced files that were in use.
pub fn clean_old_files(root: &Path) {
    for name in ["PhotoExplorer.exe", "WebView2Loader.dll"] {
        let _ = fs::remove_file(old_name(&root.join(name)));
    }
}

fn agent() -> Result<ureq::Agent, String> {
    let tls = native_tls::TlsConnector::new().map_err(|e| format!("Secure connection not available: {e}"))?;
    let mut b = ureq::AgentBuilder::new()
        .tls_connector(Arc::new(tls)) // Windows' own certificates, so company networks work
        .timeout_connect(Duration::from_secs(30))
        .timeout_read(Duration::from_secs(120))
        .user_agent(concat!("PhotoExplorer/", env!("CARGO_PKG_VERSION")));
    if let Some(p) = system_proxy() {
        if let Ok(proxy) = ureq::Proxy::new(p) {
            b = b.proxy(proxy);
        }
    }
    Ok(b.build())
}

/// The proxy set in Windows' Internet settings (or HTTPS_PROXY), if any.
fn system_proxy() -> Option<String> {
    if let Ok(p) = std::env::var("HTTPS_PROXY").or_else(|_| std::env::var("https_proxy")) {
        return Some(p);
    }
    #[cfg(windows)]
    {
        use winreg::enums::HKEY_CURRENT_USER;
        let key = winreg::RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey(r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            .ok()?;
        let enabled: u32 = key.get_value("ProxyEnable").unwrap_or(0);
        let server: String = key.get_value("ProxyServer").ok()?;
        if enabled == 0 || server.is_empty() {
            return None;
        }
        // "host:port" or "http=host:port;https=host:port"
        let pick = server
            .split(';')
            .find_map(|part| part.strip_prefix("https="))
            .or_else(|| server.split(';').find_map(|part| part.strip_prefix("http=")))
            .unwrap_or(&server);
        return Some(format!("http://{pick}"));
    }
    #[allow(unreachable_code)]
    None
}

fn set(status: &Mutex<Status>, f: impl FnOnce(&mut Status)) {
    f(&mut status.lock().unwrap());
}

/// Downloads and unpacks the app. Returns true if PhotoExplorer.exe itself was replaced.
pub fn run(root: &Path, status: &Mutex<Status>) -> Result<bool, String> {
    set(status, |s| *s = Status { state: "downloading".into(), message: "Looking for the newest version".into(), ..Default::default() });
    let work = root.join("data").join("setup");
    fs::create_dir_all(&work).map_err(|_| {
        format!("Can't write in {}. Unzip Photo Explorer into a folder of yours (Documents, Desktop or another drive), not Program Files.", root.display())
    })?;
    let agent = agent()?;
    let manifest_url = manifest_url();
    let manifest: serde_json::Value = agent
        .get(&manifest_url)
        .call()
        .map_err(|e| format!("Could not reach GitHub: {e}"))?
        .into_json()
        .map_err(|e| format!("Unexpected answer from GitHub: {e}"))?;
    let app = &manifest["app"];
    let (url, want, size, version) = (
        app["url"].as_str().unwrap_or_default(),
        app["sha256"].as_str().unwrap_or_default().to_lowercase(),
        app["size"].as_u64().unwrap_or(0),
        app["version"].as_str().unwrap_or("?").to_string(),
    );
    if url.is_empty() || want.len() != 64 {
        return Err("The release on GitHub has no app download.".into());
    }
    let url = if url.starts_with("https://") {
        url.to_string()
    } else {
        format!("{}/{}", manifest_url.rsplit_once('/').map(|(base, _)| base).unwrap_or(""), url)
    };

    // ---- download, checking the SHA-256 on the way
    let zip_path = work.join("app.zip");
    let part = work.join("app.zip.part");
    set(status, |s| { s.message = format!("Downloading Photo Explorer {version}"); s.total = size; });
    let resp = agent.get(&url).call().map_err(|e| format!("Download failed: {e}"))?;
    let mut reader = resp.into_reader();
    let mut out = fs::File::create(&part).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut got = 0u64;
    loop {
        let n = reader.read(&mut buf).map_err(|e| format!("Download interrupted: {e}"))?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        out.write_all(&buf[..n]).map_err(|e| e.to_string())?;
        got += n as u64;
        set(status, |s| s.bytes = got);
    }
    drop(out);
    let hex: String = hasher.finalize().iter().map(|b| format!("{b:02x}")).collect();
    if hex != want {
        let _ = fs::remove_file(&part);
        return Err("The download is damaged (checksum does not match). Please try again.".into());
    }
    fs::rename(&part, &zip_path).map_err(|e| e.to_string())?;

    // ---- unpack next to the exe (never into data/)
    set(status, |s| { s.state = "unpacking".into(); s.message = "Unpacking".into(); });
    let result = unpack(&zip_path, root);
    let _ = fs::remove_file(&zip_path);
    let _ = fs::remove_dir(&work);
    result
}

fn unpack(zip_path: &Path, root: &Path) -> Result<bool, String> {
    let file = fs::File::open(zip_path).map_err(|e| e.to_string())?;
    let mut zip = zip::ZipArchive::new(file).map_err(|e| format!("The download is not a valid zip: {e}"))?;
    let me = std::env::current_exe().map_err(|e| e.to_string())?;
    let my_crc = fs::read(&me).map(|b| crc32fast::hash(&b)).unwrap_or(0);
    let mut replaced = false;
    for i in 0..zip.len() {
        let mut entry = zip.by_index(i).map_err(|e| e.to_string())?;
        let name = entry.name().replace('\\', "/");
        let Some(rel) = name.strip_prefix("PhotoExplorer/") else { return Err(format!("Unexpected file in download: {name}")) };
        let rel_path = PathBuf::from(rel);
        if rel.is_empty() || rel.starts_with("data/") || entry.is_dir() {
            continue;
        }
        if rel_path.components().any(|c| !matches!(c, Component::Normal(_))) {
            return Err(format!("Unsafe path in download: {name}"));
        }
        let target = root.join(&rel_path);
        if rel == "PhotoExplorer.exe" {
            if entry.crc32() == my_crc {
                continue; // same exe as the one running
            }
            replaced = true;
        } else if target.is_file() && fs::read(&target).map(|b| crc32fast::hash(&b)).ok() == Some(entry.crc32()) {
            continue; // already there (e.g. WebView2Loader.dll, which the running app has loaded)
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let mut out = match fs::File::create(&target) {
            Ok(f) => f,
            Err(_) if target.is_file() => {
                // A file in use (the running exe or a loaded DLL) can't be overwritten,
                // but it can be renamed out of the way; clean_old_files removes it later.
                fs::rename(&target, old_name(&target)).map_err(|e| format!("Could not replace {}: {e}", target.display()))?;
                fs::File::create(&target).map_err(|e| format!("Could not write {}: {e}", target.display()))?
            }
            Err(e) => return Err(format!("Could not write {}: {e}", target.display())),
        };
        std::io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
    }
    Ok(replaced)
}
