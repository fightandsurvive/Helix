//! Analytics commands — unified across personal + eval sources.

use serde::Serialize;
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct GambitRow {
    pub gambit_id: String,
    pub gambit_name: String,
    pub side: String,
    pub soundness: Option<String>,
    pub games: i64,
    pub wins: i64,
    pub losses: i64,
    pub draws: i64,
    pub score: f32,
}

#[tauri::command]
pub async fn get_participant_gambit_stats(
    state: State<'_, AppState>,
    participant: String,
    source: Option<String>,
    min_games: Option<i64>,
) -> Result<Vec<GambitRow>, AppError> {
    let min = min_games.unwrap_or(1).max(1);
    let rows: Vec<(
        String, String, String, Option<String>, i64, i64, i64, i64, f32,
    )> = sqlx::query_as(
        "SELECT s.gambit_id, s.gambit_name, s.side, s.soundness,
                s.games, s.wins, s.losses, s.draws, s.score
         FROM analytics.participant_gambit_stats s
         WHERE s.participant = $1 AND s.games >= $2
           AND ($3::text IS NULL OR s.participant_type = $3)
         ORDER BY s.score DESC, s.games DESC",
    )
    .bind(&participant)
    .bind(min)
    .bind(source)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(id, name, side, snd, g, w, l, d, s)| GambitRow {
            gambit_id: id,
            gambit_name: name,
            side,
            soundness: snd,
            games: g,
            wins: w,
            losses: l,
            draws: d,
            score: s,
        })
        .collect())
}

#[derive(Serialize)]
pub struct EcoRow {
    pub eco_code: String,
    pub opening_name: String,
    pub color: String,
    pub games: i64,
    pub score: f32,
}

#[tauri::command]
pub async fn get_participant_eco_stats(
    state: State<'_, AppState>,
    participant: String,
    color: Option<String>,
    min_games: Option<i64>,
) -> Result<Vec<EcoRow>, AppError> {
    let min = min_games.unwrap_or(3).max(1);
    let rows: Vec<(String, String, String, i64, f32)> = sqlx::query_as(
        "SELECT eco_code, opening_name, color, games, score
         FROM analytics.participant_eco_stats
         WHERE participant = $1 AND games >= $2
           AND ($3::text IS NULL OR color = $3)
         ORDER BY score DESC, games DESC",
    )
    .bind(&participant)
    .bind(min)
    .bind(color)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(eco, name, color, games, score)| EcoRow {
            eco_code: eco,
            opening_name: name,
            color,
            games,
            score,
        })
        .collect())
}

#[derive(Serialize)]
pub struct AccuracyRow {
    pub source: String,
    pub ref_id: i64,
    pub participant: String,
    pub color: String,
    pub accuracy: f32,
    pub acpl: f32,
    pub moves_analyzed: i32,
}

#[tauri::command]
pub async fn get_recent_accuracy(
    state: State<'_, AppState>,
    participant: String,
    source: Option<String>,
    limit: Option<i64>,
) -> Result<Vec<AccuracyRow>, AppError> {
    let lim = limit.unwrap_or(30).clamp(1, 200);
    let rows: Vec<(String, i64, String, String, f32, f32, i32)> = sqlx::query_as(
        "SELECT source, ref_id, participant, color, accuracy, acpl, moves_analyzed
         FROM analytics.accuracy_results
         WHERE participant = $1
           AND ($2::text IS NULL OR source = $2)
         ORDER BY computed_at DESC
         LIMIT $3",
    )
    .bind(&participant)
    .bind(source)
    .bind(lim)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(
            |(source, ref_id, participant, color, accuracy, acpl, moves)| AccuracyRow {
                source,
                ref_id,
                participant,
                color,
                accuracy,
                acpl,
                moves_analyzed: moves,
            },
        )
        .collect())
}

#[derive(Serialize)]
pub struct ParticipantSummary {
    pub participant: String,
    pub participant_type: String,
    pub source: String,
    pub games: i64,
    pub avg_accuracy: f32,
    pub avg_acpl: f32,
    pub best_accuracy: f32,
    pub best_acpl: f32,
}

#[tauri::command]
pub async fn get_participant_summaries(
    state: State<'_, AppState>,
) -> Result<Vec<ParticipantSummary>, AppError> {
    let rows: Vec<(String, String, String, i64, f32, f32, f32, f32)> = sqlx::query_as(
        "SELECT participant, participant_type, source, games,
                avg_accuracy, avg_acpl, best_accuracy, best_acpl
         FROM analytics.accuracy_rankings
         ORDER BY avg_accuracy DESC
         LIMIT 50",
    )
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(
            |(p, pt, s, g, aa, ac, ba, bc)| ParticipantSummary {
                participant: p,
                participant_type: pt,
                source: s,
                games: g,
                avg_accuracy: aa,
                avg_acpl: ac,
                best_accuracy: ba,
                best_acpl: bc,
            },
        )
        .collect())
}

#[tauri::command]
pub async fn get_gambit_matchup(
    state: State<'_, AppState>,
    participant: String,
    opponent: String,
) -> Result<Vec<GambitRow>, AppError> {
    let rows: Vec<(String, String, i64, i64, i64, i64, f32)> = sqlx::query_as(
        "SELECT gambit_id, gambit_name, games, wins, losses, draws, score
         FROM analytics.gambit_matchup_stats
         WHERE participant = $1 AND opponent = $2
         ORDER BY games DESC",
    )
    .bind(&participant)
    .bind(&opponent)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(id, name, g, w, l, d, s)| GambitRow {
            gambit_id: id,
            gambit_name: name,
            side: "".into(),
            soundness: None,
            games: g,
            wins: w,
            losses: l,
            draws: d,
            score: s,
        })
        .collect())
}
