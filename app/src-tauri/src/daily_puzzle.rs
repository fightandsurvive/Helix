//! Daily puzzle — deterministic by date, weighted to user's rating.

use serde::Serialize;
use chrono::Utc;
use tauri::State;
use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct DailyPuzzle {
    pub puzzle_id: String,
    pub fen: String,
    pub solution_uci: String,
    pub rating: Option<i32>,
    pub themes: Vec<String>,
    pub already_played_today: bool,
    pub current_streak: i32,
    pub longest_streak: i32,
    pub total_solved: i32,
    pub total_played: i32,
}

#[tauri::command]
pub async fn get_daily_puzzle(
    state: State<'_, AppState>,
    profile_id: i64,
) -> Result<Option<DailyPuzzle>, AppError> {
    let today = Utc::now().format("%Y-%m-%d").to_string();

    // Deterministic puzzle per day, weighted to recent rating
    let puzzle: Option<(String, String, String, Option<i32>, Vec<String>)> = sqlx::query_as(
        "WITH user_rating AS (
           SELECT AVG(user_rating)::INT AS r FROM (
             SELECT user_rating FROM app.personal_games
             WHERE profile_id = $1 AND user_rating IS NOT NULL
             ORDER BY played_at DESC LIMIT 20
           ) recent
         )
         SELECT p.puzzle_id, p.fen, p.solution_uci, p.rating, p.themes::text[]
         FROM app.puzzles p, user_rating
         WHERE p.source = 'lichess'
           AND p.rating BETWEEN COALESCE(user_rating.r, 1500) - 250
                            AND COALESCE(user_rating.r, 1500) + 250
         ORDER BY md5(p.puzzle_id || $2) LIMIT 1"
    )
    .bind(profile_id).bind(&today)
    .fetch_optional(&state.pool).await?;

    let Some((pid, fen, sol, rating, themes)) = puzzle else { return Ok(None); };

    // Load state
    let streak_row: Option<(i32, i32, Option<chrono::NaiveDate>, i32, i32)> = sqlx::query_as(
        "SELECT current_streak, longest_streak, last_played_date, total_played, total_solved
         FROM app.daily_puzzle_state WHERE profile_id = $1"
    ).bind(profile_id).fetch_optional(&state.pool).await?;

    let (cur, longest, last, total_p, total_s) = streak_row.unwrap_or((0, 0, None, 0, 0));
    let already = last == Some(Utc::now().date_naive());

    Ok(Some(DailyPuzzle {
        puzzle_id: pid, fen, solution_uci: sol, rating, themes,
        already_played_today: already,
        current_streak: cur, longest_streak: longest,
        total_solved: total_s, total_played: total_p,
    }))
}

#[tauri::command]
pub async fn record_daily_attempt(
    state: State<'_, AppState>,
    profile_id: i64,
    solved: bool,
) -> Result<(i32, i32), AppError> {
    let today = Utc::now().date_naive();

    let row: (i32, i32, Option<chrono::NaiveDate>, i32, i32) = sqlx::query_as(
        "INSERT INTO app.daily_puzzle_state
            (profile_id, current_streak, longest_streak, last_played_date, total_played, total_solved)
         VALUES ($1, $2, $2, $3, 1, $4)
         ON CONFLICT (profile_id) DO UPDATE SET
            current_streak = CASE
              WHEN app.daily_puzzle_state.last_played_date = $3 THEN app.daily_puzzle_state.current_streak
              WHEN app.daily_puzzle_state.last_played_date = $3 - INTERVAL '1 day'
                   THEN app.daily_puzzle_state.current_streak + $2
              ELSE $2
            END,
            longest_streak = GREATEST(app.daily_puzzle_state.longest_streak,
              CASE
                WHEN app.daily_puzzle_state.last_played_date = $3 THEN app.daily_puzzle_state.current_streak
                WHEN app.daily_puzzle_state.last_played_date = $3 - INTERVAL '1 day'
                     THEN app.daily_puzzle_state.current_streak + $2
                ELSE $2
              END),
            last_played_date = $3,
            total_played = app.daily_puzzle_state.total_played + 1,
            total_solved = app.daily_puzzle_state.total_solved + $4,
            updated_at = NOW()
         RETURNING current_streak, longest_streak, last_played_date, total_played, total_solved"
    )
    .bind(profile_id).bind(if solved { 1 } else { 0 }).bind(today).bind(if solved { 1 } else { 0 })
    .fetch_one(&state.pool).await?;

    Ok((row.0, row.1))
}
