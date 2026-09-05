# Packaged desktop sidecar

`scripts/build_tauri_desktop.ps1` writes the PyInstaller executable
`ALLIN1-SDK-Desktop-Sidecar.exe` here before invoking `tauri build`.

The Rust broker resolves only that fixed resource name in release builds. This
directory intentionally contains no development executable in source control.
