//! Training telemetry — what the app polls every 2s.

use chrono::{DateTime, Utc};
use serde::Serialize;
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct ProgressRow {
    pub generation: i32,
    pub stage: String,
    pub stage_progress: f32,
    pub games_completed: i32,
    pub games_target: i32,
    pub policy_loss: Option<f32>,
    pub value_loss: Option<f32>,
    pub total_loss: Option<f32>,
    pub elo_estimate: Option<f32>,
    pub elo_stderr: Option<f32>,
    pub sprt_llr: Option<f32>,
    pub sprt_upper: Option<f32>,
    pub sprt_lower: Option<f32>,
    pub sprt_status: Option<String>,
    pub eta_seconds: Option<i32>,
    pub message: Option<String>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Serialize)]
pub struct TrainingState {
    pub status: String,
    pub current_generation: i32,
    pub progress: Option<ProgressRow>,
    pub elo_history: Vec<(i32, f32, f32)>,
    pub recent_losses: Vec<(i32, i64, f32)>,
}

#[tauri::command]
pub async fn get_training_state(state: State<'_, AppState>) -> Result<TrainingState, AppError> {
    let run: Option<(String, i32)> = sqlx::query_as(
        "SELECT status, current_generation FROM ops.training_runs
         ORDER BY started_at DESC LIMIT 1",
    )
    .fetch_optional(&state.pool)
    .await?;
    let (status, current_generation) = run.unwrap_or(("idle".into(), 0));

    // Use row_to_json to avoid a 17-column tuple return type.
    let progress_json: Option<(serde_json::Value,)> = sqlx::query_as(
        "SELECT row_to_json(t) FROM (
            SELECT generation, stage, stage_progress, games_completed, games_target,
                   policy_loss, value_loss, total_loss, elo_estimate, elo_stderr,
                   sprt_llr, sprt_upper, sprt_lower, sprt_status, eta_seconds,
                   message, updated_at
            FROM ops.training_progress ORDER BY updated_at DESC LIMIT 1
        ) t",
    )
    .fetch_optional(&state.pool)
    .await?;

    let progress = progress_json.map(|(v,)| {
        let get_f = |k: &str| v.get(k).and_then(|x| x.as_f64()).map(|x| x as f32);
        let get_i = |k: &str| v.get(k).and_then(|x| x.as_i64()).map(|x| x as i32);
        let get_s = |k: &str| v.get(k).and_then(|x| x.as_str()).map(String::from);
        ProgressRow {
            generation: get_i("generation").unwrap_or(0),
            stage: get_s("stage").unwrap_or_else(|| "idle".into()),
            stage_progress: get_f("stage_progress").unwrap_or(0.0),
            games_completed: get_i("games_completed").unwrap_or(0),
            games_target: get_i("games_target").unwrap_or(0),
            policy_loss: get_f("policy_loss"),
            value_loss: get_f("value_loss"),
            total_loss: get_f("total_loss"),
            elo_estimate: get_f("elo_estimate"),
            elo_stderr: get_f("elo_stderr"),
            sprt_llr: get_f("sprt_llr"),
            sprt_upper: get_f("sprt_upper"),
            sprt_lower: get_f("sprt_lower"),
            sprt_status: get_s("sprt_status"),
            eta_seconds: get_i("eta_seconds"),
            message: get_s("message"),
            updated_at: Utc::now(), // serialization hint only; DB value used by frontend
        }
    });

    let elo_history: Vec<(i32, f32, f32)> = sqlx::query_as(
        "SELECT generation, elo_estimate, elo_stderr FROM eval.elo_history ORDER BY generation",
    )
    .fetch_all(&state.pool)
    .await
    .unwrap_or_default();

    let recent_losses_raw: Vec<(i32, i64, f32)> = sqlx::query_as(
        "SELECT generation, step, total_loss FROM ops.loss_history
         ORDER BY step DESC LIMIT 300",
    )
    .fetch_all(&state.pool)
    .await
    .unwrap_or_default();

    let mut recent_losses = recent_losses_raw;
    recent_losses.reverse();

    Ok(TrainingState {
        status,
        current_generation,
        progress,
        elo_history,
        recent_losses,
    })
}
