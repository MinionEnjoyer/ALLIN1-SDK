use crate::protocol::{Envelope, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, SyncSender};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;
use tauri::ipc::Channel;
use tauri::{AppHandle, Emitter, Manager};

fn locked<T>(mutex: &Mutex<T>) -> Result<MutexGuard<'_, T>, String> {
    mutex
        .lock()
        .map_err(|_| "desktop broker lock was poisoned".to_string())
}

fn reject_unadmitted_child(child: &mut Child, message: &str) -> String {
    // This child has not been published to a Broker and has received no
    // request, so cleanup cannot destroy an uncertain user operation.
    let _ = child.kill();
    let _ = child.wait();
    message.into()
}

fn read_bounded_frame(reader: &mut impl BufRead) -> Result<Option<String>, String> {
    let mut bytes = Vec::new();
    loop {
        let (count, complete) = {
            let available = reader
                .fill_buf()
                .map_err(|error| format!("sidecar stdout failed: {error}"))?;
            if available.is_empty() {
                if bytes.is_empty() {
                    return Ok(None);
                }
                return Err("sidecar stdout ended in an unterminated protocol frame".into());
            }
            let count = available
                .iter()
                .position(|byte| *byte == b'\n')
                .map(|index| index + 1)
                .unwrap_or(available.len());
            if bytes.len().saturating_add(count) > MAX_RESPONSE_BYTES {
                return Err(format!(
                    "sidecar stdout frame exceeds the {} MiB protocol limit",
                    MAX_RESPONSE_BYTES / 1024 / 1024
                ));
            }
            bytes.extend_from_slice(&available[..count]);
            (count, bytes.last() == Some(&b'\n'))
        };
        reader.consume(count);
        if complete {
            bytes.pop();
            if bytes.last() == Some(&b'\r') {
                bytes.pop();
            }
            return String::from_utf8(bytes)
                .map(Some)
                .map_err(|error| format!("sidecar stdout is not UTF-8: {error}"));
        }
    }
}

fn drain_to_eof(reader: &mut impl Read) {
    let mut block = [0; 4096];
    while matches!(reader.read(&mut block), Ok(count) if count != 0) {}
}

fn retire_before_replacement(broker: &Broker, request_id: String) -> Result<(), String> {
    // `alive == false` can mean the protocol reader disconnected while the
    // child still performs a write. `try_shutdown` makes the child status the
    // authority and refuses to kill that uncertain writer.
    if !broker.is_alive() {
        broker.try_shutdown(request_id)?;
    }
    Ok(())
}

fn validate_build_peer(payload: &Value, expected: &Value, version: &str) -> Result<(), String> {
    if payload.get("build_identity") != Some(expected)
        || payload.get("sdk_version").and_then(Value::as_str) != Some(version)
    {
        return Err("SDK shell and sidecar belong to different builds. Repair this installation.".into());
    }
    Ok(())
}

fn repo_root() -> Result<PathBuf, String> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .ok_or_else(|| "could not resolve the SDK repository root".to_string())
}

fn sidecar_command(app: &AppHandle) -> Result<Command, String> {
    if cfg!(debug_assertions) {
        if let Some(executable) = env::var_os("ALLIN1_DESKTOP_SIDECAR") {
            let path = PathBuf::from(executable)
                .canonicalize()
                .map_err(|error| format!("ALLIN1_DESKTOP_SIDECAR is invalid: {error}"))?;
            if !path.is_file() {
                return Err("ALLIN1_DESKTOP_SIDECAR is not a file".to_string());
            }
            return Ok(Command::new(path));
        }
        let python = env::var_os("ALLIN1_DESKTOP_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("python"));
        let mut command = Command::new(python);
        command.args(["-m", "allin1_sdk.desktop_sidecar_host"]);
        let root = repo_root()?;
        command.current_dir(&root);
        let mut python_paths = vec![root.join("src")];
        if let Some(current) = env::var_os("PYTHONPATH") {
            python_paths.extend(env::split_paths(&current));
        }
        let joined = env::join_paths(python_paths)
            .map_err(|error| format!("could not construct PYTHONPATH: {error}"))?;
        command.env("PYTHONPATH", joined);
        return Ok(command);
    }

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("could not resolve Tauri resources: {error}"))?;
    crate::runtime_location::validate(&resource_dir.join("sidecar").join(crate::runtime_location::SIDECAR_NAME))?;
    let sidecar_root = resource_dir
        .join("sidecar")
        .canonicalize()
        .map_err(|error| format!("packaged sidecar directory is missing: {error}"))?;
    let executable = sidecar_root
        .join("ALLIN1-SDK-Desktop-Sidecar.exe")
        .canonicalize()
        .map_err(|error| format!("packaged ALLIN1 sidecar is missing: {error}"))?;
    if executable.parent() != Some(sidecar_root.as_path()) {
        return Err("packaged sidecar escaped its resource directory".to_string());
    }
    crate::runtime_location::validate(&executable)?;
    Ok(Command::new(executable))
}

pub struct Broker {
    stdin: Mutex<ChildStdin>,
    child: Mutex<Child>,
    pending: Arc<Mutex<HashMap<String, SyncSender<Envelope>>>>,
    jobs: Arc<Mutex<HashMap<String, Channel<Envelope>>>>,
    alive: Arc<AtomicBool>,
    accepting: AtomicBool,
}

impl Broker {
    pub fn spawn(app: AppHandle) -> Result<Arc<Self>, String> {
        let resource_dir = if cfg!(debug_assertions) {
            repo_root()?
        } else {
            app.path()
                .resource_dir()
                .map_err(|error| format!("could not resolve resource directory: {error}"))?
        };
        let preview_dir = app
            .path()
            .app_cache_dir()
            .map_err(|error| format!("could not resolve preview cache directory: {error}"))?
            .join("allin1-previews");
        std::fs::create_dir_all(&preview_dir)
            .map_err(|error| format!("could not create preview cache directory: {error}"))?;
        let preview_dir = preview_dir
            .canonicalize()
            .map_err(|error| format!("could not resolve preview cache directory: {error}"))?;
        let mut command = sidecar_command(&app)?;
        command
            // The native shell owns this fixed capability. WebView requests still
            // need a current digest and action-time confirmation before any write.
            .arg("--allow-package-writes")
            .arg("--allow-rpf-writes")
            .env("ALLIN1_SDK_HOME", &resource_dir)
            .env("ALLIN1_PREVIEW_DIR", &preview_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(windows_sys::Win32::System::Threading::CREATE_NO_WINDOW);
        }
        let mut child = command
            .spawn()
            .map_err(|error| format!("failed to start the ALLIN1 sidecar: {error}"))?;
        let stdin = match child.stdin.take() {
            Some(stdin) => stdin,
            None => return Err(reject_unadmitted_child(&mut child, "sidecar stdin was not piped")),
        };
        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => return Err(reject_unadmitted_child(&mut child, "sidecar stdout was not piped")),
        };
        let stderr = match child.stderr.take() {
            Some(stderr) => stderr,
            None => return Err(reject_unadmitted_child(&mut child, "sidecar stderr was not piped")),
        };
        let pending = Arc::new(Mutex::new(HashMap::<String, SyncSender<Envelope>>::new()));
        let jobs = Arc::new(Mutex::new(HashMap::<String, Channel<Envelope>>::new()));
        let alive = Arc::new(AtomicBool::new(true));

        let broker = Arc::new(Self {
            stdin: Mutex::new(stdin),
            child: Mutex::new(child),
            pending: pending.clone(),
            jobs: jobs.clone(),
            alive: alive.clone(),
            accepting: AtomicBool::new(true),
        });

        let status_app = app.clone();
        let stdout_reader = std::thread::Builder::new()
            .name("allin1-sidecar-stdout".to_string())
            .spawn(move || {
                let mut reader = BufReader::new(stdout);
                let mut failure = "ALLIN1 SDK sidecar exited".to_string();
                loop {
                    let line = match read_bounded_frame(&mut reader) {
                        Ok(Some(value)) => value,
                        Ok(None) => break,
                        Err(error) => { failure = error; break; }
                    };
                    let message = match serde_json::from_str::<Envelope>(&line) {
                        Ok(value) => value,
                        Err(error) => {
                            failure = format!("ALLIN1 SDK sidecar protocol corruption: {error}");
                            break;
                        }
                    };
                    if let Err(error) = message.validate_response() {
                        failure = error;
                        break;
                    }
                    if let Some(request_id) = message.request_id.as_ref() {
                        if let Ok(mut waiting) = pending.lock() {
                            if let Some(sender) = waiting.remove(request_id) {
                                let _ = sender.send(message.clone());
                            }
                        }
                    }
                    if let Some(job_id) = message.job_id.as_ref() {
                        if let Ok(mut channels) = jobs.lock() {
                            if let Some(channel) = channels.get(job_id) {
                                let _ = channel.send(message.clone());
                            }
                            if message.terminal {
                                channels.remove(job_id);
                            }
                        }
                    }
                }
                alive.store(false, Ordering::Release);
                if let Ok(mut waiting) = pending.lock() {
                    for (request_id, sender) in waiting.drain() {
                        let _ = sender.send(Envelope::local_error(Some(request_id), &failure));
                    }
                }
                if let Ok(mut channels) = jobs.lock() {
                    for (job_id, channel) in channels.drain() {
                        let mut message = Envelope::local_error(None, &failure);
                        message.job_id = Some(job_id);
                        let _ = channel.send(message);
                    }
                }
                let _ = status_app.emit("sidecar-status", format!("Sidecar crash: {failure}"));
                // Once protocol trust is lost the process may still be
                // writing. Keep draining raw stdout after marking it
                // unavailable, so it cannot deadlock on a full pipe while the
                // broker preserves the uncertain outcome.
                drain_to_eof(&mut reader);
            });
        if let Err(error) = stdout_reader {
            broker.terminate();
            return Err(format!("failed to start sidecar reader: {error}"));
        }

        let stderr_reader = std::thread::Builder::new()
            .name("allin1-sidecar-stderr".to_string())
            .spawn(move || {
                let mut reader = BufReader::new(stderr);
                let mut block = [0; 4096];
                while let Ok(count) = reader.read(&mut block) {
                    if count == 0 { break; }
                    eprintln!("[ALLIN1 sidecar] {}", String::from_utf8_lossy(&block[..count]));
                }
            });
        if let Err(error) = stderr_reader {
            broker.terminate();
            return Err(format!("failed to start sidecar diagnostics: {error}"));
        }

        Ok(broker)
    }

    pub fn is_alive(&self) -> bool {
        if !self.alive.load(Ordering::Acquire) {
            return false;
        }
        match locked(&self.child).and_then(|mut child| {
            child
                .try_wait()
                .map_err(|error| format!("failed to inspect sidecar process: {error}"))
        }) {
            Ok(None) => true,
            Ok(Some(_)) | Err(_) => {
                self.alive.store(false, Ordering::Release);
                false
            }
        }
    }

    pub fn register_job(&self, job_id: String, channel: Channel<Envelope>) -> Result<(), String> {
        let _pending = locked(&self.pending)?;
        if !self.accepting.load(Ordering::Acquire) {
            return Err("SDK service is closing".into());
        }
        let mut jobs = locked(&self.jobs)?;
        if jobs.contains_key(&job_id) {
            return Err(format!("job channel is already registered: {job_id}"));
        }
        jobs.insert(job_id, channel);
        Ok(())
    }

    pub fn unregister_job(&self, job_id: &str) {
        if let Ok(mut jobs) = self.jobs.lock() {
            jobs.remove(job_id);
        }
    }

    pub fn request(&self, request: Envelope, timeout: Duration) -> Result<Envelope, String> {
        if !self.is_alive() {
            return Err("ALLIN1 SDK sidecar is not running".to_string());
        }
        let request_id = request
            .request_id
            .clone()
            .ok_or("broker requests require a request id")?;
        let mut encoded = serde_json::to_vec(&request)
            .map_err(|error| format!("could not encode sidecar request: {error}"))?;
        if encoded.len() > MAX_REQUEST_BYTES {
            return Err("sidecar request exceeds the 256 KiB limit".to_string());
        }
        encoded.push(b'\n');
        let (sender, receiver) = mpsc::sync_channel(1);
        {
            let mut pending = locked(&self.pending)?;
            if !self.accepting.load(Ordering::Acquire) && request.operation != "shutdown" {
                return Err("SDK service is closing".into());
            }
            pending.insert(request_id.clone(), sender);
        }
        let write_result = locked(&self.stdin).and_then(|mut stdin| {
            stdin
                .write_all(&encoded)
                .and_then(|_| stdin.flush())
                .map_err(|error| format!("failed to write to sidecar: {error}"))
        });
        if let Err(error) = write_result {
            if let Ok(mut pending) = self.pending.lock() {
                pending.remove(&request_id);
            }
            return Err(error);
        }
        match receiver.recv_timeout(timeout) {
            Ok(message) => Ok(message),
            Err(error) => {
                // A client timeout does not cancel Python. Retain the request
                // until its response/EOF so close or restart cannot kill a write.
                Err(format!(
                    "sidecar request {request_id} did not complete: {error}"
                ))
            }
        }
    }

    pub fn terminate(&self) {
        self.alive.store(false, Ordering::Release);
        if let Ok(mut child) = self.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    pub fn try_shutdown(&self, request_id: String) -> Result<(), String> {
        if !self.is_alive() && locked(&self.child)?.try_wait().map_err(|error| error.to_string())?.is_none() {
            return Err("The SDK connection was lost while its process is still running. Its write outcome is unknown; automatic termination is blocked.".into());
        }
        {
            let pending = locked(&self.pending)?;
            let jobs = locked(&self.jobs)?;
            ensure_idle(self.is_alive(), pending.len(), jobs.len())?;
            // Registration uses the same pending lock; nothing can slip between
            // the idle check and stopping admission of requests/jobs.
            self.accepting.store(false, Ordering::Release);
        }
        self.shutdown(request_id);
        Ok(())
    }

    pub fn shutdown(&self, request_id: String) {
        if self.is_alive() {
            let _ = self.request(
                Envelope::request(request_id, "shutdown", json!({})),
                Duration::from_secs(3),
            );
        }
        self.terminate();
    }
}

pub struct SidecarManager {
    app: AppHandle,
    broker: Mutex<Option<Arc<Broker>>>,
    handshake: Mutex<Option<Envelope>>,
    counter: AtomicU64,
    stopped: AtomicBool,
}

impl SidecarManager {
    pub fn new(app: AppHandle) -> Self {
        Self {
            app,
            broker: Mutex::new(None),
            handshake: Mutex::new(None),
            counter: AtomicU64::new(1),
            stopped: AtomicBool::new(false),
        }
    }

    pub fn next_id(&self, prefix: &str) -> String {
        let value = self.counter.fetch_add(1, Ordering::Relaxed);
        format!("rust-{prefix}-{value}")
    }

    pub fn ensure_started(&self) -> Result<Arc<Broker>, String> {
        let mut slot = locked(&self.broker)?;
        if self.stopped.load(Ordering::Acquire) {
            return Err("SDK service is closing".into());
        }
        if let Some(current) = slot.as_ref() {
            if current.is_alive() {
                return Ok(current.clone());
            }
            retire_before_replacement(current, self.next_id("replacement-shutdown"))?;
            *slot = None;
        }
        let broker = Broker::spawn(self.app.clone())?;
        let handshake = Envelope::request(
            self.next_id("handshake"),
            "handshake",
            json!({
                "client": {"name": "ALLIN1 Tauri", "version": env!("CARGO_PKG_VERSION")},
                "supported_versions": [crate::protocol::PROTOCOL_VERSION]
            }),
        );
        let mut response = match broker.request(handshake, Duration::from_secs(8)) {
            Ok(response) => response,
            Err(error) => {
                // The broker is still private to this handshake attempt. Once
                // admitted to the manager, uncertain work must use try_shutdown.
                broker.terminate();
                return Err(error);
            }
        };
        if response.operation == "error" {
            broker.terminate();
            return Err(format!("sidecar handshake failed: {}", response.payload));
        }
        if let Some(identity) = crate::build_identity() {
            if let Err(error) = validate_build_peer(&response.payload, &identity, env!("CARGO_PKG_VERSION")) {
                broker.terminate();
                return Err(error);
            }
            response.payload["shell_build_identity"] = identity;
        } else if !cfg!(debug_assertions) {
            broker.terminate();
            return Err("Release shell has no build provenance. Rebuild through the candidate pipeline.".into());
        }
        match locked(&self.handshake) {
            Ok(mut retained) => *retained = Some(response),
            Err(error) => {
                broker.terminate();
                return Err(error);
            }
        }
        *slot = Some(broker.clone());
        let _ = self.app.emit("sidecar-status", "SDK sidecar connected");
        Ok(broker)
    }

    pub fn handshake(&self) -> Result<Envelope, String> {
        self.ensure_started()?;
        locked(&self.handshake)?
            .clone()
            .ok_or_else(|| "sidecar handshake was not retained".to_string())
    }

    pub fn request(
        &self,
        operation: &str,
        payload: Value,
        timeout: Duration,
    ) -> Result<Envelope, String> {
        let broker = self.ensure_started()?;
        broker.request(
            Envelope::request(self.next_id(operation), operation, payload),
            timeout,
        )
    }

    pub fn register_job(
        &self,
        job_id: String,
        channel: Channel<Envelope>,
    ) -> Result<Arc<Broker>, String> {
        let broker = self.ensure_started()?;
        broker.register_job(job_id, channel)?;
        Ok(broker)
    }

    pub fn restart(&self) -> Result<Envelope, String> {
        {
            let mut slot = locked(&self.broker)?;
            if let Some(previous) = slot.as_ref() {
                previous.try_shutdown(self.next_id("restart-shutdown"))?;
            }
            *slot = None;
        }
        *locked(&self.handshake)? = None;
        self.ensure_started()?;
        Ok(Envelope {
            protocol_version: crate::protocol::PROTOCOL_VERSION.to_string(),
            request_id: Some(self.next_id("restart")),
            job_id: None,
            operation: "result".to_string(),
            payload: json!({"state": "restarted"}),
            sequence: 0,
            risk: "none".to_string(),
            terminal: true,
        })
    }

    pub fn try_shutdown(&self) -> Result<(), String> {
        let mut slot = locked(&self.broker)?;
        if let Some(broker) = slot.as_ref() {
            broker.try_shutdown(self.next_id("shutdown"))?;
        }
        self.stopped.store(true, Ordering::Release);
        *slot = None;
        Ok(())
    }
}

fn ensure_idle(alive: bool, requests: usize, jobs: usize) -> Result<(), String> {
    if alive && (requests != 0 || jobs != 0) {
        Err("An SDK operation is still running. Wait for it to finish or cancel its inspection before closing or restarting.".into())
    } else {
        Ok(())
    }
}

pub fn validate_command(command: &str, args: &[String]) -> Result<(), String> {
    if command.is_empty()
        || command.len() > 96
        || !command.chars().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '-'
        })
    {
        return Err(
            "command must use 1-96 lowercase ASCII letters, digits, or hyphens".to_string(),
        );
    }
    if args.len() > 128
        || args
            .iter()
            .any(|item| item.contains('\0') || item.len() > 32_768)
    {
        return Err("args must contain at most 128 bounded strings without NUL bytes".to_string());
    }
    Ok(())
}

#[cfg(test)]
mod build_identity_tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn running_or_timed_out_requests_and_jobs_block_shutdown() {
        assert!(ensure_idle(true, 0, 0).is_ok());
        assert!(ensure_idle(true, 1, 0).is_err());
        assert!(ensure_idle(true, 0, 1).is_err());
        assert!(ensure_idle(false, 1, 1).is_ok());
    }

    #[test]
    fn same_version_different_build_or_missing_provenance_is_rejected() {
        let expected = json!({"build_id": "candidate-a", "source": "reviewed-input"});
        let valid = json!({"sdk_version": "0.6.4", "build_identity": expected});
        assert!(validate_build_peer(&valid, &expected, "0.6.4").is_ok());
        for invalid in [
            json!({"sdk_version": "0.6.4"}),
            json!({"sdk_version": "0.6.4", "build_identity": {"build_id": "candidate-b"}}),
            json!({"sdk_version": "0.6.3", "build_identity": expected}),
        ] {
            assert!(validate_build_peer(&invalid, &expected, "0.6.4").is_err());
        }
    }

    #[test]
    fn bounded_reader_handles_eof_multibyte_and_oversize_frames() {
        let mut multiple = Cursor::new(b"{}\nnext\n");
        assert_eq!(read_bounded_frame(&mut multiple).unwrap(), Some("{}".into()));
        assert_eq!(read_bounded_frame(&mut multiple).unwrap(), Some("next".into()));
        assert_eq!(read_bounded_frame(&mut Cursor::new("{\"name\":\"café\"}\r\n".as_bytes())).unwrap(), Some("{\"name\":\"café\"}".into()));
        assert_eq!(read_bounded_frame(&mut Cursor::new(Vec::<u8>::new())).unwrap(), None);
        assert!(read_bounded_frame(&mut Cursor::new(b"{}".to_vec())).is_err());
        let mut exact = vec![b'x'; MAX_RESPONSE_BYTES - 1];
        exact.push(b'\n');
        assert_eq!(read_bounded_frame(&mut Cursor::new(exact)).unwrap().unwrap().len(), MAX_RESPONSE_BYTES - 1);
        assert!(read_bounded_frame(&mut Cursor::new(vec![b'x'; MAX_RESPONSE_BYTES + 1])).is_err());
    }

    #[cfg(windows)]
    fn disconnected_owned_broker() -> Arc<Broker> {
        use std::os::windows::process::CommandExt;
        let mut child = Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", "[Console]::In.ReadLine() | Out-Null"])
            .creation_flags(windows_sys::Win32::System::Threading::CREATE_NO_WINDOW)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
            .spawn().unwrap();
        Arc::new(Broker {
            stdin: Mutex::new(child.stdin.take().unwrap()),
            child: Mutex::new(child),
            pending: Arc::new(Mutex::new(HashMap::new())),
            jobs: Arc::new(Mutex::new(HashMap::new())),
            // Simulates a protocol-reader disconnect while the owned child
            // remains running and could still be committing a write.
            alive: Arc::new(AtomicBool::new(false)),
            accepting: AtomicBool::new(false),
        })
    }

    #[cfg(windows)]
    #[test]
    fn disconnected_live_child_cannot_be_retired_for_replacement() {
        let broker = disconnected_owned_broker();
        let error = retire_before_replacement(&broker, "test-replacement".into()).unwrap_err();
        let alive = {
            let mut child = broker.child.lock().unwrap();
            let alive = child.try_wait().unwrap().is_none();
            // This test owns the inert child. The retirement guard itself did
            // not terminate it.
            child.kill().unwrap(); child.wait().unwrap(); alive
        };
        assert!(error.contains("automatic termination is blocked"));
        assert!(alive);
    }
}
