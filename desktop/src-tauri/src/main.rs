#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if std::env::args().nth(1).as_deref() == Some("--verify-embedded-frontend") {
        match allin1_sdk_desktop_lib::inspect_embedded_frontend() {
            Ok(report) => println!("{report}"),
            Err(error) => { eprintln!("{error}"); std::process::exit(1); }
        }
        return;
    }
    // Read-only artifact inspection: no WebView, sidecar, user state, or game startup.
    if std::env::args().nth(1).as_deref() == Some("--build-identity") {
        println!("{}", allin1_sdk_desktop_lib::build_identity().unwrap_or(serde_json::Value::Null));
        return;
    }
    allin1_sdk_desktop_lib::run();
}
