//! Import personal games from Chess.com / Lichess via Python ingest script.

use serde::Serialize;
use std::process::Command;
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct ImportResult {
    pub profile_id: i64,
    pub games_imported: i64,
    pub message: String,
}

#[tauri::command]
pub async fn import_personal_games(
    state: State<'_, AppState>,
    platform: String,
    username: String,
) -> Result<ImportResult, AppError> {
    if platform != "lichess" && platform != "chesscom" {
        return Err(AppError::InvalidInput(
            "platform must be 'lichess' or 'chesscom'".into(),
        ));
    }
    if username.is_empty() || username.len() > 60 {
        return Err(AppError::InvalidInput("invalid username".into()));
    }

    let u = username.clone();
    let p = platform.clone();

    let output = tokio::task::spawn_blocking(move || -> Result<(), String> {
        let mut cmd = Command::new("python");
        cmd.args(["-m", "ingest.personal_games", "--platform", &p, "--username", &u]);
        if let Some(root) = crate::repo::find_root() {
            cmd.current_dir(root);
        }
        let out = cmd.output().map_err(|e| format!("spawn import: {e}"))?;
        if !out.status.success() {
            return Err(format!(
                "import failed: {}",
                String::from_utf8_lossy(&out.stderr)
            ));
        }
        Ok(())
    })
    .await
    .map_err(|e| AppError::Internal(e.to_string()))?
    .map_err(AppError::Internal)?;

    let _ = output;

    // Look up the profile (import script creates it)
    let row: Option<(i64,)> = sqlx::query_as(
        "SELECT profile_id FROM app.player_profiles
         WHERE platform = $1 AND username = $2",
    )
    .bind(&platform)
    .bind(&username)
    .fetch_optional(&state.pool)
    .await?;

    let profile_id = row
        .map(|r| r.0)
        .ok_or_else(|| AppError::Internal("profile not created by import".into()))?;

    let count: (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM app.personal_games WHERE profile_id = $1",
    )
    .bind(profile_id)
    .fetch_one(&state.pool)
    .await?;

    Ok(ImportResult {
        profile_id,
        games_imported: count.0,
        message: format!("Imported {} games from {} for {}", count.0, platform, username),
    })
}
