use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const PROTOCOL_VERSION: &str = "1.0.0";
pub const MAX_REQUEST_BYTES: usize = 256 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Envelope {
    pub protocol_version: String,
    pub request_id: Option<String>,
    pub job_id: Option<String>,
    pub operation: String,
    pub payload: Value,
    pub sequence: u64,
    pub risk: String,
    pub terminal: bool,
}

impl Envelope {
    pub fn request(request_id: String, operation: &str, payload: Value) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION.to_string(),
            request_id: Some(request_id),
            job_id: None,
            operation: operation.to_string(),
            payload,
            sequence: 0,
            risk: "none".to_string(),
            terminal: false,
        }
    }

    pub fn local_error(request_id: Option<String>, message: impl Into<String>) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION.to_string(),
            request_id,
            job_id: None,
            operation: "error".to_string(),
            payload: json!({ "message": message.into() }),
            sequence: 0,
            risk: "unclassified".to_string(),
            terminal: true,
        }
    }

    pub fn validate_response(&self) -> Result<(), String> {
        if self.protocol_version != PROTOCOL_VERSION {
            return Err(format!(
                "sidecar protocol drift: expected {PROTOCOL_VERSION}, received {}",
                self.protocol_version
            ));
        }
        if !matches!(self.operation.as_str(), "result" | "error" | "job_event") {
            return Err(format!(
                "sidecar returned an invalid operation: {}",
                self.operation
            ));
        }
        if !matches!(
            self.risk.as_str(),
            "none" | "read_only" | "authoring_write" | "game_write" | "unclassified"
        ) {
            return Err(format!("sidecar returned an invalid risk: {}", self.risk));
        }
        Ok(())
    }
}
