//! Local play vs Stockfish.
//!
//! Persistent SF subprocess with mutex-guarded UCI communication. Each
//! game keeps its own skill level + time control; on game end, the PGN
//! is saved to `app.personal_games` (platform='helix') and an accuracy
//! job is queued.

use once_cell::sync::Lazy;
use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use tauri::State;

use crate::db::AppState;
use crate::errors::AppError;

struct SFHandle {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    skill: u32,
}

static SF: Lazy<Mutex<Option<SFHandle>>> = Lazy::new(|| Mutex::new(None));

fn get_or_spawn_sf(sf_path: &str, skill: u32) -> Result<(), String> {
    let mut guard = SF.lock().map_err(|_| "mutex poisoned")?;
    if guard.is_none() {
        let mut child = Command::new(sf_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("spawn SF: {e}"))?;
        let stdin = child.stdin.take().ok_or("no stdin")?;
        let stdout = BufReader::new(child.stdout.take().ok_or("no stdout")?);
        *guard = Some(SFHandle { child, stdin, stdout, skill: 255 });
    }
    let h = guard.as_mut().unwrap();

    // Configure skill level if changed
    if h.skill != skill {
        writeln!(h.stdin, "setoption name Skill Level value {skill}").map_err(|e| e.to_string())?;
        h.skill = skill;
    }
    writeln!(h.stdin, "ucinewgame").map_err(|e| e.to_string())?;
    writeln!(h.stdin, "isready").map_err(|e| e.to_string())?;
    h.stdin.flush().map_err(|e| e.to_string())?;

    // Drain until readyok
    loop {
        let mut line = String::new();
        h.stdout.read_line(&mut line).map_err(|e| e.to_string())?;
        if line.trim() == "readyok" {
            break;
        }
    }
    Ok(())
}

#[derive(Deserialize)]
pub struct PlayRequest {
    pub fen: String,
    pub moves: Vec<String>,       // full move history from start
    pub skill_level: u32,          // 1-20
    pub movetime_ms: u32,          // how long SF should think
}

#[derive(Serialize)]
pub struct PlayResponse {
    pub best_move_uci: String,
    pub best_move_san: String,
    pub eval_cp: i32,
}

#[tauri::command]
pub async fn sf_play_move(
    _state: State<'_, AppState>,
    req: PlayRequest,
    sf_path: String,
) -> Result<PlayResponse, AppError> {
    // Validate
    if req.skill_level < 1 || req.skill_level > 20 {
        return Err(AppError::InvalidInput("skill_level must be 1-20".into()));
    }
    if req.movetime_ms < 100 || req.movetime_ms > 60000 {
        return Err(AppError::InvalidInput("movetime_ms must be 100-60000".into()));
    }

    let result = tokio::task::spawn_blocking(move || -> Result<PlayResponse, String> {
        get_or_spawn_sf(&sf_path, req.skill_level)?;

        let mut guard = SF.lock().map_err(|_| "mutex poisoned")?;
        let h = guard.as_mut().ok_or("SF not initialized")?;

        // Set position
        let mut pos_cmd = String::from("position fen ");
        pos_cmd.push_str(&req.fen);
        if !req.moves.is_empty() {
            pos_cmd.push_str(" moves ");
            pos_cmd.push_str(&req.moves.join(" "));
        }
        writeln!(h.stdin, "{}", pos_cmd).map_err(|e| e.to_string())?;
        writeln!(h.stdin, "go movetime {}", req.movetime_ms).map_err(|e| e.to_string())?;
        h.stdin.flush().map_err(|e| e.to_string())?;

        // Read until bestmove
        let mut best_uci = String::new();
        let mut last_eval = 0i32;
        loop {
            let mut line = String::new();
            h.stdout.read_line(&mut line).map_err(|e| e.to_string())?;
            let trimmed = line.trim();
            if trimmed.starts_with("info ") && trimmed.contains(" score ") && trimmed.contains(" pv ") {
                // Parse last score cp X or score mate Y
                let parts: Vec<&str> = trimmed.split_whitespace().collect();
                for i in 0..parts.len() {
                    if parts[i] == "cp" && i + 1 < parts.len() {
                        if let Ok(v) = parts[i + 1].parse::<i32>() {
                            last_eval = v;
                        }
                    } else if parts[i] == "mate" && i + 1 < parts.len() {
                        if let Ok(m) = parts[i + 1].parse::<i32>() {
                            last_eval = if m > 0 { 10000 } else { -10000 };
                        }
                    }
                }
            } else if trimmed.starts_with("bestmove ") {
                best_uci = trimmed.split_whitespace().nth(1).unwrap_or("").to_string();
                break;
            }
        }

        Ok(PlayResponse {
            best_move_uci: best_uci.clone(),
            best_move_san: String::new(), // computed on frontend
            eval_cp: last_eval,
        })
    })
    .await
    .map_err(|e| AppError::Internal(e.to_string()))?
    .map_err(AppError::Internal)?;

    Ok(result)
}

#[derive(Deserialize)]
pub struct SaveGameRequest {
    pub profile_id: i64,
    pub pgn: String,
    pub white_player: String,
    pub black_player: String,
    pub user_color: String,
    pub result: String,
    pub user_rating: Option<i32>,
    pub opponent_rating: Option<i32>,
    pub time_control: Option<String>,
}

#[tauri::command]
pub async fn save_played_game(
    state: State<'_, AppState>,
    req: SaveGameRequest,
) -> Result<i64, AppError> {
    let external_id = format!("helix_{}", uuid::Uuid::new_v4());

    let row: (i64,) = sqlx::query_as(
        "INSERT INTO app.personal_games
            (profile_id, platform, external_id, played_at, white_player, black_player,
             user_color, user_rating, opponent_rating, result, time_control, pgn)
         VALUES ($1, 'helix', $2, NOW(), $3, $4, $5, $6, $7, $8, $9, $10)
         RETURNING game_id"
    )
    .bind(req.profile_id)
    .bind(&external_id)
    .bind(&req.white_player)
    .bind(&req.black_player)
    .bind(&req.user_color)
    .bind(req.user_rating)
    .bind(req.opponent_rating)
    .bind(&req.result)
    .bind(&req.time_control)
    .bind(&req.pgn)
    .fetch_one(&state.pool)
    .await?;

    // Queue accuracy job
    sqlx::query(
        "INSERT INTO analytics.accuracy_jobs (source, ref_id, priority)
         VALUES ('personal', $1, 5) ON CONFLICT DO NOTHING"
    )
    .bind(row.0)
    .execute(&state.pool)
    .await?;

    Ok(row.0)
}
