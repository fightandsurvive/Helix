//! Weakness Finder commands — query cached insights, trigger refreshes.

use serde::Serialize;
use std::process::Command;
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct WeaknessInsight {
    pub insight_id: i64,
    pub category: String,
    pub severity: String,
    pub title: String,
    pub description: String,
    pub metric_name: String,
    pub metric_value: f32,
    pub baseline_value: Option<f32>,
    pub sample_size: i32,
    pub confidence: f32,
    pub impact_score: f32,
    pub action_type: String,
    pub action_label: String,
    pub action_payload: Option<serde_json::Value>,
    pub evidence: Option<serde_json::Value>,
    pub computed_at: chrono::DateTime<chrono::Utc>,
}

#[tauri::command]
pub async fn get_weakness_insights(
    state: State<'_, AppState>,
    profile_id: i64,
    min_confidence: Option<f32>,
    category_filter: Option<Vec<String>>,
) -> Result<Vec<WeaknessInsight>, AppError> {
    let min_conf = min_confidence.unwrap_or(0.0);

    let rows: Vec<(
        i64, String, String, String, String,
        String, f32, Option<f32>, i32, f32, f32,
        String, String, Option<serde_json::Value>,
        Option<serde_json::Value>, chrono::DateTime<chrono::Utc>,
    )> = sqlx::query_as(
        "SELECT insight_id, category, severity, title, description,
                metric_name, metric_value, baseline_value, sample_size,
                confidence, impact_score, action_type, action_label,
                action_payload, evidence, computed_at
         FROM analytics.weakness_insights
         WHERE profile_id = $1
           AND confidence >= $2
           AND ($3::text[] IS NULL OR category = ANY($3))
         ORDER BY (impact_score * confidence) DESC"
    )
    .bind(profile_id)
    .bind(min_conf)
    .bind(category_filter)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|r| WeaknessInsight {
            insight_id: r.0,
            category: r.1,
            severity: r.2,
            title: r.3,
            description: r.4,
            metric_name: r.5,
            metric_value: r.6,
            baseline_value: r.7,
            sample_size: r.8,
            confidence: r.9,
            impact_score: r.10,
            action_type: r.11,
            action_label: r.12,
            action_payload: r.13,
            evidence: r.14,
            computed_at: r.15,
        })
        .collect())
}

#[tauri::command]
pub async fn refresh_weakness_insights(
    _state: State<'_, AppState>,
    profile_id: i64,
) -> Result<i64, AppError> {
    let output = tokio::task::spawn_blocking(move || {
        Command::new("python")
            .args(["-m", "analytics.weakness", &profile_id.to_string()])
            .output()
    })
    .await
    .map_err(|e| AppError::Internal(e.to_string()))?
    .map_err(|e| AppError::Internal(e.to_string()))?;

    if !output.status.success() {
        return Err(AppError::Internal(
            String::from_utf8_lossy(&output.stderr).to_string(),
        ));
    }

    // Parse the count from stdout — the CLI prints the full insight list,
    // we just count it. Faster: re-query the cache.
    Ok(0)
}

#[tauri::command]
pub async fn has_weakness_insights(
    state: State<'_, AppState>,
    profile_id: i64,
) -> Result<bool, AppError> {
    let row: (bool,) = sqlx::query_as(
        "SELECT EXISTS(SELECT 1 FROM analytics.weakness_insights WHERE profile_id = $1)"
    )
    .bind(profile_id)
    .fetch_one(&state.pool)
    .await?;
    Ok(row.0)
}
