//! Opening study and puzzle training commands.
//!
//! SM-2 scheduling must match training/spaced_repetition.py bit-for-bit
//! so a line reviewed from the CLI keeps its schedule when reviewed in
//! the app and vice versa.

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct OpeningLine {
    pub line_id: String,
    pub eco_code: String,
    pub name: String,
    pub moves_san: Vec<String>,
    pub fens: Vec<String>,
    pub side: String,
}

#[tauri::command]
pub async fn get_due_opening_lines(
    state: State<'_, AppState>,
    limit: Option<i64>,
) -> Result<Vec<OpeningLine>, AppError> {
    let lim = limit.unwrap_or(10).clamp(1, 100);
    let rows: Vec<(String, String, String, Vec<String>, Vec<String>, String)> =
        sqlx::query_as(
            "SELECT l.line_id, l.eco_code, l.name, l.moves_san::text[], l.fens::text[], l.side
             FROM app.opening_lines l
             LEFT JOIN app.opening_study s ON s.line_id = l.line_id
             WHERE s.due_at IS NULL OR s.due_at <= NOW()
             ORDER BY s.due_at NULLS FIRST
             LIMIT $1",
        )
        .bind(lim)
        .fetch_all(&state.pool)
        .await?;

    Ok(rows
        .into_iter()
        .map(|(id, eco, name, ms, fns, side)| OpeningLine {
            line_id: id,
            eco_code: eco,
            name,
            moves_san: ms,
            fens: fns,
            side,
        })
        .collect())
}

#[derive(Deserialize)]
pub struct OpeningAttempt {
    pub line_id: String,
    pub mode: String,
    pub correct_moves: i32,
    pub total_moves: i32,
    pub time_ms: Option<i32>,
}

#[tauri::command]
pub async fn record_opening_attempt(
    state: State<'_, AppState>,
    a: OpeningAttempt,
) -> Result<(), AppError> {
    if a.total_moves < 0 || a.correct_moves < 0 || a.correct_moves > a.total_moves {
        return Err(AppError::InvalidInput("invalid move counts".into()));
    }
    let quality = if a.total_moves == 0 {
        0
    } else {
        ((a.correct_moves as f32 / a.total_moves as f32) * 5.0).round() as i32
    };
    let passed = quality >= 3;

    sqlx::query(
        "INSERT INTO app.opening_attempts
         (line_id, mode, correct_moves, total_moves, time_ms, passed)
         VALUES ($1,$2,$3,$4,$5,$6)",
    )
    .bind(&a.line_id)
    .bind(&a.mode)
    .bind(a.correct_moves)
    .bind(a.total_moves)
    .bind(a.time_ms)
    .bind(passed)
    .execute(&state.pool)
    .await?;

    // SM-2 update — must match Python's update_sm2 exactly.
    let cur: Option<(f32, i32, i32)> = sqlx::query_as(
        "SELECT ef, interval_days, repetitions FROM app.opening_study WHERE line_id=$1",
    )
    .bind(&a.line_id)
    .fetch_optional(&state.pool)
    .await?;
    let (ef, interval, reps) = cur.unwrap_or((2.5, 1, 0));
    let (nef, nint, nreps) = sm2(ef, interval, reps, quality);

    sqlx::query(
        "INSERT INTO app.opening_study
         (line_id, ef, interval_days, repetitions, due_at, last_reviewed, mastery_score)
         VALUES ($1,$2,$3,$4, NOW() + ($3 || ' days')::interval, NOW(), LEAST(1.0, $4::real / 10.0))
         ON CONFLICT (line_id) DO UPDATE SET
            ef=$2, interval_days=$3, repetitions=$4,
            due_at=NOW() + ($3 || ' days')::interval,
            last_reviewed=NOW(),
            mastery_score=LEAST(1.0, $4::real / 10.0)",
    )
    .bind(&a.line_id)
    .bind(nef)
    .bind(nint)
    .bind(nreps)
    .execute(&state.pool)
    .await?;
    Ok(())
}

/// SM-2 — must match training/spaced_repetition.py::update_sm2.
fn sm2(ef: f32, interval: i32, reps: i32, quality: i32) -> (f32, i32, i32) {
    let q = quality.clamp(0, 5);
    if q < 3 {
        return (ef, 1, 0);
    }
    let nef = (ef + (0.1 - (5 - q) as f32 * (0.08 + (5 - q) as f32 * 0.02))).max(1.3);
    let nint = match reps {
        0 => 1,
        1 => 6,
        _ => ((interval as f32) * nef).round() as i32,
    };
    (nef, nint.max(1), reps + 1)
}

#[derive(Serialize)]
pub struct Puzzle {
    pub puzzle_id: String,
    pub fen: String,
    pub solution_uci: String,
    pub rating: Option<i32>,
    pub themes: Vec<String>,
    pub source: String,
    pub eco_code: Option<String>,
    pub phase_flag: Option<String>,
}

#[tauri::command]
pub async fn get_due_puzzles(
    state: State<'_, AppState>,
    limit: Option<i64>,
    source: Option<String>,
    phase: Option<String>,
    theme: Option<String>,
) -> Result<Vec<Puzzle>, AppError> {
    let lim = limit.unwrap_or(10).clamp(1, 100);
    let rows: Vec<(String, String, String, Option<i32>, Vec<String>, String, Option<String>, Option<String>)> =
        sqlx::query_as(
            "SELECT p.puzzle_id, p.fen, p.solution_uci, p.rating, p.themes::text[],
                    p.source, p.eco_code, p.phase_flag
             FROM app.puzzles p
             LEFT JOIN app.puzzle_sr s ON s.puzzle_id = p.puzzle_id
             WHERE (s.due_at IS NULL OR s.due_at <= NOW())
               AND ($1::text IS NULL OR p.source = $1)
               AND ($2::text IS NULL OR p.phase_flag = $2)
               AND ($3::text IS NULL OR $3 = ANY(p.themes))
             ORDER BY s.due_at NULLS FIRST, p.rating
             LIMIT $4"
        ).bind(source).bind(phase).bind(theme).bind(lim)
        .fetch_all(&state.pool).await?;
    // ... (same mapping as before)
    Ok(rows.into_iter().map(|(id, fen, sol, rt, th, src, eco, ph)| Puzzle {
        puzzle_id: id, fen, solution_uci: sol, rating: rt, themes: th,
        source: src, eco_code: eco, phase_flag: ph,
    }).collect())
}

#[tauri::command]
pub async fn get_puzzle_themes(
    state: State<'_, AppState>,
) -> Result<Vec<(String, i64)>, AppError> {
    let rows: Vec<(String, i64)> = sqlx::query_as(
        "SELECT theme, puzzle_count FROM app.available_puzzle_themes
         ORDER BY puzzle_count DESC LIMIT 50"
    ).fetch_all(&state.pool).await?;
    Ok(rows)
}

#[derive(Deserialize)]
pub struct PuzzleAttempt {
    pub puzzle_id: String,
    pub correct: bool,
    pub hints_used: Option<i32>,
    pub time_ms: Option<i32>,
}

#[tauri::command]
pub async fn record_puzzle_attempt(
    state: State<'_, AppState>,
    a: PuzzleAttempt,
) -> Result<(), AppError> {
    let quality = if a.correct {
        4 - a.hints_used.unwrap_or(0)
    } else {
        1
    };

    sqlx::query(
        "INSERT INTO app.puzzle_attempts (puzzle_id, correct, hints_used, time_ms)
         VALUES ($1,$2,$3,$4)",
    )
    .bind(&a.puzzle_id)
    .bind(a.correct)
    .bind(a.hints_used)
    .bind(a.time_ms)
    .execute(&state.pool)
    .await?;

    let cur: Option<(f32, i32, i32)> = sqlx::query_as(
        "SELECT ef, interval_days, repetitions FROM app.puzzle_sr WHERE puzzle_id=$1",
    )
    .bind(&a.puzzle_id)
    .fetch_optional(&state.pool)
    .await?;
    let (ef, interval, reps) = cur.unwrap_or((2.5, 1, 0));
    let (nef, nint, nreps) = sm2(ef, interval, reps, quality.max(0));

    sqlx::query(
        "INSERT INTO app.puzzle_sr
         (puzzle_id, ef, interval_days, repetitions, due_at, last_reviewed)
         VALUES ($1,$2,$3,$4, NOW() + ($3 || ' days')::interval, NOW())
         ON CONFLICT (puzzle_id) DO UPDATE SET
            ef=$2, interval_days=$3, repetitions=$4,
            due_at=NOW() + ($3 || ' days')::interval,
            last_reviewed=NOW()",
    )
    .bind(&a.puzzle_id)
    .bind(nef)
    .bind(nint)
    .bind(nreps)
    .execute(&state.pool)
    .await?;
    Ok(())
}

#[tauri::command]
pub async fn import_study_pgn(
    _state: State<'_, AppState>,
    pgn_path: String,
    study_name: String,
) -> Result<i64, AppError> {
    // Delegate to the Python ingest script — PGN parsing lives in one
    // place, avoiding a second implementation in Rust.
    let output = std::process::Command::new("python")
        .args(["-m", "ingest.study_pgn", &pgn_path, "--study-name", &study_name])
        .output()
        .map_err(|e| AppError::Internal(e.to_string()))?;
    if !output.status.success() {
        return Err(AppError::Internal(
            String::from_utf8_lossy(&output.stderr).to_string(),
        ));
    }
    Ok(0)
}
