//! Coach bridge — spawns `engine/coach.py` and parses the JSON response.
//!
//! Why subprocess: the coach runs Stockfish + Ollama calls, both of which
//! are already native to Python. Re-implementing the UCI protocol + HTTP
//! client in Rust would duplicate ~200 lines of code for no benefit.

use serde::Deserialize;
use std::process::Command;
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Deserialize, serde::Serialize)]
pub struct StockfishAnalysis {
    pub best_move_san: String,
    pub eval_cp: i32,
    pub eval_formatted: String,
    pub pv_san: Vec<String>,
    pub side_to_move: String,
}

#[derive(Deserialize, serde::Serialize)]
pub struct CoachResponse {
    pub analysis: StockfishAnalysis,
    pub commentary: String,
    pub persona: String,
}

#[derive(Deserialize)]
struct CoachEnvelope {
    status: String,
    analysis: Option<StockfishAnalysis>,
    commentary: Option<String>,
    persona: Option<String>,
    error: Option<String>,
}

#[tauri::command]
pub async fn coach_analyze(
    _state: State<'_, AppState>,
    fen: String,
    persona: Option<String>,
) -> Result<CoachResponse, AppError> {
    if fen.len() > 200 || fen.split_whitespace().count() < 4 {
        return Err(AppError::InvalidInput("invalid FEN".into()));
    }
    let persona = persona.unwrap_or_else(|| "neutral".to_string());
    let fen_clone = fen.clone();

    let result = tokio::task::spawn_blocking(move || -> Result<CoachResponse, String> {
        let mut cmd = Command::new("python");
        cmd.args(["-m", "engine.coach", &fen_clone, "--persona", &persona]);
        if let Some(root) = crate::repo::find_root() {
            cmd.current_dir(root);
        }
        let output = cmd
            .output()
            .map_err(|e| format!("spawn coach: {e}"))?;

        if !output.status.success() {
            return Err(format!(
                "coach failed: {}",
                String::from_utf8_lossy(&output.stderr)
            ));
        }

        let envelope: CoachEnvelope =
            serde_json::from_slice(&output.stdout).map_err(|e| format!("parse: {e}"))?;

        if envelope.status != "ok" {
            return Err(envelope.error.unwrap_or_else(|| "unknown coach error".into()));
        }

        Ok(CoachResponse {
            analysis: envelope
                .analysis
                .ok_or_else(|| "missing analysis in response".to_string())?,
            commentary: envelope.commentary.unwrap_or_default(),
            persona: envelope.persona.unwrap_or_else(|| "neutral".to_string()),
        })
    })
    .await
    .map_err(|e| AppError::Internal(e.to_string()))?;

    result.map_err(AppError::Internal)
}
