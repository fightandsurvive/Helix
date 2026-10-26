//! Engine bridge — style weights, adapter metadata.

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct Analysis {
    pub best_move: String,
    pub eval: f32,
}

#[tauri::command]
pub async fn analyze_position(
    state: State<'_, AppState>,
    fen: String,
) -> Result<Analysis, AppError> {
    // Placeholder: production spawns `engine.uci_engine` as a persistent
    // child process. For now use the most recent self-play data as a proxy
    // — accurate for positions the engine has seen, empty otherwise.
    let row: Option<(f32,)> = sqlx::query_as(
        "SELECT AVG(value_target) FROM selfplay.positions WHERE fen_string=$1",
    )
    .bind(&fen)
    .fetch_optional(&state.pool)
    .await?;

    Ok(Analysis {
        best_move: "e2e4".into(),
        eval: row.map(|r| r.0).unwrap_or(0.0),
    })
}

#[derive(Serialize, Deserialize)]
pub struct StyleWeights {
    pub profile: String,
    pub morphy: f32,
    pub petrosian: f32,
    pub tal_kasparov: f32,
    pub fischer: f32,
    pub fischer_endgame: f32,
    pub carlsen_endgame: f32,
    pub temperature: f32,
    pub contempt: f32,
}

#[tauri::command]
pub async fn get_style_weights(
    state: State<'_, AppState>,
    profile: String,
) -> Result<StyleWeights, AppError> {
    let row: (String, f32, f32, f32, f32, f32, f32, f32, f32) = sqlx::query_as(
        "SELECT profile_name, morphy, petrosian, tal_kasparov, fischer,
                fischer_endgame, carlsen_endgame, temperature, contempt
         FROM engine_state.style_weights WHERE profile_name=$1",
    )
    .bind(&profile)
    .fetch_one(&state.pool)
    .await?;

    Ok(StyleWeights {
        profile: row.0,
        morphy: row.1,
        petrosian: row.2,
        tal_kasparov: row.3,
        fischer: row.4,
        fischer_endgame: row.5,
        carlsen_endgame: row.6,
        temperature: row.7,
        contempt: row.8,
    })
}

#[tauri::command]
pub async fn set_style_weights(
    state: State<'_, AppState>,
    w: StyleWeights,
) -> Result<(), AppError> {
    if w.temperature < 0.0 || w.temperature > 5.0 {
        return Err(AppError::InvalidInput("temperature must be in [0,5]".into()));
    }
    if w.contempt < 0.0 || w.contempt > 1.0 {
        return Err(AppError::InvalidInput("contempt must be in [0,1]".into()));
    }
    sqlx::query(
        "UPDATE engine_state.style_weights SET
            morphy=$2, petrosian=$3, tal_kasparov=$4, fischer=$5,
            fischer_endgame=$6, carlsen_endgame=$7, temperature=$8, contempt=$9,
            updated_at=NOW()
         WHERE profile_name=$1",
    )
    .bind(&w.profile)
    .bind(w.morphy)
    .bind(w.petrosian)
    .bind(w.tal_kasparov)
    .bind(w.fischer)
    .bind(w.fischer_endgame)
    .bind(w.carlsen_endgame)
    .bind(w.temperature)
    .bind(w.contempt)
    .execute(&state.pool)
    .await?;
    Ok(())
}

#[tauri::command]
pub async fn count_adapters(state: State<'_, AppState>) -> Result<i64, AppError> {
    let row: (i64,) =
        sqlx::query_as("SELECT COUNT(*) FROM engine_state.checkpoints WHERE phase='adapter'")
            .fetch_one(&state.pool)
            .await?;
    Ok(row.0)
}

#[derive(Serialize)]
pub struct AdapterInfo {
    pub legend: String,
    pub path: String,
    pub generation: Option<i32>,
}

#[tauri::command]
pub async fn list_adapters(state: State<'_, AppState>) -> Result<Vec<AdapterInfo>, AppError> {
    let rows: Vec<(String, String, Option<i32>)> = sqlx::query_as(
        "SELECT config->>'legend', path, generation
         FROM engine_state.checkpoints WHERE phase='adapter' ORDER BY path",
    )
    .fetch_all(&state.pool)
    .await?;
    Ok(rows
        .into_iter()
        .map(|(legend, path, gen)| AdapterInfo { legend, path, generation: gen })
        .collect())
}

#[tauri::command]
pub async fn save_position(
    state: State<'_, AppState>,
    profile_id: i64,
    fen: String,
    name: String,
    notes: Option<String>,
    tags: Vec<String>,
) -> Result<i64, AppError> {
    let row: (i64,) = sqlx::query_as(
        "INSERT INTO app.saved_positions (profile_id, fen, name, notes, tags)
         VALUES ($1, $2, $3, $4, $5) RETURNING position_id"
    )
    .bind(profile_id).bind(&fen).bind(&name).bind(notes).bind(&tags)
    .fetch_one(&state.pool).await?;
    Ok(row.0)
}

#[tauri::command]
pub async fn list_saved_positions(
    state: State<'_, AppState>,
    profile_id: i64,
) -> Result<Vec<(i64, String, String, Option<String>, Vec<String>)>, AppError> {
    let rows: Vec<(i64, String, String, Option<String>, Vec<String>)> = sqlx::query_as(
        "SELECT position_id, fen, name, notes, tags FROM app.saved_positions
         WHERE profile_id = $1 ORDER BY created_at DESC"
    ).bind(profile_id).fetch_all(&state.pool).await?;
    Ok(rows)
}

#[tauri::command]
pub async fn get_active_profile(
    state: State<'_, AppState>,
) -> Result<Option<i64>, AppError> {
    let row: Option<(i64,)> = sqlx::query_as(
        "SELECT profile_id FROM app.player_profiles
         ORDER BY last_synced DESC NULLS LAST, profile_id DESC LIMIT 1"
    ).fetch_optional(&state.pool).await?;
    Ok(row.map(|r| r.0))
}
