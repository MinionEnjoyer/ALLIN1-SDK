//! Read-only compiled-asset inspection. Never creates a window or starts a service.
use serde_json::{json, Value};

pub fn inspect(context: &tauri::Context<tauri::Wry>, build_id: &str, version: &str) -> Result<Value, String> {
    if tauri::is_dev() {
        return Err("Shell was compiled in development mode; embedded frontend is not qualified".into());
    }
    if build_id.len() != 32 || !build_id.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("Shell has no valid compiled candidate identity".into());
    }
    let index = context.assets().get(&"index.html".into()).ok_or("Embedded index.html is missing")?;
    let html = std::str::from_utf8(&index).map_err(|_| "Embedded index.html is not UTF-8")?;
    if !html.contains("id=\"root\"") {
        return Err("Embedded frontend has no React mount point".into());
    }
    let mut assets: Vec<Value> = context.assets().iter().map(|(name, bytes)| {
        json!({"path": name.trim_start_matches('/'), "bytes": bytes.len()})
    }).collect();
    assets.sort_by(|a, b| a["path"].as_str().cmp(&b["path"].as_str()));
    for extension in [".js", ".css"] {
        if !assets.iter().any(|asset| {
            let name = asset["path"].as_str().unwrap_or("");
            name.ends_with(extension) && asset["bytes"].as_u64().unwrap_or(0) > 0 && html.contains(name)
        }) {
            return Err(format!("Embedded index does not reference a bundled {extension} entry"));
        }
    }
    Ok(json!({"schema_version": 1, "kind": "embedded_frontend_probe", "status": "PASS",
        "production": true, "build_id": build_id, "version": version, "assets": assets,
        "runtime_location_probe_version": 1,
        "native_ui": "NOT TESTED", "release_ready": false}))
}
