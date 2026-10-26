//! Personal game analytics — per-opening stats and repertoire queries.
//!
//! These work on app.personal_games (imported from Chess.com/Lichess)
//! and complement the cross-source views in analytics.rs.

use serde::Serialize;
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct OpeningStat {
    pub eco: String,
    pub name: String,
    pub games: i64,
    pub score: f32,
}

#[tauri::command]
pub async fn get_opening_stats(
    state: State<'_, AppState>,
    profile_id: i64,
    color: Option<String>,
) -> Result<Vec<OpeningStat>, AppError> {
    let rows: Vec<(String, String, i64, f32)> = sqlx::query_as(
        "SELECT eco_code, opening_name, games, score
         FROM app.player_opening_stats
         WHERE profile_id = $1 AND ($2::text IS NULL OR user_color = $2)
         ORDER BY score DESC, games DESC",
    )
    .bind(profile_id)
    .bind(color)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(eco, name, games, score)| OpeningStat {
            eco,
            name,
            games,
            score,
        })
        .collect())
}

#[tauri::command]
pub async fn best_repertoire_against(
    state: State<'_, AppState>,
    profile_id: i64,
    opponent_eco: String,
) -> Result<Vec<OpeningStat>, AppError> {
    // The SQL function returns (eco, name, games, score) already filtered
    // to black-side responses with >= 3 games. We pass through directly.
    let rows: Vec<(String, String, i64, f32)> =
        sqlx::query_as("SELECT * FROM app.best_repertoire_against($1, $2, 3)")
            .bind(profile_id)
            .bind(&opponent_eco)
            .fetch_all(&state.pool)
            .await?;

    Ok(rows
        .into_iter()
        .map(|(eco, name, games, score)| OpeningStat {
            eco,
            name,
            games,
            score,
        })
        .collect())
}

#[derive(Serialize)]
pub struct GambitStat {
    pub gambit_id: String,
    pub name: String,
    pub games: i64,
    pub win_rate: f32,
    pub wins: i64,
    pub losses: i64,
    pub draws: i64,
}

#[tauri::command]
pub async fn get_gambit_stats(
    state: State<'_, AppState>,
    profile_id: i64,
) -> Result<Vec<GambitStat>, AppError> {
    let rows: Vec<(String, String, i64, f32, i64, i64, i64)> = sqlx::query_as(
        "SELECT s.gambit_id, g.name, s.games, s.win_rate,
                s.wins, s.losses, s.draws
         FROM app.player_gambit_stats s
         JOIN app.gambits g USING (gambit_id)
         WHERE s.profile_id = $1
         ORDER BY s.games DESC",
    )
    .bind(profile_id)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(id, name, g, w, wi, l, d)| GambitStat {
            gambit_id: id,
            name,
            games: g,
            win_rate: w,
            wins: wi,
            losses: l,
            draws: d,
        })
        .collect())
}

#[tauri::command]
pub async fn review_personal_game(
    state: State<'_, AppState>,
    game_id: i64,
) -> Result<serde_json::Value, AppError> {
    let row: Option<(Option<serde_json::Value>, String)> = sqlx::query_as(
        "SELECT review_json, pgn FROM app.personal_games WHERE game_id=$1",
    )
    .bind(game_id)
    .fetch_optional(&state.pool)
    .await?;

    match row {
        Some((Some(cached), _)) => Ok(cached),
        Some((None, pgn)) => {
            // Full review is expensive (SF + LLM per move). Queue for the
            // accuracy worker; the frontend renders the cached result once
            // it arrives.
            sqlx::query(
                "INSERT INTO analytics.accuracy_jobs (source, ref_id, priority)
                 VALUES ('personal', $1, 5) ON CONFLICT DO NOTHING",
            )
            .bind(game_id)
            .execute(&state.pool)
            .await?;
            Ok(serde_json::json!({"status": "queued", "pgn": pgn}))
        }
        None => Err(AppError::NotFound(format!("game {game_id}"))),
    }
}
