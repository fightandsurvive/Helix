//! Opening analytics — position win rates broken down by legend.

use serde::Serialize;
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct OpeningNode {
    pub mv: String,
    pub fen: String,
    pub games: i64,
    pub white_score: f32,
    pub by_legend: Vec<LegendRow>,
}

#[derive(Serialize)]
pub struct LegendRow {
    pub legend: String,
    pub games: i64,
    pub score: f32,
}

#[tauri::command]
pub async fn explore_opening(
    state: State<'_, AppState>,
    fen: String,
    min_games: Option<i64>,
) -> Result<Vec<OpeningNode>, AppError> {
    let min = min_games.unwrap_or(20).max(1);
    let rows: Vec<(String, String, i64, f32)> = sqlx::query_as(
        "SELECT m.move_notation, m.fen_string, ps.total_games, ps.white_score
         FROM gm.moves_general m
         JOIN gm.position_stats ps ON ps.fen_md5 = m.fen_md5
         WHERE m.fen_md5 = md5($1)::uuid AND ps.total_games >= $2
         ORDER BY ps.white_score DESC LIMIT 12",
    )
    .bind(&fen)
    .bind(min)
    .fetch_all(&state.pool)
    .await?;

    let mut out = Vec::with_capacity(rows.len());
    for (mv, child_fen, games, score) in rows {
        let by_legend: Vec<(String, i64, f32)> = sqlx::query_as(
            "SELECT legend_tag, games, white_score
             FROM gm.position_stats_by_legend
             WHERE fen_md5 = md5($1)::uuid",
        )
        .bind(&child_fen)
        .fetch_all(&state.pool)
        .await
        .unwrap_or_default();

        out.push(OpeningNode {
            mv,
            fen: child_fen,
            games,
            white_score: score,
            by_legend: by_legend
                .into_iter()
                .map(|(l, g, s)| LegendRow { legend: l, games: g, score: s })
                .collect(),
        });
    }
    Ok(out)
}

#[tauri::command]
pub async fn best_openings_vs(
    state: State<'_, AppState>,
    eco: String,
    _engine: Option<String>,
) -> Result<Vec<OpeningNode>, AppError> {
    let row: Option<(String,)> = sqlx::query_as("SELECT pgn FROM gm.eco_dict WHERE eco_code=$1")
        .bind(&eco)
        .fetch_optional(&state.pool)
        .await?;
    let pgn = row.map(|r| r.0).unwrap_or_default();
    if pgn.is_empty() {
        return Ok(Vec::new());
    }

    // Replay the ECO line to get the resulting FEN, then explore.
    // We don't have a Rust chess crate in the deps, so use position_stats
    // to find the FEN: query the fen_string alongside eco_code.
    let fen_row: Option<(String,)> = sqlx::query_as(
        "SELECT MIN(fen_string) FROM gm.moves_general m
         JOIN gm.games g ON g.game_id=m.game_id AND g.legend_tag=m.legend_tag
         WHERE g.eco_code=$1 LIMIT 1",
    )
    .bind(&eco)
    .fetch_optional(&state.pool)
    .await?;

    match fen_row {
        Some((fen,)) => explore_opening(state, fen, Some(50)).await,
        None => Ok(Vec::new()),
    }
}

#[derive(Serialize)]
pub struct PlayerOpening {
    pub eco_code: String,
    pub games: i64,
    pub white_score: f32,
}

#[tauri::command]
pub async fn spy_player_openings(
    state: State<'_, AppState>,
    player: String,
) -> Result<Vec<PlayerOpening>, AppError> {
    let pattern = format!("%{}%", player);
    let rows: Vec<(String, i64, f32)> = sqlx::query_as(
        "SELECT eco_code, COUNT(*)::BIGINT,
                AVG(CASE result WHEN '1-0' THEN 1.0
                                WHEN '1/2-1/2' THEN 0.5
                                ELSE 0.0 END)::REAL
         FROM gm.games_general
         WHERE white_player ILIKE $1 AND eco_code IS NOT NULL
         GROUP BY eco_code
         HAVING COUNT(*) >= 3
         ORDER BY COUNT(*) DESC LIMIT 20",
    )
    .bind(&pattern)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(eco, games, score)| PlayerOpening {
            eco_code: eco,
            games,
            white_score: score,
        })
        .collect())
}
