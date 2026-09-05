//! Installation-path checks shared by service startup and read-only package QA.
use serde_json::{json, Value};
use std::path::Path;

pub const SIDECAR_NAME: &str = "ALLIN1-SDK-Desktop-Sidecar.exe";
// Match the conservative Windows installer policy. The frozen Python bootloader
// cannot reliably open its own archive beyond MAX_PATH on default Windows.
pub const MAX_RUNTIME_PATH_UNITS: usize = 240;

pub fn validate(executable: &Path) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        let units: Vec<u16> = executable.as_os_str().encode_wide().collect();
        let prefix: Vec<u16> = r"\\?\".encode_utf16().collect();
        let length = units.len() - if units.starts_with(&prefix) { prefix.len() } else { 0 };
        if length > MAX_RUNTIME_PATH_UNITS {
            return Err(format!(
                "SDK installation path is too long for the packaged Python service ({length} UTF-16 units; maximum {MAX_RUNTIME_PATH_UNITS}). Move the entire SDK folder to a shorter local path and reopen it. Your projects do not need to move."
            ));
        }
    }
    if !executable.is_file() {
        return Err("Packaged SDK service is missing. Re-extract the complete portable ZIP or repair the SDK installation.".into());
    }
    Ok(())
}

pub fn inspect(root: &Path) -> Value {
    let result = validate(&root.join("sidecar").join(SIDECAR_NAME));
    json!({"schema_version": 1, "kind": "sdk_runtime_location_probe",
        "status": if result.is_ok() { "READY" } else { "BLOCKED" },
        "error": result.err(), "sidecar_process_started": false,
        "maximum_windows_runtime_path_units": MAX_RUNTIME_PATH_UNITS,
        "long_path_runtime_supported": false, "release_ready": false})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_service_reports_repair_without_starting_it() {
        let report = inspect(Path::new("Z:/nonexistent-sdk-location-fixture"));
        assert_eq!(report["status"], "BLOCKED");
        assert_eq!(report["sidecar_process_started"], false);
        assert_eq!(report["release_ready"], false);
        assert!(report["error"].as_str().unwrap().contains("Re-extract"));
    }

    #[cfg(windows)]
    #[test]
    fn runtime_limit_counts_utf16_and_handles_extended_prefixes() {
        // At the boundary validation reaches the existence check. Beyond it,
        // the user receives the location warning before any process can start.
        for prefix in [r"C:\", r"\\?\C:\"] {
            let at_limit = format!("{prefix}{}", "a".repeat(MAX_RUNTIME_PATH_UNITS - 3));
            assert!(validate(Path::new(&at_limit)).unwrap_err().contains("missing"));
            let too_long = format!("{at_limit}a");
            let error = validate(Path::new(&too_long)).unwrap_err();
            assert!(error.contains("path is too long") && error.contains("Move the entire SDK folder"));
        }
        let unicode = format!(r"C:\{}", "🚙".repeat(120));
        assert!(validate(Path::new(&unicode)).unwrap_err().contains("243 UTF-16 units"));
    }

    #[test]
    fn existing_executable_is_accepted_for_location_only() {
        assert!(validate(&std::env::current_exe().unwrap()).is_ok());
    }
}
