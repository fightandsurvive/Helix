//! Eval graph data — returns per-ply eval points + user-move markers.

use serde::Serialize;
use tauri::State;
use crate::db::AppState;
use crate::errors::AppError;

#[derive(Serialize)]
pub struct EvalPoint {
    pub ply: i32,
    pub move_san: String,
    pub fen: String,
    pub white_eval_cp: i32,
    pub played_by_user: bool,
    pub user_loss_cp: Option<i32>,
}

#[tauri::command]
pub async fn get_eval_curve(
    state: State<'_, AppState>,
    game_id: i64,
) -> Result<Vec<EvalPoint>, AppError> {
    let rows: Vec<(i32, String, String, i32, bool, Option<i32>)> = sqlx::query_as(
        "SELECT ply_number, move_san, fen_after, white_eval_cp, played_by_user, user_loss_cp
         FROM analytics.game_eval_curve WHERE game_id = $1 ORDER BY ply_number"
    )
    .bind(game_id)
    .fetch_all(&state.pool)
    .await?;

    Ok(rows
        .into_iter()
        .map(|(ply, san, fen, eval, user, loss)| EvalPoint {
            ply, move_san: san, fen, white_eval_cp: eval,
            played_by_user: user, user_loss_cp: loss,
        })
        .collect())
}
