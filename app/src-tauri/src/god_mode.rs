//! God Mode analysis — three candidate moves per position.
//!
//! Stockfish + brilliant candidates come from `engine/god_mode_helper.py`
//! (spawned as subprocess per call, ~600ms total including SF).
//! Trinstinctus best comes from the latest self-play data in the DB.

use serde::Serialize;
use std::process::Command;
use tauri::State;

use crate::db::AppState;
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
    pub trinstinctus: Option<CandidateMove>,
    pub brilliant: Option<CandidateMove>,
}

fn run_sf_helper(fen: &str) -> Result<serde_json::Value, String> {
    let output = Command::new("python")
        .args(["-m", "engine.god_mode_helper", fen])
        .output()
        .map_err(|e| format!("spawn helper: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "helper failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    serde_json::from_slice(&output.stdout).map_err(|e| format!("parse: {e}"))
}

#[tauri::command]
pub async fn analyze_god_mode(
    state: State<'_, AppState>,
    fen: String,
) -> Result<GodModeAnalysis, AppError> {
    if fen.len() > 200 || fen.split_whitespace().count() < 4 {
        return Err(AppError::InvalidInput("invalid FEN".into()));
    }

    // Trinstinctus recommendation from latest self-play position.
    let trin: Option<(String, f32)> = sqlx::query_as(
        "SELECT move_notation, value_target FROM selfplay.positions
         WHERE fen_string=$1 ORDER BY generation DESC LIMIT 1",
    )
    .bind(&fen)
    .fetch_optional(&state.pool)
    .await?;

    let trinstinctus = trin.map(|(mv, val)| CandidateMove {
        source: "trinstinctus".into(),
        san: mv.clone(),
        mv,
        eval_cp: (val * 400.0) as i32,
        pv: vec![],
        explanation: "Trinstinctus MCTS recommendation (highest visit count).".into(),
    });

    // SF + brilliant via helper subprocess.
    let fen_clone = fen.clone();
    let helper_result = tokio::task::spawn_blocking(move || run_sf_helper(&fen_clone))
        .await
        .map_err(|e| AppError::Internal(e.to_string()))?;

    let (stockfish, brilliant) = match helper_result {
        Ok(json) => {
            let sf = json.get("stockfish").and_then(|v| v.as_object()).map(|obj| {
                CandidateMove {
                    source: "stockfish".into(),
                    mv: obj
                        .get("uci")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    san: obj
                        .get("san")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    eval_cp: obj.get("eval_cp").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    pv: obj
                        .get("pv")
                        .and_then(|v| v.as_array())
                        .map(|a| {
                            a.iter()
                                .filter_map(|s| s.as_str().map(String::from))
                                .collect()
                        })
                        .unwrap_or_default(),
                    explanation: "Stockfish objective best move.".into(),
                }
            });
            let br = json.get("brilliant").and_then(|v| v.as_object()).map(|obj| {
                CandidateMove {
                    source: "brilliant".into(),
                    mv: obj
                        .get("uci")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    san: obj
                        .get("san")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    eval_cp: obj.get("eval_cp").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    pv: obj
                        .get("pv")
                        .and_then(|v| v.as_array())
                        .map(|a| {
                            a.iter()
                                .filter_map(|s| s.as_str().map(String::from))
                                .collect()
                        })
                        .unwrap_or_default(),
                    explanation:
                        "Sacrifice that maintains advantage — chess.com-style brilliant.".into(),
                }
            });
            (sf, br)
        }
        Err(e) => {
            tracing::warn!(error = %e, "god_mode_helper_failed");
            (None, None)
        }
    };

    Ok(GodModeAnalysis {
        fen,
        stockfish,
        trinstinctus,
        brilliant,
    })
}
