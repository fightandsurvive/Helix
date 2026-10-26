//! Persistent panel service bridge.
//!
//! Spawns `python -m engine.panel_service` once (lazy on first request),
//! keeps stdin/stdout pipes open, serializes requests behind a mutex.
//! Model loading is ~10s — per-request subprocess would make the panel
//! unusable. After warm-up, each request is ~350-600ms (SF dominates).

use once_cell::sync::OnceCell;
use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;

use crate::errors::AppError;

struct PanelHandle {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

static PANEL_SERVICE: OnceCell<Mutex<Option<PanelHandle>>> = OnceCell::new();

fn spawn_service() -> Result<PanelHandle, String> {
    let mut child = Command::new("python")
        .args(["-m", "engine.panel_service"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("spawn panel_service: {e}"))?;
    let stdin = child.stdin.take().ok_or("no stdin")?;
    let stdout = BufReader::new(child.stdout.take().ok_or("no stdout")?);
    Ok(PanelHandle { child, stdin, stdout })
}

#[derive(Serialize)]
struct AnalyzeRequest<'a> {
    cmd: &'a str,
    fen: &'a str,
}

#[derive(Deserialize)]
struct ServiceResponse {
    status: String,
    panel: Option<serde_json::Value>,
    commentary: Option<String>,
    error: Option<String>,
}

fn call_service(cmd: &str, fen: &str) -> Result<serde_json::Value, String> {
    let cell = PANEL_SERVICE.get_or_init(|| Mutex::new(None));
    let mut guard = cell.lock().map_err(|_| "panel mutex poisoned")?;

    if guard.is_none() {
        *guard = Some(spawn_service()?);
    }
    let svc = guard.as_mut().ok_or("panel service not available")?;

    let req = AnalyzeRequest { cmd, fen };
    let line = serde_json::to_string(&req).map_err(|e| e.to_string())?;
    writeln!(svc.stdin, "{line}").map_err(|e| e.to_string())?;
    svc.stdin.flush().map_err(|e| e.to_string())?;

    let mut resp_line = String::new();
    svc.stdout.read_line(&mut resp_line).map_err(|e| e.to_string())?;

    let resp: ServiceResponse = serde_json::from_str(&resp_line).map_err(|e| e.to_string())?;
    match resp.status.as_str() {
        "ok" => {
            let mut out = serde_json::json!({
                "panel": resp.panel.unwrap_or(serde_json::json!({})),
            });
            if let Some(c) = resp.commentary {
                out["commentary"] = serde_json::Value::String(c);
            }
            Ok(out)
        }
        _ => Err(resp.error.unwrap_or_else(|| "unknown panel error".into())),
    }
}

#[tauri::command]
pub async fn panel_analysis(fen: String) -> Result<serde_json::Value, AppError> {
    let f = fen.clone();
    tokio::task::spawn_blocking(move || call_service("analyze", &f))
        .await
        .map_err(|e| AppError::Internal(e.to_string()))?
        .map_err(AppError::Internal)
}

#[tauri::command]
pub async fn panel_commentary(fen: String) -> Result<serde_json::Value, AppError> {
    let f = fen.clone();
    tokio::task::spawn_blocking(move || call_service("commentary", &f))
        .await
        .map_err(|e| AppError::Internal(e.to_string()))?
        .map_err(AppError::Internal)
}

/// Called on window close — sends shutdown so the Python process releases VRAM.
pub fn shutdown() {
    if let Some(cell) = PANEL_SERVICE.get() {
        if let Ok(mut guard) = cell.lock() {
            if let Some(mut svc) = guard.take() {
                let _ = writeln!(svc.stdin, r#"{{"cmd":"shutdown"}}"#);
                let _ = svc.stdin.flush();
                let _ = svc.child.wait();
            }
        }
    }
}
