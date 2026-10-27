//! Capability probe — runs `python -m config.capabilities` once at startup
//! and caches the JSON. Frontend reads it to decide what to render.

use once_cell::sync::OnceCell;
use serde::{Deserialize, Serialize};
use std::process::Command;

#[derive(Serialize, Deserialize, Clone)]
pub struct Capabilities {
    pub engine_available: bool,
    pub training_available: bool,
    pub coach_llm_available: bool,
    pub stockfish_available: bool,
    pub stockfish_path: String,
    pub syzygy_available: bool,
    pub gpu_name: Option<String>,
    pub gpu_vram_gb: Option<f64>,
    pub is_blackwell: bool,
    pub engine_reason: String,
    pub training_reason: String,
    pub coach_reason: String,
}

static CACHE: OnceCell<Capabilities> = OnceCell::new();

fn probe_blocking() -> Capabilities {
    match Command::new("python").args(["-m", "config.capabilities"]).output() {
        Ok(o) if o.status.success() => serde_json::from_slice(&o.stdout).unwrap_or_else(|e| {
            tracing::error!(error = %e, "capability_parse_failed");
            disabled("Malformed capability probe output")
        }),
        _ => disabled("Capability probe failed — is the Python venv active?"),
    }
}

fn disabled(reason: &str) -> Capabilities {
    Capabilities {
        engine_available: false,
        training_available: false,
        coach_llm_available: false,
        stockfish_available: false,
        stockfish_path: String::new(),
        syzygy_available: false,
        gpu_name: None,
        gpu_vram_gb: None,
        is_blackwell: false,
        engine_reason: reason.into(),
        training_reason: reason.into(),
        coach_reason: reason.into(),
    }
}

pub fn get() -> &'static Capabilities {
    CACHE.get_or_init(probe_blocking)
}

#[tauri::command]
pub async fn get_capabilities() -> Result<Capabilities, String> {
    Ok(get().clone())
}
