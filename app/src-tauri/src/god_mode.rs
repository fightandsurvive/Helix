//! God Mode — Stockfish + brilliant candidates.
//! Trinstinctus DB source removed; engine disabled on this build.

use serde::Serialize;
use std::process::Command;

use crate::errors::AppError;

#[derive(Serialize)]
pub struct CandidateMove {
    pub source: String,
    pub mv: String,
    pub san: String,
    pub eval_cp: i32,
    pub pv: Vec<String>,
    pub explanation: String,
}

#[derive(Serialize)]
pub struct GodModeAnalysis {
    pub fen: String,
    pub stockfish: Option<CandidateMove>,
    pub brilliant: Option<CandidateMove>,
    pub alternatives: Vec<CandidateMove>,
}

fn run_sf_helper(fen: &str) -> Result<serde_json::Value, String> {
    let mut cmd = crate::repo::python_command("engine.god_mode_helper", &[fen]);
    let output = cmd.output().map_err(|e| format!("spawn helper: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "helper failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    serde_json::from_slice(&output.stdout).map_err(|e| format!("parse: {e}"))
}

fn to_candidate(
    json: &serde_json::Value,
    source: &str,
    explanation: &str,
) -> Option<CandidateMove> {
    let obj = json.as_object()?;
    Some(CandidateMove {
        source: source.into(),
        mv: obj.get("uci").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        san: obj.get("san").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        eval_cp: obj.get("eval_cp").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
        pv: obj
            .get("pv")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect())
            .unwrap_or_default(),
        explanation: explanation.into(),
    })
}

#[tauri::command]
pub async fn analyze_god_mode(fen: String) -> Result<GodModeAnalysis, AppError> {
    if fen.len() > 200 || fen.split_whitespace().count() < 4 {
        return Err(AppError::InvalidInput("invalid FEN".into()));
    }

    let fen_clone = fen.clone();
    let helper_result = tokio::task::spawn_blocking(move || run_sf_helper(&fen_clone))
        .await
        .map_err(|e| AppError::Internal(e.to_string()))?;

    let json = helper_result.map_err(AppError::Internal)?;

    let stockfish = json
        .get("stockfish")
        .and_then(|v| to_candidate(v, "stockfish", "Stockfish objective best move."));
    let brilliant = json.get("brilliant").and_then(|v| {
        to_candidate(
            v,
            "brilliant",
            "Sacrifice that maintains advantage — chess.com-style brilliant.",
        )
    });
    let alternatives = json
        .get("alternatives")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| to_candidate(v, "alternative", "Alternative candidate."))
                .collect()
        })
        .unwrap_or_default();

    Ok(GodModeAnalysis {
        fen,
        stockfish,
        brilliant,
        alternatives,
    })
}
